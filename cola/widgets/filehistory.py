"""A per-file history browser with a diff preview"""
from qtpy import QtCore
from qtpy import QtWidgets
from qtpy.QtCore import Qt

from .. import cmds
from .. import difftool
from .. import hotkeys
from .. import qtutils
from ..i18n import N_
from ..models import filehistory
from ..models import prefs
from . import browse
from . import defs
from . import diff
from . import standard


def file_history(context, path, ref=None, show=True):
    """Create a File History window for a path"""
    view = FileHistoryWindow(context, path, ref=ref)
    context.file_history_windows.append(view)
    view.closed.connect(context.file_history_windows.remove)
    if show:
        view.show()
    view.load()
    return view


class FileHistoryWindow(standard.Widget):
    """Show the commits that touched one file with a per-commit diff"""

    # Delay before a selected commit's diff loads. Arrow-stepping through
    # the list faster than this only diffs the commit we settle on.
    DIFF_DEBOUNCE_MSEC = 100

    def __init__(self, context, path, ref=None, parent=None):
        standard.Widget.__init__(self, parent)
        self.context = context
        self.path = path
        self.ref = ref
        self.entries = []
        # Monotonic token so a slow load cannot clobber a newer one.
        self._load_token = 0
        self._pending_entry = None
        self._sized_columns = False

        self.setWindowTitle(N_('File History: %s') % path)

        self.treewidget = FileHistoryTreeWidget(context, self)
        self.diffwidget = diff.CommitDiffWidget(context, self, is_commit=True)
        self.diff_panel = diff.DiffPanel(self.diffwidget, self.diffwidget.diff, self)

        self.splitter = qtutils.splitter(Qt.Vertical, self.treewidget, self.diff_panel)
        self.splitter.setStretchFactor(1, 2)

        self.main_layout = qtutils.vbox(defs.no_margin, defs.spacing, self.splitter)
        self.setLayout(self.main_layout)

        self.copy_oid_action = qtutils.add_action(
            self, N_('Copy Commit ID'), self.copy_oid, hotkeys.COPY_COMMIT_ID
        )
        self.difftool_action = qtutils.add_action(
            self, N_('Launch Diff Tool'), self.launch_difftool, hotkeys.DIFF
        )
        self.grab_file_action = qtutils.add_action(
            self, N_('Grab File...'), self.grab_file
        )
        self.checkout_action = qtutils.add_action(
            self, N_('Checkout This Version'), self.checkout_version
        )
        self.dag_action = qtutils.add_action(
            self, N_('View History in DAG...'), self.view_dag
        )
        qtutils.add_close_action(self)

        self._diff_timer = QtCore.QTimer(self)
        self._diff_timer.setSingleShot(True)
        self._diff_timer.setInterval(self.DIFF_DEBOUNCE_MSEC)
        self._diff_timer.timeout.connect(self._show_pending_entry)

        self.treewidget.itemSelectionChanged.connect(self._selection_changed)
        context.model.updated.connect(self.load, type=Qt.QueuedConnection)

        self.init_state(context.settings, self.resize, 720, 540)

    def load(self):
        """Reload the history in the background"""
        self._load_token += 1
        token = self._load_token
        task = qtutils.SimpleTask(
            filehistory.load_history, self.context, self.path, self.ref
        )
        self.context.runtask.start(
            task, result=lambda entries: self.set_entries(entries, token)
        )

    def set_entries(self, entries, token=None):
        """Fill the commit list, preserving the selection across reloads"""
        if token is not None and token != self._load_token:
            return
        selected_oid = self._selected_oid()
        self.entries = entries
        tree = self.treewidget
        with qtutils.BlockSignals(tree):
            tree.clear()
        if not entries:
            self.diffwidget.clear()
            return
        abbrev = prefs.abbrev(self.context)
        items = [FileHistoryItem(entry, abbrev) for entry in entries]
        tree.addTopLevelItems(items)
        if not self._sized_columns:
            # Size the one-line columns to their content once; Summary takes
            # the remaining space.
            self._sized_columns = True
            for column in (0, 2, 3):
                tree.resizeColumnToContents(column)
        index = 0
        for i, entry in enumerate(entries):
            if entry.oid == selected_oid:
                index = i
                break
        tree.setCurrentItem(items[index])

    def selected_entry(self):
        """Return the selected FileHistoryEntry or None"""
        item = self.treewidget.currentItem()
        return item.entry if item is not None else None

    def _selected_oid(self):
        entry = self.selected_entry()
        return entry.oid if entry is not None else None

    def _selection_changed(self):
        entry = self.selected_entry()
        if entry is None:
            return
        self._pending_entry = entry
        self._diff_timer.start()

    def _show_pending_entry(self):
        entry = self._pending_entry
        if entry is None:
            return
        self._pending_entry = None
        self.diffwidget.set_details(
            entry.oid, entry.author, entry.email, entry.authdate, entry.summary
        )
        self.diffwidget.oid = entry.oid
        self.diffwidget.set_diff_oid(
            entry.oid, filename=filehistory.diff_pathspec(entry)
        )

    # Actions
    def copy_oid(self):
        entry = self.selected_entry()
        if entry is not None:
            abbrev = prefs.abbrev(self.context)
            qtutils.set_clipboard(entry.oid[:abbrev])

    def launch_difftool(self):
        entry = self.selected_entry()
        if entry is not None:
            difftool.difftool_launch(
                self.context,
                left=entry.oid,
                left_take_parent=True,
                right=entry.oid,
                paths=[entry.path],
            )

    def grab_file(self):
        """Save the file as it was at the selected commit"""
        entry = self.selected_entry()
        if entry is not None:
            model = browse.BrowseModel(entry.oid, filename=entry.path)
            browse.save_path(self.context, entry.path, model)

    def checkout_version(self):
        """Check out the file as it was at the selected commit"""
        entry = self.selected_entry()
        if entry is not None:
            cmds.do(cmds.Checkout, self.context, [entry.oid, '--', entry.path])

    def view_dag(self):
        """Open the DAG scoped to this file"""
        from . import dag as dag_widget  # lazy import; avoids a circular import

        context = self.context
        existing = getattr(context.view, 'dag', None)
        view = dag_widget.git_dag(context, existing_view=existing, paths=[self.path])
        if hasattr(context.view, 'dag'):
            context.view.dag = view

    def context_menu_actions(self):
        """Return the actions shown in the commit list's context menu"""
        return (
            self.difftool_action,
            self.grab_file_action,
            self.checkout_action,
            None,
            self.copy_oid_action,
            self.dag_action,
        )


class FileHistoryTreeWidget(standard.TreeWidget):
    """The flat list of commits that touched the file"""

    def __init__(self, context, parent):
        standard.TreeWidget.__init__(self, parent)
        self.context = context
        self.view = parent
        self.setHeaderLabels([N_('ID'), N_('Summary'), N_('Author'), N_('Date')])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)

    def contextMenuEvent(self, event):
        menu = qtutils.create_menu(N_('Actions'), self)
        for action in self.view.context_menu_actions():
            if action is None:
                menu.addSeparator()
            else:
                menu.addAction(action)
        menu.exec_(self.mapToGlobal(event.pos()))


class FileHistoryItem(QtWidgets.QTreeWidgetItem):
    """One commit row in the file history list"""

    def __init__(self, entry, abbrev):
        QtWidgets.QTreeWidgetItem.__init__(self)
        self.entry = entry
        self.setText(0, entry.oid[:abbrev])
        self.setText(1, entry.summary)
        self.setText(2, entry.author)
        self.setText(3, entry.authdate)
        if entry.old_path:
            self.setToolTip(1, N_('Renamed from %s') % entry.old_path)
