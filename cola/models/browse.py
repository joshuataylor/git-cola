from __future__ import annotations
import time

from qtpy import QtGui
from qtpy.QtCore import QMimeData
from qtpy.QtCore import QModelIndex
from qtpy.QtCore import Qt
from qtpy.QtCore import Signal

from .. import gitcmds
from .. import icons
from .. import qtutils
from .. import utils
from ..git import STDOUT
from ..i18n import N_

# The batched metadata walk stops after this many commits. Paths whose
# newest commit is older than the cap fall back to a per-path task.
METADATA_COMMIT_LIMIT = 10000

# States for the batched metadata cache.
METADATA_EMPTY = 'empty'
METADATA_RUNNING = 'running'
METADATA_COMPLETE = 'complete'


def gather_metadata(
    context, limit: int = METADATA_COMMIT_LIMIT
) -> tuple[str | None, dict[str, tuple[str, str, str]], bool]:
    """Walk history once and build a path -> (age, message, author) map

    Replaces the per-path "git log -1 -- <path>" N+1 subprocess pattern.
    Returns (head_oid, metadata, truncated).
    """
    out = context.git.log(
        f'-{limit}',
        z=True,
        name_only=True,
        no_renames=True,
        no_color=True,
        # tformat terminates each entry, so the metadata token is always
        # NUL-separated from the commit's file list ("format" would not be).
        pretty=r'tformat:%x00%H%x1f%ar%x01%s%x01%an',
        _readonly=True,
    )[STDOUT]
    return parse_metadata_log(out, limit)


def parse_metadata_log(
    out: str, limit: int
) -> tuple[str | None, dict[str, tuple[str, str, str]], bool]:
    """Parse "git log -z --name-only" output framed by %x00 markers

    Each record is "<oid>\\x1f<age>\\x01<subject>\\x01<author>" preceded by a
    NUL marker, then the commit's first path prefixed with "\\n" and further
    paths as plain NUL-separated tokens. Records arrive newest-first, so the
    first commit that mentions a path (or anything beneath a directory)
    provides that entry's metadata -- the same result "git log -1 -- <path>"
    produces for each path.
    """
    head_oid = None
    metadata: dict[str, tuple[str, str, str]] = {}
    commits = 0
    meta = None
    for token in out.split('\0'):
        if not token:
            continue
        if '\x1f' in token:
            oid, details = token.split('\x1f', 1)
            age, message, author = details.split('\x01', 2)
            meta = (age, message, author)
            commits += 1
            if head_oid is None:
                head_oid = oid
            continue
        if meta is None:
            continue
        path = token[1:] if token.startswith('\n') else token
        if path not in metadata:
            metadata[path] = meta
        # Credit ancestor directories: crediting a directory also credited
        # all of its ancestors, so stop at the first hit.
        parent = utils.dirname(path)
        while parent:
            if parent in metadata:
                break
            metadata[parent] = meta
            parent = utils.dirname(parent)
    return head_oid, metadata, commits >= limit


def relative_mtime(ops, path: str) -> str:
    """Return a relative age for paths without any git history"""
    try:
        st = ops.stat(path)
    except OSError:
        return N_('%d minutes ago') % 0
    elapsed = time.time() - st.get('st_mtime')
    minutes = int(elapsed / 60)
    if minutes < 60:
        return N_('%d minutes ago') % minutes
    hours = int(elapsed / 60 / 60)
    if hours < 24:
        return N_('%d hours ago') % hours
    return N_('%d days ago') % int(elapsed / 60 / 60 / 24)


def path_status(
    path: str, unmerged, modified, staged, upstream_changed, untracked
) -> tuple[str | None, str]:
    """Return the (icon, text) status for a path given the parent-closed sets"""
    if path in unmerged:
        status = (icons.modified_name(), N_('Unmerged'))
    elif path in modified and path in staged:
        status = (icons.partial_name(), N_('Partially Staged'))
    elif path in modified:
        status = (icons.modified_name(), N_('Modified'))
    elif path in staged:
        status = (icons.staged_name(), N_('Staged'))
    elif path in upstream_changed:
        status = (icons.upstream_name(), N_('Changed Upstream'))
    elif path in untracked:
        status = (None, '?')
    else:
        status = (None, '')
    return status


def _head_oid(context) -> str | None:
    """Return the current HEAD oid, or None when there is no HEAD"""
    out = context.git.rev_parse('HEAD', _readonly=True)[STDOUT].strip()
    return out or None


