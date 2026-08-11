import collections
from functools import partial

from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets
from qtpy.QtCore import Qt
from qtpy.QtCore import Signal

from .. import cmds
from .. import hotkeys
from .. import qtutils
from ..i18n import N_
from ..models import dag
from .standard import TreeWidget


def gather_files(context, key):
    """Return the paths changed by a commit or commit range

    ``key`` is the selection key built by ``FileWidget.commits_selected``:
    ``('oid', oid)`` for a single commit or ``('range', start, end)`` for a
    range. Runs on a background thread, so it only talks to git.
    """
    git = context.git
    paths = []

    if key[0] == 'range':
        # Get a list of changed files for a commit range.
        _, start_oid, end = key
        start = start_oid + '~'
        if end == dag.STAGE:
            status, out, _ = git.diff(
                start, cached=True, z=True, numstat=True, no_renames=True
            )
        elif end == dag.WORKTREE:
            if start_oid == dag.STAGE:
                status, out, _ = git.diff(z=True, numstat=True, no_renames=True)
            else:
                status, out, _ = git.diff(start, z=True, numstat=True, no_renames=True)
        else:
            status, out, _ = git.diff(start, end, z=True, numstat=True, no_renames=True)
        if status == 0:
            paths = [f for f in out.rstrip('\0').split('\0') if f]
    else:
        # Get the list of changed files in a single commit.
        _, oid = key
        # NOTE: The output from "git diff-files --numstat -z" is not equivalent
        # to the output of "git show --numstat -z". "git diff-files" does not
        # emit a NULL separator between each entry. That's why we use the
        # default output (without "-z") and split on newline instead.
        # This is also true for "git diff-index" as well.
        if oid == dag.STAGE:
            status, out, _ = git.diff_index(
                'HEAD', cached=True, numstat=True, _readonly=True
            )
            if status == 0:
                paths = [f for f in out.split('\n') if f]
        elif oid == dag.WORKTREE:
            status, out, _ = git.diff_files(numstat=True, _readonly=True)
            if status == 0:
                paths = [f for f in out.split('\n') if f]
        else:
            status, out, _ = git.show(
                oid,
                format='',
                numstat=True,
                no_renames=True,
                z=True,
                _readonly=True,
            )
            if status == 0:
                paths = [f for f in out.rstrip('\0').split('\0') if f]

    return paths