class Columns:
    """Defines columns in the worktree browser"""

    NAME = 0
    STATUS = 1
    MESSAGE = 2
    AUTHOR = 3
    AGE = 4

    ALL = (NAME, STATUS, MESSAGE, AUTHOR, AGE)
    ATTRS = ('name', 'status', 'message', 'author', 'age')
    TEXT: list[str] = []

    @classmethod
    def init(cls) -> None:
        cls.TEXT.extend(
            [N_('Name'), N_('Status'), N_('Message'), N_('Author'), N_('Age')]
        )

    @classmethod
    def text_values(cls) -> list[str]:
        if not cls.TEXT:
            cls.init()
        return cls.TEXT

    @classmethod
    def text(cls, column: int) -> str:
        try:
            value = cls.TEXT[column]
        except IndexError:
            # Defer translation until runtime
            cls.init()
            value = cls.TEXT[column]
        return value

    @classmethod
    def attr(cls, column: int) -> str:
        """Return the attribute for the column"""
        return cls.ATTRS[column]


class GitRepoModel(QtGui.QStandardItemModel):
    """Provides an interface into a git repository for browsing purposes."""

    restore = Signal()

    def __init__(self, context, parent) -> None:
        QtGui.QStandardItemModel.__init__(self, parent)
        self.setColumnCount(len(Columns.ALL))

        self.context = context
        self.model = context.model
        self.entries = {}
        cfg = context.cfg
        self.turbo = cfg.get('cola.turbo', False)
        self.default_author = cfg.get('user.name', N_('Author'))
        self._interesting_paths = set()
        self._interesting_files = set()
        self._runtask = qtutils.RunTask(parent=parent)
        # Batched per-path metadata (age, message, author) from a single
        # history walk, replacing one "git log -1" subprocess per path.
        self._metadata = {}
        self._metadata_head = None
        self._metadata_state = METADATA_EMPTY
        self._metadata_truncated = False
        self._metadata_pending = set()
        self._cached_status_sets = None

        self.model.updated.connect(self.refresh, type=Qt.QueuedConnection)

        self.file_icon = icons.file_text()
        self.dir_icon = icons.directory()

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        paths = qtutils.paths_from_indexes(
            self.context, self, indexes, item_type=GitRepoNameItem.TYPE
        )
        return qtutils.mimedata_from_paths(self.context, paths)

    def mimeTypes(self) -> list[str]:
        return qtutils.path_mimetypes()

    def clear(self) -> None:
        self.entries.clear()
        super().clear()

    def hasChildren(self, index: QModelIndex) -> bool:
        if index.isValid():
            item = self.itemFromIndex(index)
            result = item.hasChildren()
        else:
            result = True
        return result

    def get(self, path: str, default=None):
        if not path:
            item = self.invisibleRootItem()
        else:
            item = self.entries.get(path, default)
        return item

    def create_row(
        self, path: str, create: bool = True, is_dir: bool = False
    ) -> list[GitRepoNameItem | GitRepoItem]:
        try:
            row = self.entries[path]
        except KeyError:
            if create:
                column = create_column
                row = self.entries[path] = [
                    column(c, path, is_dir) for c in Columns.ALL
                ]
            else:
                row = None
        return row

    def populate(self, item: GitRepoItem) -> None:
        self.populate_dir(item, item.path + '/')

    def add_directory(self, parent: GitRepoItem, path: str) -> GitRepoItem:
        """Add a directory entry to the model."""
        # First, try returning an existing item
        current_item = self.get(path)
        if current_item is not None:
            return current_item[0]

        # Create model items
        row_items = self.create_row(path, is_dir=True)

        # Use a standard directory icon
        name_item = row_items[0]
        name_item.setIcon(self.dir_icon)
        parent.appendRow(row_items)

        return name_item

    def add_file(self, parent: GitRepoItem, path: str) -> GitRepoItem:
        """Add a file entry to the model."""

        file_entry = self.get(path)
        if file_entry is not None:
            return file_entry

        # Create model items
        row_items = self.create_row(path)
        name_item = row_items[0]

        # Use a standard file icon for the name field
        name_item.setIcon(self.file_icon)

        # Add file paths at the end of the list
        parent.appendRow(row_items)

        return name_item

    def populate_dir(self, parent: GitRepoItem, path: str) -> None:
        """Populate a subtree"""
        context = self.context
        dirs, paths = gitcmds.listdir(context, path)

        # Insert directories before file paths
        for dirname in dirs:
            dir_parent = parent
            if '/' in dirname:
                dir_parent = self.add_parent_directories(parent, dirname)
            self.add_directory(dir_parent, dirname)
            self.update_entry(dirname)

        for filename in paths:
            file_parent = parent
            if '/' in filename:
                file_parent = self.add_parent_directories(parent, filename)
            self.add_file(file_parent, filename)
            self.update_entry(filename)

    def add_parent_directories(self, parent: GitRepoItem, dirname: str) -> GitRepoItem:
        """Ensure that all parent directory entries exist"""
        sub_parent = parent
        parent_dir = utils.dirname(dirname)
        for path in utils.pathset(parent_dir):
            sub_parent = self.add_directory(sub_parent, path)
        return sub_parent

    def path_is_interesting(self, path: str) -> bool:
        """Return True if path has a status."""
        return path in self._interesting_paths

    def get_paths(self, files=None) -> set[str]:
        """Return paths of interest; e.g. paths with a status."""
        if files is None:
            files = self.get_files()
        return utils.add_parents(files)

    def get_files(self):
        model = self.model
        return set(model.staged + model.unstaged)

    def refresh(self) -> None:
        self._cached_status_sets = None
        if self._metadata_state == METADATA_COMPLETE:
            # HEAD moves when commits land; working-tree-only updates (the
            # common case) leave it untouched and cost one background
            # rev-parse instead of a new metadata walk.
            task = qtutils.SimpleTask(_head_oid, self.context)
            self._runtask.start(task, result=self._verify_metadata_head)

        old_files = self._interesting_files
        old_paths = self._interesting_paths
        new_files = self.get_files()
        new_paths = self.get_paths(files=new_files)

        if new_files != old_files or not old_paths:
            self.clear()
            self._initialize()
            self.restore.emit()

        # Existing items
        for path in sorted(new_paths.union(old_paths)):
            self.update_entry(path)

        self._interesting_files = new_files
        self._interesting_paths = new_paths

    def _initialize(self) -> None:
        self.setHorizontalHeaderLabels(Columns.text_values())
        self.entries = {}
        self._interesting_files = files = self.get_files()
        self._interesting_paths = self.get_paths(files=files)

        root = self.invisibleRootItem()
        self.populate_dir(root, './')

    def update_entry(self, path: str) -> None:
        if self.turbo or path not in self.entries:
            return  # entry doesn't currently exist
        try:
            age, message, author = self._metadata[path]
        except KeyError:
            pass
        else:
            self.apply_data((path, self._path_status(path), message, author, age))
            return
        if self._metadata_state == METADATA_COMPLETE:
            if self._metadata_truncated:
                # Rare: the path's newest commit is older than the walk cap,
                # so fall back to a bounded per-path lookup.
                task = GitRepoInfoTask(self.context, path, self.default_author)
                task.connect(self.apply_data)
                self._runtask.start(task)
            else:
                # No history at all: a new or untracked path.
                data = (
                    path,
                    self._path_status(path),
                    '-',
                    self.default_author,
                    relative_mtime(self.context.ops, path),
                )
                self.apply_data(data)
            return
        self._metadata_pending.add(path)
        self._start_metadata_task()

    def _start_metadata_task(self) -> None:
        """Start the one-shot batched metadata walk"""
        if self._metadata_state != METADATA_EMPTY:
            return
        self._metadata_state = METADATA_RUNNING
        task = qtutils.SimpleTask(gather_metadata, self.context)
        self._runtask.start(task, result=self._apply_metadata)

    def _apply_metadata(self, result) -> None:
        """Store the walk results and flush entries that waited on them"""
        head_oid, metadata, truncated = result
        self._metadata = metadata
        self._metadata_head = head_oid
        self._metadata_truncated = truncated
        self._metadata_state = METADATA_COMPLETE
        pending = self._metadata_pending
        self._metadata_pending = set()
        for path in pending:
            self.update_entry(path)

    def _verify_metadata_head(self, head_oid) -> None:
        """Rebuild the metadata cache when HEAD has moved"""
        if self._metadata_state != METADATA_COMPLETE:
            return
        if head_oid == self._metadata_head:
            return
        self._metadata = {}
        self._metadata_head = None
        self._metadata_state = METADATA_EMPTY
        self._metadata_truncated = False
        for path in list(self.entries):
            self.update_entry(path)

    def _status_sets(self):
        """Return the parent-closed status sets, cached per refresh"""
        if self._cached_status_sets is None:
            model = self.model
            self._cached_status_sets = (
                utils.add_parents(model.unmerged),
                utils.add_parents(model.modified),
                utils.add_parents(model.staged),
                utils.add_parents(model.upstream_changed),
                utils.add_parents(model.untracked),
            )
        return self._cached_status_sets

    def _path_status(self, path: str) -> tuple[str | None, str]:
        return path_status(path, *self._status_sets())

    def apply_data(self, data: list[str]) -> None:
        entry = self.get(data[0])
        if entry:
            entry[1].set_status(data[1])
            entry[2].setText(data[2])
            entry[3].setText(data[3])
            entry[4].setText(data[4])


def create_column(col, path: str, is_dir: bool) -> GitRepoNameItem | GitRepoItem:
    """Creates a StandardItem for use in a treeview cell."""
    # GitRepoNameItem is the only one that returns a custom type()
    # and is used to infer selections.
    if col == Columns.NAME:
        item = GitRepoNameItem(path, is_dir)
    else:
        item = GitRepoItem(path)
    return item


class GitRepoInfoTask(qtutils.Task):
    """Handles expensive git lookups for a path."""

    def __init__(self, context, path: str, default_author: str) -> None:
        qtutils.Task.__init__(self)
        self.context = context
        self.path = path
        self._default_author = default_author
        self._data = {}

    def data(self, key: str) -> str:
        """Return git data for a path

        Supported keys are 'date', 'message', and 'author'

        """
        git = self.context.git
        if not self._data:
            log_line = git.log(
                '-1',
                '--',
                self.path,
                no_color=True,
                pretty=r'format:%ar%x01%s%x01%an',
                _readonly=True,
            )[STDOUT]
            if log_line:
                date, message, author = log_line.split(chr(0x01), 2)
                self._data['date'] = date
                self._data['message'] = message
                self._data['author'] = author
            else:
                self._data['date'] = self.date()
                self._data['message'] = '-'
                self._data['author'] = self._default_author

        return self._data[key]

    def date(self) -> str:
        """Returns a relative date for a file path

        This is typically used for new entries that do not have
        'git log' information.

        """
        return relative_mtime(self.context.ops, self.path)

    def status(self) -> tuple[str | None, str]:
        """Return the status for the entry's path."""
        model = self.context.model
        return path_status(
            self.path,
            utils.add_parents(model.unmerged),
            utils.add_parents(model.modified),
            utils.add_parents(model.staged),
            utils.add_parents(model.upstream_changed),
            utils.add_parents(model.untracked),
        )

    def task(self) -> tuple[str, tuple[str | None, str], str, str, str]:
        """Perform expensive lookups and post corresponding events."""
        data = (
            self.path,
            self.status(),
            self.data('message'),
            self.data('author'),
            self.data('date'),
        )
        return data