class FileWidget(TreeWidget):
    files_selected = Signal(object)
    difftool_selected = Signal(object)
    histories_selected = Signal(object)
    grab_file = Signal(object)
    grab_file_from_parent = Signal(object)
    select_line_range_for_file = Signal(object)
    remark_toggled = Signal(object, object)

    # Delay before a selected commit's file list is loaded. Holding an arrow
    # key in the DAG steps through commits faster than this, so intermediate
    # commits never spawn a git process; only the commit we land on does.
    FILES_DEBOUNCE_MSEC = 100

    # Number of file lists kept in the most-recently-used cache. Revisiting a
    # commit (common while arrow-stepping the DAG) then renders instantly.
    _FILES_CACHE_MAX = 50

    def __init__(self, context, parent, remarks=False):
        TreeWidget.__init__(self, parent)
        self.context = context
        # Debounce + staleness token + MRU cache, mirroring CommitDiffWidget:
        # the file list loads in the background once the selection settles,
        # superseded results are dropped, and revisits skip git entirely.
        # Volatile pseudo-commits (WORKTREE/STAGE) are never cached and the
        # cache is dropped whenever the DAG reloads (see clear_files_cache).
        self._pending_selection = None
        self._files_token = 0
        self._files_cache = collections.OrderedDict()
        self._files_timer = QtCore.QTimer(self)
        self._files_timer.setSingleShot(True)
        self._files_timer.setInterval(self.FILES_DEBOUNCE_MSEC)
        self._files_timer.timeout.connect(self._load_pending_files)
        self._columns_initialized = False
        # Guards _resize_columns() from mistaking its own section changes for a
        # manual drag, and records once the user has taken control of the widths.
        self._auto_resizing = False
        self._user_adjusted_columns = False
        self.setHeaderLabels([N_('Filename'), '+', '-'])
        header = self.header()
        # The Filename column absorbs the free space; without this the last
        # (narrow "-") column would stretch instead. Disabling it also keeps
        # widget resizes from firing sectionResized and looking like a drag.
        header.setStretchLastSection(False)
        header.sectionResized.connect(self._section_resized)

        self.show_history_action = qtutils.add_action(
            self, N_('Show History'), self.show_history, hotkeys.HISTORY
        )
        self.launch_difftool_action = qtutils.add_action(
            self, N_('Launch Diff Tool'), self.show_diff
        )
        self.launch_editor_action = qtutils.add_action(
            self, N_('Launch Editor'), self.edit_paths, hotkeys.EDIT
        )
        self.grab_file_action = qtutils.add_action(
            self, N_('Grab File...'), self._grab_file
        )
        self.grab_file_from_parent_action = qtutils.add_action(
            self, N_('Grab File from Parent Commit...'), self._grab_file_from_parent
        )
        self.select_line_range_action = qtutils.add_action(
            self, N_('Trace Evolution of Line Range...'), self._select_line_range
        )
        if remarks:
            self.toggle_remark_actions = tuple(
                qtutils.add_action(
                    self,
                    r,
                    lambda remark=r: self.toggle_remark(remark),
                    hotkeys.hotkey(Qt.CTRL | getattr(Qt, 'Key_' + r)),
                )
                for r in map(str, range(10))
            )
        else:
            self.toggle_remark_actions = ()

        self.itemSelectionChanged.connect(self.selection_changed)

    def selection_changed(self):
        items = self.selected_items()
        self.files_selected.emit([i.path for i in items])

    def commits_selected(self, commits):
        if not commits:
            self._files_timer.stop()
            self._pending_selection = None
            # Invalidate any in-flight load so a late result cannot repopulate
            # the cleared list.
            self._files_token += 1
            self.clear()
            return

        if len(commits) > 1:
            key = ('range', commits[0].oid, commits[-1].oid)
        else:
            key = ('oid', commits[0].oid)
        self._pending_selection = key
        # (Re)start the debounce; the files load once the selection settles.
        self._files_timer.start()

    def _files_key_cacheable(self, key):
        """Return True when a selection key is safe to cache

        The WORKTREE and STAGE pseudo-commits are volatile, so any file list
        that involves them is never cached.
        """
        return not any(part in (dag.WORKTREE, dag.STAGE) for part in key)

    def _load_pending_files(self):
        """Load the file list for the most recently selected commit(s)"""
        key = self._pending_selection
        if key is None:
            return
        self._pending_selection = None
        # Stamp the load so that a result arriving after the selection has
        # already moved on is discarded in _files_ready().
        self._files_token += 1
        if self._files_key_cacheable(key):
            cached = self._files_cache.get(key)
            if cached is not None:
                self._files_cache.move_to_end(key)
                self.list_files(cached)
                return
        task = qtutils.SimpleTask(gather_files, self.context, key)
        self.context.runtask.start(
            task, result=partial(self._files_ready, self._files_token, key)
        )

    def _files_ready(self, token, key, paths):
        """Apply a background result unless the selection has moved on"""
        if token != self._files_token:
            return
        if self._files_key_cacheable(key):
            self._files_cache[key] = paths
            self._files_cache.move_to_end(key)
            while len(self._files_cache) > self._FILES_CACHE_MAX:
                self._files_cache.popitem(last=False)
        self.list_files(paths)

    def clear_files_cache(self):
        """Drop cached file lists, e.g. when the DAG reloads its commits"""
        self._files_cache.clear()
        self._files_token += 1

    def list_files(self, files_log):
        self.clear()
        if not files_log:
            return
        files = []
        for filename in files_log:
            item = FileTreeWidgetItem(filename)
            files.append(item)
        self.insertTopLevelItems(0, files)

    def _section_resized(self, _index, _old, _new):
        """Record a manual column resize so we stop overriding it"""
        if not self._auto_resizing:
            self._user_adjusted_columns = True

    def _resize_columns(self):
        """Set columns to their initial size"""
        header_width = self.header().width() - 1
        metrics = QtGui.QFontMetrics(self.font())
        numbers_max = qtutils.fontmetrics_width(metrics, '12345678')  # Linux had 28,000,000+ LOC of code in 2020.
        numbers_width = min(numbers_max, header_width // 8 - 1)
        files_width = header_width - numbers_width * 2
        self._auto_resizing = True
        try:
            self.setColumnWidth(0, files_width)
            self.setColumnWidth(1, numbers_width)
            self.setColumnWidth(2, numbers_width)
        finally:
            self._auto_resizing = False

    def showEvent(self, event):
        """Defer initializaztion of column widths"""
        super().showEvent(event)
        if not self._columns_initialized:
            self._columns_initialized = True
            self._resize_columns()

    def resizeEvent(self, event):
        """Grow the Filename column with the widget until the user resizes one"""
        super().resizeEvent(event)
        # Once the user has dragged a column, keep their widths instead of
        # snapping back to the computed layout on every resize.
        if not self._user_adjusted_columns:
            self._resize_columns()

    def contextMenuEvent(self, event):
        menu = qtutils.create_menu(N_('Actions'), self)
        menu.addAction(self.select_line_range_action)
        menu.addSeparator()
        menu.addAction(self.grab_file_action)
        menu.addAction(self.grab_file_from_parent_action)
        menu.addAction(self.show_history_action)
        menu.addAction(self.launch_difftool_action)
        menu.addAction(self.launch_editor_action)
        if self.toggle_remark_actions:
            menu_toggle_remark = menu.addMenu(N_('Toggle remark of touching commits'))
            tuple(map(menu_toggle_remark.addAction, self.toggle_remark_actions))
        menu.exec_(self.mapToGlobal(event.pos()))

    def show_diff(self):
        self.difftool_selected.emit(self.selected_paths())

    def _grab_file(self):
        for path in self.selected_paths():
            self.grab_file.emit(path)

    def _grab_file_from_parent(self):
        for path in self.selected_paths():
            self.grab_file_from_parent.emit(path)

    def _select_line_range(self):
        """Emit a signal so that we can select the line range for the selected file"""
        paths = self.selected_paths()
        if paths:
            self.select_line_range_for_file.emit(paths[0])

    def selected_paths(self):
        return [i.path for i in self.selected_items()]

    def edit_paths(self):
        cmds.do(cmds.Edit, self.context, self.selected_paths())

    def show_history(self):
        items = self.selected_items()
        paths = [i.path for i in items]
        self.histories_selected.emit(paths)

    def toggle_remark(self, remark):
        items = self.selected_items()
        paths = tuple(i.path for i in items)
        self.remark_toggled.emit(remark, paths)


class FileTreeWidgetItem(QtWidgets.QTreeWidgetItem):
    def __init__(self, file_log, parent=None):
        QtWidgets.QTreeWidgetItem.__init__(self, parent)
        texts = file_log.split('\t')
        self.path = path = texts[2]
        self.setText(0, path)
        self.setText(1, texts[0])
        self.setText(2, texts[1])