class GitRepoItem(QtGui.QStandardItem):
    """Represents a cell in a treeview.

    Many GitRepoItems map to a single repository path.
    Each GitRepoItem manages a different cell in the tree view.
    One is created for each column -- Name, Status, Age, etc.

    """

    def __init__(self, path: str) -> None:
        QtGui.QStandardItem.__init__(self)
        self.path = path
        self.cached = False
        self.setDragEnabled(False)
        self.setEditable(False)

    def set_status(self, data: tuple[str | None, str]) -> None:
        icon, txt = data
        if icon:
            self.setIcon(QtGui.QIcon(icon))
        else:
            self.setIcon(QtGui.QIcon())
        self.setText(txt)


class GitRepoNameItem(GitRepoItem):
    """Subclass GitRepoItem to provide a custom type()."""

    TYPE = qtutils.standard_item_type_value(1)

    def __init__(self, path: str, is_dir: bool) -> None:
        GitRepoItem.__init__(self, path)
        self.is_dir: bool = is_dir
        self.setDragEnabled(True)
        self.setText(utils.basename(path))

    def type(self):
        """
        Indicate that this item is of a special user-defined type.

        'name' is the only column that registers a user-defined type.
        This is done to allow filtering out other columns when determining
        which paths are selected.

        """
        return self.TYPE

    def hasChildren(self) -> bool:
        return self.is_dir
