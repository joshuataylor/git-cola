from __future__ import annotations
import bisect
import collections
import datetime
import enum
import functools
import html
import itertools
import math
import subprocess
import sys
import threading
from functools import partial

from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets
from qtpy.QtCore import Qt
from qtpy.QtCore import Signal

from .. import cmds
from .. import core
from .. import dates
from .. import difftool
from .. import gitcmds
from .. import guicmds
from .. import hotkeys
from .. import icons
from .. import qtcompat
from .. import qtutils
from .. import textwrap
from .. import utils
from ..i18n import N_
from ..models import dag
from ..models import main
from ..models import prefs
from ..models.graph import GraphRowColor
from ..models.graph import build_graph
from ..qtutils import get
from . import archive
from . import browse
from . import completion
from . import createbranch
from . import createtag
from . import defs
from . import diff
from . import diff_intraline
from . import filelist
from . import finder
from . import prefs as prefs_widget
from . import standard
from .text import HintedDefaultLineEdit


def git_dag(
    context, args=None, existing_view=None, show=True, paths=None, prefs_model=None
):
    """Return a pre-populated git DAG widget.

    prefs_model is shared with the Preferences dialog opened from the DAG; the
    main window passes its own so config changes made there reach it too.
    """
    model = context.model
    branch = model.currentbranch
    if paths:
        # Scope the DAG to these paths, mirroring GitDAG.histories_selected().
        argv = ([branch] if branch else []) + ['--'] + list(paths)
        params = dag.DAG(core.list2cmdline(argv), 1000)
    else:
        # disambiguate between branch names and filenames by using '--'
        branch_doubledash = (branch + ' --') if branch else ''
        params = dag.DAG(branch_doubledash, 1000)
    params.set_arguments(args)

    if existing_view is None:
        view = GitDAG(context, params, prefs_model=prefs_model)
    else:
        view = existing_view
        view.set_params(params)
    if show:
        view.show()
    if params.ref:
        view.display()
    return view


class FocusRedirectProxy:
    """Redirect actions from the main widget to child widgets"""

    def __init__(self, *widgets):
        """Provide proxied widgets; the default widget must be first"""
        self.widgets = widgets
        self.default = widgets[0]

    def __getattr__(self, name):
        return lambda *args, **kwargs: self._forward_action(name, *args, **kwargs)

    def _forward_action(self, name, *args, **kwargs):
        """Forward the captured action to the focused or default widget"""
        widget = QtWidgets.QApplication.focusWidget()
        if widget in self.widgets and hasattr(widget, name):
            func = getattr(widget, name)
        else:
            func = getattr(self.default, name)

        return func(*args, **kwargs)


class ViewerMixin:
    """Implementations must provide selected_items()"""

    def __init__(self):
        self.context = None  # provided by implementation
        self.selected = None
        self.clicked = None
        self.menu_actions = None  # provided by implementation

    def selected_item(self):
        """Return the currently selected item"""
        selected_items = self.selected_items()
        if not selected_items:
            return None
        return selected_items[0]

    def selected_oid(self):
        """Return the currently selected commit object ID"""
        item = self.selected_item()
        if item is None:
            result = None
        else:
            result = item.commit.oid
        return result

    def selected_oids(self):
        """Return the currently selected commit object IDs"""
        return [i.commit for i in self.selected_items()]

    def clicked_oid(self, filtered=True):
        """Return the clicked or selected commit object ID"""
        if self.clicked:
            oid = self.clicked.oid
        else:
            oid = self.selected_oid()
        if filtered and oid and oid in (dag.STAGE, dag.WORKTREE):
            oid = None
        return oid

    def clicked_commit(self, filtered=True):
        """Return the clicked or selected commit object"""
        if self.clicked:
            item = self.clicked
        else:
            item = self.selected_item()
        if item and hasattr(item, 'commit'):
            commit = item.commit
        else:
            commit = item
        if filtered and commit and commit.oid in (dag.STAGE, dag.WORKTREE):
            commit = None
        return commit

    def with_oid(self, func, filtered=True):
        """Run an operation with a commit object ID"""
        oid = self.clicked_oid(filtered=filtered)
        if oid:
            result = func(oid)
        else:
            result = None
        return result

    def with_oid_short(self, func):
        """Run an operation with a short commit object ID"""
        oid = self.clicked_oid()
        if oid:
            abbrev = prefs.abbrev(self.context)
            result = func(oid[:abbrev])
        else:
            result = None
        return result

    def with_selected_oid(self, func):
        """Run an operation with a commit object ID"""
        oid = self.selected_oid()
        if oid:
            result = func(oid)
        else:
            result = None
        return result

    def diff_selected_this(self):
        """Diff the selected commit against the clicked commit"""
        clicked_oid = self.clicked.oid
        selected_oid = self.selected.oid
        self.diff_commits.emit(selected_oid, clicked_oid)

    def diff_this_selected(self):
        """Diff the clicked commit against the selected commit"""
        clicked_oid = self.clicked.oid
        selected_oid = self.selected.oid
        self.diff_commits.emit(clicked_oid, selected_oid)

    def cherry_pick(self):
        """Cherry-pick a commit using git cherry-pick"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.CherryPick, context, [oid]))

    def revert(self):
        """Revert a commit using git revert"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.Revert, context, oid))

    def selected_commits_for_copy(self):
        """Return every selected commit, for the clipboard Copy actions"""
        items = self.selected_items()
        selected_oids = [item.commit.oid for item in items]
        clicked = self.clicked
        if clicked and clicked.oid not in selected_oids:
            # Right-clicked a commit outside the current selection: copy just it.
            commits = [clicked]
        else:
            commits = sort_by_generation([item.commit for item in items])
        return [
            commit for commit in commits if commit.oid not in (dag.STAGE, dag.WORKTREE)
        ]

    def selected_oids_for_copy(self):
        """Return oids for every selected commit, for the clipboard Copy actions"""
        return [commit.oid for commit in self.selected_commits_for_copy()]

    def copy_to_clipboard(self):
        """Copy the selected commit object IDs to the clipboard"""
        oids = self.selected_oids_for_copy()
        if oids:
            qtutils.set_clipboard('\n'.join(oids))

    def copy_to_clipboard_short(self):
        """Copy the abbreviated selected commit object IDs to the clipboard"""
        oids = self.selected_oids_for_copy()
        if oids:
            abbrev = prefs.abbrev(self.context)
            qtutils.set_clipboard('\n'.join(oid[:abbrev] for oid in oids))

    def _copy_lines(self, formatter):
        """Copy one formatted line per selected commit to the clipboard"""
        commits = self.selected_commits_for_copy()
        lines = [formatter(commit) for commit in commits]
        lines = [line for line in lines if line]
        if lines:
            qtutils.set_clipboard('\n'.join(lines))

    def copy_title_to_clipboard(self):
        """Copy the selected commit titles to the clipboard, one per line"""
        self._copy_lines(lambda commit: commit.summary)

    def copy_oneline_to_clipboard(self):
        """Copy the selected commits as `git log --oneline` lines"""
        abbrev = prefs.abbrev(self.context)
        self._copy_lines(lambda commit: f'{commit.oid[:abbrev]} {commit.summary}')

    def copy_link_to_clipboard(self):
        """Copy the web URL of each selected commit, one per line"""
        context = self.context
        self._copy_lines(lambda commit: gitcmds.commit_web_url(context, commit.oid))

    def copy_markdown_link_to_clipboard(self):
        """Copy each selected commit as a Markdown link: [abbrev](url) title"""
        context = self.context
        abbrev = prefs.abbrev(self.context)

        def fmt(commit):
            url = gitcmds.commit_web_url(context, commit.oid)
            if not url:
                return ''
            return f'[{commit.oid[:abbrev]}]({url}) {commit.summary}'

        self._copy_lines(fmt)

    def copy_message_to_clipboard(self):
        """Copy the selected commit messages to the clipboard"""
        self._copy_messages(unwrap=False)

    def copy_message_unwrapped_to_clipboard(self):
        """Copy the selected commit messages with paragraphs unwrapped"""
        self._copy_messages(unwrap=True)

    def _copy_messages(self, unwrap):
        oids = self.selected_oids_for_copy()
        if not oids:
            return
        messages = commit_messages(self.context, oids, unwrap=unwrap)
        qtutils.set_clipboard('\n\n'.join(msg for msg in messages if msg))

    def checkout_branch(self):
        """Checkout the clicked/selected branch"""
        branches = []
        clicked = self.clicked
        selected = self.selected_item()
        if clicked:
            branches.extend(clicked.branches)
        if selected:
            branches.extend(selected.commit.branches)
        if not branches:
            return
        guicmds.checkout_branch(self.context, default=branches[0])

    def create_branch(self):
        """Create a branch at the selected commit"""
        context = self.context
        create_new_branch = partial(createbranch.create_new_branch, context)
        self.with_oid(lambda oid: create_new_branch(revision=oid))

    def create_tag(self):
        """Create a tag at the selected commit"""
        context = self.context
        self.with_oid(lambda oid: createtag.create_tag(context, ref=oid))

    def create_tarball(self):
        """Create a tarball from the selected commit"""
        context = self.context
        self.with_oid(lambda oid: archive.show_save_dialog(context, oid, parent=self))

    def show_diff(self):
        """Show the diff for the selected commit"""
        commit = self.clicked_commit()
        if not commit:
            return
        is_root_commit = not commit.parents
        self.with_oid(
            lambda oid: _diff_expression(self.context, self, oid, is_root_commit),
            filtered=False,
        )

    def show_dir_diff(self):
        """Show a full directory diff for the selected commit"""
        context = self.context
        commit = self.clicked_commit()
        if not commit:
            return
        is_root_commit = not commit.parents
        self.with_oid(
            lambda oid: difftool.difftool_launch(
                context,
                oid=oid,
                is_root_commit=is_root_commit,
                dir_diff=True,
                staged=oid == dag.STAGE,
            ),
            filtered=False,
        )

    def rebase_to_commit(self):
        """Rebase the current branch to the selected commit"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.Rebase, context, upstream=oid))

    def reset_mixed(self):
        """Reset the repository using git reset --mixed"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.ResetMixed, context, ref=oid))

    def reset_keep(self):
        """Reset the repository using git reset --keep"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.ResetKeep, context, ref=oid))

    def reset_merge(self):
        """Reset the repository using git reset --merge"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.ResetMerge, context, ref=oid))

    def reset_soft(self):
        """Reset the repository using git reset --soft"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.ResetSoft, context, ref=oid))

    def reset_hard(self):
        """Reset the repository using git reset --hard"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.ResetHard, context, ref=oid))

    def restore_worktree(self):
        """Reset the worktree contents from the selected commit"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.RestoreWorktree, context, ref=oid))

    def checkout_detached(self):
        """Checkout a commit using an anonymous detached HEAD"""
        context = self.context
        self.with_oid(lambda oid: cmds.do(cmds.Checkout, context, [oid]))

    def save_blob_dialog(self):
        """Save a file blob from the selected commit"""
        context = self.context
        self.with_oid(
            lambda oid: browse.BrowseBranch.browse(context, oid), filtered=False
        )

    def save_blob_from_parent_dialog(self):
        """Save a file blob from the parent of the selected commit"""
        self.with_oid(
            lambda oid: _save_blob_from_parent(self.context, oid), filtered=False
        )

    def search_line_range(self):
        """Open a dialog to select a range of lines from a file"""
        self.with_oid(lambda oid: self.search_line_range_in_oid.emit(oid))

    def update_menu_actions(self, event):
        """Update menu actions to reflect the selection state"""
        selected_items = self.selected_items()
        selected_item = self.selected_item()
        item = self.itemAt(event.pos())
        if item is None:
            self.clicked = commit = None
        else:
            self.clicked = commit = item.commit

        has_oid = bool(commit and commit.oid not in (dag.WORKTREE, dag.STAGE))
        has_single_selection = len(selected_items) == 1
        has_single_selection_or_clicked = bool(has_single_selection or commit)
        has_selection = bool(selected_items)
        can_diff = bool(
            commit
            and has_single_selection
            and selected_items
            and commit is not selected_items[0].commit
        )
        has_branches = (
            has_single_selection
            and selected_item
            and bool(selected_item.commit.branches)
        ) or (self.clicked and bool(self.clicked.branches))

        if can_diff:
            self.selected = selected_items[0].commit
        else:
            self.selected = None

        self.menu_actions['diff_this_selected'].setEnabled(can_diff)
        self.menu_actions['diff_selected_this'].setEnabled(can_diff)
        self.menu_actions['diff_commit'].setEnabled(has_single_selection_or_clicked)
        self.menu_actions['diff_commit_all'].setEnabled(has_single_selection_or_clicked)
        self.menu_actions['checkout_branch'].setEnabled(bool(has_branches) and has_oid)
        self.menu_actions['checkout_detached'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['cherry_pick'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['copy'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['copy_short'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        for name in (
            'copy_title',
            'copy_oneline',
            'copy_link',
            'copy_markdown_link',
        ):
            self.menu_actions[name].setEnabled(
                has_single_selection_or_clicked and has_oid
            )
        self.menu_actions['copy_message'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['copy_message_unwrapped'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['create_branch'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['create_patch'].setEnabled(has_selection and has_oid)
        self.menu_actions['create_tag'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['create_tarball'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['rebase_to_commit'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['reset_mixed'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['reset_keep'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['reset_merge'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['reset_soft'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['reset_hard'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['restore_worktree'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['revert'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['save_blob'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )
        self.menu_actions['save_blob_from_parent'].setEnabled(
            has_single_selection_or_clicked
        )
        self.menu_actions['search_line_range'].setEnabled(
            has_single_selection_or_clicked and has_oid
        )

    def context_menu_event(self, event):
        """Build a context menu and execute it"""
        self.update_menu_actions(event)
        menu = qtutils.create_menu(N_('Actions'), self)
        menu.addAction(self.menu_actions['diff_this_selected'])
        menu.addAction(self.menu_actions['diff_selected_this'])
        menu.addAction(self.menu_actions['diff_commit'])
        menu.addAction(self.menu_actions['diff_commit_all'])
        menu.addSeparator()
        menu.addAction(self.menu_actions['search_line_range'])
        menu.addSeparator()
        menu.addAction(self.menu_actions['checkout_branch'])
        menu.addAction(self.menu_actions['create_branch'])
        menu.addAction(self.menu_actions['create_tag'])
        menu.addAction(self.menu_actions['rebase_to_commit'])
        menu.addSeparator()
        menu.addAction(self.menu_actions['cherry_pick'])
        menu.addAction(self.menu_actions['revert'])
        menu.addAction(self.menu_actions['create_patch'])
        menu.addAction(self.menu_actions['create_tarball'])
        menu.addSeparator()
        reset_menu = qtutils.add_menu(
            N_('Reset'), menu, icon=icons.style_dialog_reset()
        )
        reset_menu.addAction(self.menu_actions['reset_soft'])
        reset_menu.addAction(self.menu_actions['reset_mixed'])
        reset_menu.addAction(self.menu_actions['restore_worktree'])
        reset_menu.addSeparator()
        reset_menu.addAction(self.menu_actions['reset_keep'])
        reset_menu.addAction(self.menu_actions['reset_merge'])
        reset_menu.addAction(self.menu_actions['reset_hard'])
        menu.addAction(self.menu_actions['checkout_detached'])
        menu.addSeparator()
        menu.addAction(self.menu_actions['save_blob'])
        menu.addAction(self.menu_actions['save_blob_from_parent'])
        menu.addAction(self.menu_actions['copy_short'])
        menu.addAction(self.menu_actions['copy'])
        menu.addAction(self.menu_actions['copy_title'])
        menu.addAction(self.menu_actions['copy_oneline'])
        menu.addAction(self.menu_actions['copy_message'])
        menu.addAction(self.menu_actions['copy_message_unwrapped'])
        menu.addAction(self.menu_actions['copy_link'])
        menu.addAction(self.menu_actions['copy_markdown_link'])
        menu.exec_(self.mapToGlobal(event.pos()))


def format_message_tooltip(message):
    """Render a commit message as tooltip markup

    Wrapping the escaped text in <pre> keeps the author's line breaks and
    stops QToolTip re-flowing it as rich text.
    """
    if not message:
        return ''
    return '<pre>' + html.escape(message) + '</pre>'


def commit_message(context, oid, unwrap=False):
    """Return the full commit message for an oid

    With unwrap=True the body paragraphs are joined back into single lines
    while the subject line is left untouched.
    """
    message = gitcmds.log(context, '-1', oid, '--', pretty='format:%B').strip()
    return _unwrap_message(message) if unwrap else message


def commit_messages(context, oids, unwrap=False):
    """Return the full messages of the commits, in order, with one git call

    Copying the messages of a large selection ran "git log -1" per commit on
    the GUI thread. The STAGE and WORKTREE rows have no message.
    """
    oids = [oid for oid in oids if oid not in (dag.STAGE, dag.WORKTREE)]
    if not oids:
        return []
    out = gitcmds.log(
        context,
        '--no-walk=unsorted',
        '--stdin',
        '--no-show-signature',
        pretty='format:%B%x00',
        _input=('\n'.join(oids) + '\n').encode('utf-8'),
    )
    messages = out.split('\0')
    # git rejects the whole batch when one oid is bad: ask one at a time.
    if len(messages) < len(oids):
        return [commit_message(context, oid, unwrap=unwrap) for oid in oids]
    messages = [message.strip() for message in messages[: len(oids)]]
    if unwrap:
        messages = [_unwrap_message(message) for message in messages]
    return messages


def _unwrap_message(message):
    """Join the body paragraphs into single lines, leaving the subject alone"""
    if not message:
        return message
    subject, sep, body = message.partition('\n')
    return subject + sep + textwrap.unwrap(body)


def _diff_expression(context, widget, oid, is_root_commit):
    """Launch difftool using the specified object ID"""
    if oid == dag.WORKTREE:
        ref = ''
    elif oid == dag.STAGE:
        ref = '--cached'
    elif is_root_commit:
        ref = f'{context.model.empty_tree_oid}..{oid}'
    else:
        ref = f'{oid}~..{oid}'
    return difftool.diff_expression(
        context, widget, ref, hide_expr=False, focus_tree=True
    )


class ColumnInitState:
    """State machine states for initialization of column widths"""

    NONE = 0
    SHOW_EVENT = 1
    GRAPH = 2
    COMPLETE = 3


def _save_blob_from_parent(context, oid):
    """Save a browse dialog to grab a file from the parent commit"""
    if oid in (dag.STAGE, dag.WORKTREE):
        ref = 'HEAD'
    else:
        ref = f'{oid}^'
    return browse.BrowseBranch.browse(context, ref)


def set_icon(icon, action):
    """ "Set the icon for an action and return the action"""
    action.setIcon(icon)
    return action


def viewer_actions(widget, proxy):
    """Return common actions across the tree and graph widgets"""
    return {
        'diff_this_selected': set_icon(
            icons.compare(),
            qtutils.add_action(
                widget, N_('Diff this -> selected'), proxy.diff_this_selected
            ),
        ),
        'diff_selected_this': set_icon(
            icons.compare(),
            qtutils.add_action(
                widget, N_('Diff selected -> this'), proxy.diff_selected_this
            ),
        ),
        'create_branch': set_icon(
            icons.branch(),
            qtutils.add_action(widget, N_('Create Branch'), proxy.create_branch),
        ),
        'create_patch': set_icon(
            icons.save(),
            qtutils.add_action(widget, N_('Create Patch'), proxy.create_patch),
        ),
        'create_tag': set_icon(
            icons.tag(),
            qtutils.add_action(widget, N_('Create Tag'), proxy.create_tag),
        ),
        'create_tarball': set_icon(
            icons.file_zip(),
            qtutils.add_action(
                widget, N_('Save As Tarball/Zip...'), proxy.create_tarball
            ),
        ),
        'cherry_pick': set_icon(
            icons.cherry_pick(),
            qtutils.add_action(widget, N_('Cherry Pick'), proxy.cherry_pick),
        ),
        'revert': set_icon(
            icons.undo(), qtutils.add_action(widget, N_('Revert'), proxy.revert)
        ),
        'diff_commit': set_icon(
            icons.diff(),
            qtutils.add_action(
                widget, N_('Launch Diff Tool'), proxy.show_diff, hotkeys.DIFF
            ),
        ),
        'diff_commit_all': set_icon(
            icons.diff(),
            qtutils.add_action(
                widget,
                N_('Launch Directory Diff Tool'),
                proxy.show_dir_diff,
                hotkeys.DIFF_SECONDARY,
            ),
        ),
        'checkout_branch': set_icon(
            icons.branch(),
            qtutils.add_action(widget, N_('Checkout Branch'), proxy.checkout_branch),
        ),
        'checkout_detached': set_icon(
            icons.branch(),
            qtutils.add_action(
                widget, N_('Checkout Detached HEAD'), proxy.checkout_detached
            ),
        ),
        'rebase_to_commit': set_icon(
            icons.play(),
            qtutils.add_action(
                widget, N_('Rebase to this commit'), proxy.rebase_to_commit
            ),
        ),
        'reset_soft': set_icon(
            icons.style_dialog_reset(),
            qtutils.add_action(widget, N_('Reset Branch (Soft)'), proxy.reset_soft),
        ),
        'reset_mixed': set_icon(
            icons.style_dialog_reset(),
            qtutils.add_action(
                widget, N_('Reset Branch and Stage (Mixed)'), proxy.reset_mixed
            ),
        ),
        'reset_keep': set_icon(
            icons.style_dialog_reset(),
            qtutils.add_action(
                widget,
                N_('Restore Worktree and Reset All (Keep Unstaged Edits)'),
                proxy.reset_keep,
            ),
        ),
        'reset_merge': set_icon(
            icons.style_dialog_reset(),
            qtutils.add_action(
                widget,
                N_('Restore Worktree and Reset All (Merge)'),
                proxy.reset_merge,
            ),
        ),
        'reset_hard': set_icon(
            icons.style_dialog_reset(),
            qtutils.add_action(
                widget,
                N_('Restore Worktree and Reset All (Hard)'),
                proxy.reset_hard,
            ),
        ),
        'restore_worktree': set_icon(
            icons.edit(),
            qtutils.add_action(widget, N_('Restore Worktree'), proxy.restore_worktree),
        ),
        'save_blob': set_icon(
            icons.save(),
            qtutils.add_action(widget, N_('Grab File...'), proxy.save_blob_dialog),
        ),
        'save_blob_from_parent': set_icon(
            icons.save(),
            qtutils.add_action(
                widget,
                N_('Grab File from Parent Commit...'),
                proxy.save_blob_from_parent_dialog,
            ),
        ),
        'search_line_range': set_icon(
            icons.search(),
            qtutils.add_action(
                widget,
                N_('Trace Evolution of Line Range...'),
                proxy.search_line_range,
            ),
        ),
        'copy': set_icon(
            icons.copy(),
            qtutils.add_action(
                widget,
                N_('Copy Commit'),
                proxy.copy_to_clipboard,
                hotkeys.COPY_COMMIT_ID,
            ),
        ),
        'copy_short': set_icon(
            icons.copy(),
            qtutils.add_action(
                widget,
                N_('Copy Commit (Short)'),
                proxy.copy_to_clipboard_short,
                hotkeys.COPY,
            ),
        ),
        'copy_title': set_icon(
            icons.copy(),
            qtutils.add_action(
                widget,
                N_('Copy Commit Title'),
                proxy.copy_title_to_clipboard,
                hotkeys.COPY_COMMIT_TITLE,
            ),
        ),
        'copy_oneline': set_icon(
            icons.copy(),
            qtutils.add_action(
                widget,
                N_('Copy Commit (Short) and Title'),
                proxy.copy_oneline_to_clipboard,
                hotkeys.COPY_COMMIT_ONELINE,
            ),
        ),
        'copy_message': set_icon(
            icons.copy(),
            qtutils.add_action(
                widget,
                N_('Copy Commit Message'),
                proxy.copy_message_to_clipboard,
                hotkeys.COPY_COMMIT_MESSAGE,
            ),
        ),
        'copy_link': set_icon(
            icons.copy(),
            qtutils.add_action(
                widget,
                N_('Copy Commit Link'),
                proxy.copy_link_to_clipboard,
                hotkeys.COPY_COMMIT_LINK,
            ),
        ),
        'copy_markdown_link': set_icon(
            icons.copy(),
            qtutils.add_action(
                widget,
                N_('Copy Commit Link (Markdown)'),
                proxy.copy_markdown_link_to_clipboard,
            ),
        ),
        'copy_message_unwrapped': set_icon(
            icons.copy(),
            qtutils.add_action(
                widget,
                N_('Copy Commit Message (Unwrapped)'),
                proxy.copy_message_unwrapped_to_clipboard,
            ),
        ),
    }


class GitDagLineEdit(completion.GitLogLineEdit):  # type: ignore[misc, valid-type]
    """The text input field for specifying "git log" options"""

    def __init__(self, context):
        super().__init__(context)
        self._action_filter_to_current_author = qtutils.add_action(
            self, N_('Commits authored by me'), self._filter_to_current_author
        )
        self._action_pickaxe_search = qtutils.add_action(
            self, N_('Pickaxe search for changes containing text'), self._pickaxe_search
        )
        self._action_grep_search = qtutils.add_action(
            self,
            N_('Search commit messages'),
            self._grep_search,
        )
        self._action_no_merges = qtutils.add_action(
            self, N_('Ignore merge commits'), self._no_merges
        )
        self._action_filter_lines = qtutils.add_action(
            self, N_('Filter commits by line range'), self._filter_to_line_range
        )

    def filter_actions(self):
        """Return the "git log" filter actions in display order"""
        return [
            self._action_pickaxe_search,
            self._action_filter_lines,
            self._action_filter_to_current_author,
            self._action_grep_search,
            self._action_no_merges,
        ]

    def contextMenuEvent(self, event):
        """Adds custom actions to the default context menu"""
        event_pos = event.pos()
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        actions = menu.actions()
        first_action = actions[0]
        for action in self.filter_actions():
            menu.insertAction(first_action, action)
        menu.insertSeparator(first_action)
        menu.exec_(self.mapToGlobal(event_pos))

    def insert(self, text):
        """Insert text at the beginning of the current text"""
        value = self.value()
        if value:
            text = f'{text} {value}'
        self.setText(text)
        self.close_popup()

    def _filter_to_current_author(self):
        """Filter to commits by the current author/user"""
        _, email = self.context.cfg.get_author()
        author_filter = '--author=' + email
        self.insert(author_filter)

    def _filter_to_line_range(self):
        """Filter to commits by line range expressions"""
        range_filter = '-L:funcname:filename'
        self.insert(range_filter)

    def _pickaxe_search(self):
        """Pickaxe search for changes containing text"""
        self.insert('-G"search"')
        start = len('-G"')
        length = len('search')
        self.setSelection(start, length)

    def _grep_search(self):
        """Pickaxe search for changes containing text"""
        self.insert('--grep="search"')
        start = len('--grep="')
        length = len('search')
        self.setSelection(start, length)

    def _no_merges(self):
        """Ignore merge commits"""
        self.insert('--no-merges')


PREFETCH_SETTLE_MSEC = 150
"""How long the selection has to stay put before its neighbours are prefetched"""


def neighbour_rows(row, previous_row, count, ahead=3, behind=1):
    """Return the rows worth prefetching around row, most likely first

    The next `ahead` rows in the direction of the last move (down, towards
    older commits, unless the selection moved up), then `behind` rows the
    other way. Rows outside 0..count-1 are skipped.
    """
    step = -1 if previous_row is not None and row < previous_row else 1
    candidates = [row + step * offset for offset in range(1, ahead + 1)]
    candidates += [row - step * offset for offset in range(1, behind + 1)]
    return [idx for idx in candidates if 0 <= idx < count]


GRAPH_ROW_ROLE = Qt.UserRole + 1
GRAPH_PREV_ROW_ROLE = Qt.UserRole + 2
COMMIT_ROLE = Qt.UserRole + 3

_REMOTES_PREFIX = 'remotes/'
_TAGS_PREFIX = 'tags/'
_HEADS_PREFIX = 'heads/'


class RefType(enum.Enum):
    LOCAL = 'local'
    REMOTE = 'remote'
    TAG = 'tag'
    OTHER = 'other'


def _parse_ref(ref: str) -> tuple[str, str | None, str | None, RefType]:
    """Return display properties for a decorated ref:

    - display_text - full text to display
    - condensed_text - what will be displayed in condensed mode (optional)
    - branch_name - the branch or tag name ref refers to (optional)
    - ref_type - RefType enum
    """
    if ref.startswith(_REMOTES_PREFIX):
        display_text = ref[len(_REMOTES_PREFIX) :]
        slash = display_text.find('/')
        remote_name = display_text[:slash]
        branch_name = display_text[slash + 1 :] if slash >= 0 else None
        return display_text, f'{remote_name}/\u2026', branch_name, RefType.REMOTE
    if ref.startswith(_TAGS_PREFIX):
        name = ref[len(_TAGS_PREFIX) :]
        return name, name, name, RefType.TAG
    if ref.startswith(_HEADS_PREFIX):
        name = ref[len(_HEADS_PREFIX) :]
        return name, name, name, RefType.LOCAL
    return ref, ref, None, RefType.OTHER


def _prepare_labels(refs: list[str]) -> tuple[tuple[str, str, str | None], ...]:
    """Decide which labels to condense and return (ref, display_text, condensed_text)

    A commit's refs are immutable once displayed, but this runs on every paint,
    size hint and hit test of a labelled row, so the result is memoised.
    """
    return _prepare_labels_cached(tuple(refs))


@functools.lru_cache(maxsize=512)
def _prepare_labels_cached(
    refs: tuple[str, ...],
) -> tuple[tuple[str, str, str | None], ...]:
    """Group, sort and condense a commit's ref labels.

    Refs are grouped into groups with the same branch name. Local branch (if any)
    is placed last. All refs within the group except the last are condensed to
    "remote/\u2026" (horizontal ellipsis).
    """

    # branch name -> (ref, is_local, display, condensed)
    groups: dict[
        str, list[tuple[str, bool, str, str | None]]
    ] = collections.defaultdict(list)

    non_group: list[tuple[str, str]] = []
    for ref in refs:
        if ref == 'HEAD':
            continue

        display, condensed, branch_name, ref_type = _parse_ref(ref)
        if ref_type in (RefType.OTHER, RefType.TAG) or branch_name is None:
            non_group.append((ref, display))
            continue
        groups[branch_name].append((ref, ref_type == RefType.LOCAL, display, condensed))

    # non grouped special refs go first
    result: list[tuple[str, str, str | None]] = []
    for ref, display in non_group:
        result.append((ref, display, None))

    for branch_name in sorted(groups.keys()):
        remotes = groups.get(branch_name, [])
        # sort by is_local, display -> local branch will always be last
        remotes.sort(key=lambda item: (item[1], item[2]))
        condense_count = len(remotes) - 1

        for i, (ref, _, display, condensed) in enumerate(remotes):
            result.append((ref, display, condensed if i < condense_count else None))

    return tuple(result)


# Which column shows each field find matches in memory, for the "matched in" badge.
FIND_FIELD_COLUMNS = {
    'summary': 'summary',
    'author': 'author',
    'email': 'email',
    'committer': 'committer',
    'committer_email': 'committer_email',
    'ref': 'refs',
    'oid': 'oid',
}


def find_field_names():
    """Badge text for each kind of match, keyed like matched_fields() and git"""
    return {
        'author': N_('author'),
        'email': N_('email'),
        'committer': N_('committer'),
        'committer_email': N_('committer email'),
        'ref': N_('ref'),
        'oid': N_('hash'),
        'message': N_('message'),
        'files': N_('files'),
    }


def matched_fields(commit, needle, case_sensitive=False):
    """The in-memory fields of commit that find matches, in display order"""
    if not needle:
        return []
    if case_sensitive:
        fold = _identity
    else:
        fold = str.lower
        needle = needle.lower()
    fields = []
    for key, value in (
        ('summary', commit.summary),
        ('author', commit.author),
        ('email', commit.email),
        ('committer', commit.committer),
        ('committer_email', commit.committer_email),
    ):
        if value and needle in fold(value):
            fields.append(key)
    if any(needle in fold(tag) for tag in commit.tags):
        fields.append('ref')
    if fold(commit.oid or '').startswith(needle):
        fields.append('oid')
    return fields


def _identity(value):
    return value


def match_spans(text, needle, case_sensitive=False):
    """(start, length) of each non-overlapping occurrence of needle in text"""
    if not text or not needle:
        return []
    if not case_sensitive:
        text = text.lower()
        needle = needle.lower()
    spans = []
    start = text.find(needle)
    while start >= 0:
        spans.append((start, len(needle)))
        start = text.find(needle, start + len(needle))
    return spans


class FindState:
    """What the find bar is looking for, as the commit list paints it"""

    # Translucent, so it reads as a highlighter mark over the text in light
    # and dark themes, and over the selection colour.
    highlight = QtGui.QColor(255, 200, 0, 110)

    def __init__(self, needle, case_sensitive=False, git_kinds=None, filtering=False):
        self.needle = needle
        self.case_sensitive = case_sensitive
        self.git_kinds = git_kinds or {}
        """oid -> {'message', 'files'}, the kinds git matched for that commit"""
        self.filtering = filtering

    def spans(self, text):
        return match_spans(text, self.needle, self.case_sensitive)

    def badge(self, commit, visible_columns):
        """Text naming the matched fields that the visible cells don't show"""
        fields = matched_fields(commit, self.needle, self.case_sensitive)
        kinds = [
            field
            for field in fields
            if FIND_FIELD_COLUMNS.get(field) not in visible_columns
        ]
        git_kinds = self.git_kinds.get(commit.oid, ())
        # The summary is the start of the message: only name the message when
        # the match is elsewhere in it.
        if 'message' in git_kinds and 'summary' not in fields:
            kinds.append('message')
        if 'files' in git_kinds:
            kinds.append('files')
        if not kinds:
            return ''
        names = find_field_names()
        return N_('in %s') % ', '.join(names[kind] for kind in kinds)

    def paint_spans(self, painter, text, text_x, rect, font_metrics):
        """Mark each match in text, drawn from text_x, within rect"""
        for start, length in self.spans(text):
            left = text_x + font_metrics.horizontalAdvance(text[:start])
            if left >= rect.right():
                break
            width = font_metrics.horizontalAdvance(text[start : start + length])
            mark = QtCore.QRect(
                int(left), rect.top() + 1, int(width), rect.height() - 2
            )
            painter.fillRect(mark.intersected(rect), self.highlight)

    @staticmethod
    def badge_rect(badge, rect, font_metrics):
        """Where the badge goes: the right end of rect"""
        width = font_metrics.horizontalAdvance(badge) + 10
        height = font_metrics.height()
        return QtCore.QRect(
            rect.right() - width - 4,
            rect.center().y() - height // 2,
            width,
            height,
        )

    def paint_badge(self, painter, badge, rect, option):
        """Draw the "in message, files" badge at the right end of rect"""
        badge_rect = self.badge_rect(badge, rect, option.fontMetrics)
        if badge_rect.left() <= rect.left():
            return
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.highlight)
        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(option.palette.text().color())
        painter.drawText(badge_rect, Qt.AlignCenter, badge)
        painter.restore()


class FindHighlightDelegate(QtWidgets.QStyledItemDelegate):
    """Paint a cell as usual, then mark the find bar's matches in its text"""

    def paint(self, painter, option, index):
        tree = self.parent()
        state = getattr(tree, 'find_state', None)
        if state is None:
            super().paint(painter, option, index)
            return
        # Without the inline graph the summary column paints here.
        badge = ''
        if index.column() == CommitTreeWidgetItem.SUMMARY:
            item = tree.itemFromIndex(index)
            if item is not None:
                badge = state.badge(item.commit, tree.visible_column_keys())
        cell = QtWidgets.QStyleOptionViewItem(option)
        if badge:
            # The text stops short of the badge instead of running under it.
            badge_rect = state.badge_rect(badge, option.rect, option.fontMetrics)
            if badge_rect.left() > option.rect.left():
                # The view has painted the row's background, but a selected
                # cell paints its own: carry it on under the badge.
                if option.state & QtWidgets.QStyle.State_Selected:
                    painter.fillRect(
                        QtCore.QRect(
                            badge_rect.left() - 4,
                            option.rect.top(),
                            option.rect.right() - badge_rect.left() + 5,
                            option.rect.height(),
                        ),
                        option.palette.highlight(),
                    )
                cell.rect = QtCore.QRect(option.rect)
                cell.rect.setRight(badge_rect.left() - 4)
        super().paint(painter, cell, index)
        text = index.data(Qt.DisplayRole)
        opt = QtWidgets.QStyleOptionViewItem(cell)
        self.initStyleOption(opt, index)
        style = tree.style() if tree is not None else QtWidgets.QApplication.style()
        text_rect = style.subElementRect(
            QtWidgets.QStyle.SE_ItemViewItemText, opt, tree
        )
        margin = (
            style.pixelMetric(QtWidgets.QStyle.PM_FocusFrameHMargin, None, tree) + 1
        )
        if text:
            painter.save()
            painter.setClipRect(text_rect)
            state.paint_spans(
                painter, text, text_rect.left() + margin, text_rect, option.fontMetrics
            )
            painter.restore()
        if badge:
            state.paint_badge(painter, badge, option.rect, option)


class GraphDelegate(QtWidgets.QStyledItemDelegate):
    LANE_WIDTH = 16
    DOT_RADIUS = 5
    EDGE_WIDTH = 3
    commit_color = QtGui.QColor(Qt.white)
    merge_color = QtGui.QColor(Qt.lightGray)
    head_color = QtGui.QColor(Qt.green)
    current_head_color = QtGui.QColor(255, 215, 0)  # Gold color. Plain yellow is too close to white
    outline_pen = QtGui.QPen()
    outline_pen.setWidth(2)
    outline_pen.setColor(QtGui.QColor(Qt.white).darker())

    other_color = QtGui.QColor(Qt.white)
    remote_color = QtGui.QColor(Qt.yellow)

    text_pen = QtGui.QPen()
    text_pen.setColor(QtGui.QColor(Qt.black))
    text_pen.setWidth(1)

    head_pen = QtGui.QPen()
    head_pen.setColor(QtGui.QColor(Qt.black))
    head_pen.setWidth(1)

    LABEL_BORDER = 3
    LABEL_SPACING = 4
    LABEL_TEXT_OFFSET = 2
    ANIMATION_DURATION = 50

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hover_item: object | None = None
        self._hover_label_idx: int = -1
        self._expand_progress: float = 0.0
        self._animation = QtCore.QVariantAnimation(self)
        self._animation.setDuration(self.ANIMATION_DURATION)
        self._animation.valueChanged.connect(self._on_animation_value)
        self._animation.finished.connect(self._on_animation_finished)

    def _on_animation_value(self, value):
        self._expand_progress = value
        parent = self.parent()
        if parent is not None:
            parent.viewport().update()

    def _on_animation_finished(self) -> None:
        if self._expand_progress == 0.0:
            self._hover_item = None
            self._hover_label_idx = -1

    def set_hover(self, item: object | None, label_idx: int) -> None:
        if item == self._hover_item and label_idx == self._hover_label_idx:
            return
        self._animation.stop()
        if label_idx >= 0 and item is not None:
            self._hover_item = item
            self._hover_label_idx = label_idx
            self._animation.setStartValue(0.0)
            self._animation.setEndValue(1.0)
            self._animation.start()
        else:
            self._animation.setStartValue(self._expand_progress)
            self._animation.setEndValue(0.0)
            self._animation.start()

    def paint(self, painter, option, index):
        row = index.data(GRAPH_ROW_ROLE)
        prev_row = index.data(GRAPH_PREV_ROW_ROLE)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setClipRect(option.rect)

        rect = option.rect
        mid_y = rect.center().y()
        top_y = rect.top()
        # +1 to connect to the next row (FlatCap ends lines exactly at the endpoint)
        bottom_y = rect.bottom() + 1
        lane_w = self.LANE_WIDTH

        if option.state & QtWidgets.QStyle.State_Selected:
            painter.fillRect(rect, option.palette.highlight())

        tree = self.parent()
        state = getattr(tree, 'find_state', None)
        # Hidden rows break the lanes, so a filtered list shows only the dots.
        filtering = state is not None and state.filtering

        # Draw the graph if we have graph data.
        if (row is not None or prev_row is not None) and not filtering:
            pen = QtGui.QPen()
            pen.setWidth(self.EDGE_WIDTH)
            pen.setCapStyle(Qt.FlatCap)

            colors = EdgeColor.colors
            lane_x = rect.left() + lane_w // 2
            # A wide history passes a hundred or more lanes through each row.
            # Their straight segments are drawn with one drawLines() per color
            # rather than a pen change and drawLine() each.
            batched = {}

            # Top half: edges from the previous row arrive vertically.
            if prev_row is not None:
                for edge in prev_row.edges_to_parent:
                    to_x = lane_x + edge.to_column * lane_w
                    batched.setdefault(edge.color_index % len(colors), []).append(
                        QtCore.QLine(to_x, top_y, to_x, mid_y)
                    )

            # Bottom half: the pass-through lanes come first and are straight.
            # The rest are drawn one by one, in order, as before.
            edges = row.edges_to_parent if row is not None else []
            first_unbatched = len(edges)
            for position, edge in enumerate(edges):
                if edge.from_column != edge.to_column:
                    first_unbatched = position
                    break
                x_val = lane_x + edge.from_column * lane_w
                batched.setdefault(edge.color_index % len(colors), []).append(
                    QtCore.QLine(x_val, mid_y, x_val, bottom_y)
                )
            for color_index, lines in batched.items():
                pen.setColor(colors[color_index])
                painter.setPen(pen)
                painter.drawLines(lines)

            # Bottom half: straight or spline depending on diagonal.
            if row is not None:
                for edge in edges[first_unbatched:]:
                    color = colors[edge.color_index % len(colors)]
                    pen.setColor(color)
                    painter.setPen(pen)
                    from_x = lane_x + edge.from_column * lane_w
                    to_x = lane_x + edge.to_column * lane_w
                    if edge.from_column == edge.to_column:
                        painter.drawLine(from_x, mid_y, to_x, bottom_y)
                    else:
                        path = QtGui.QPainterPath()
                        path.moveTo(from_x, mid_y)
                        path.cubicTo(
                            from_x,
                            bottom_y,
                            to_x,
                            mid_y,
                            to_x,
                            bottom_y,
                        )
                        painter.drawPath(path)

        if row is not None:
            if not filtering:
                cx = rect.left() + row.commit_column * lane_w + lane_w // 2
            else:
                cx = rect.left() + lane_w // 2
            color_map = {
                GraphRowColor.NORMAL: self.commit_color,
                GraphRowColor.MERGE: self.merge_color,
                GraphRowColor.HEAD: self.current_head_color,
            }
            painter.setPen(self.outline_pen)
            painter.setBrush(color_map[row.color])
            painter.drawEllipse(
                QtCore.QPointF(cx, mid_y), self.DOT_RADIUS, self.DOT_RADIUS
            )

        commit = index.data(COMMIT_ROLE)

        label_x = rect.left() + self._graph_width(row, prev_row) + 8
        labels_width = 0

        if commit and commit.tags:
            painter.setFont(option.font)
            item = tree.itemFromIndex(index) if tree else None
            labels_width = self._draw_labels(
                painter, mid_y, commit.tags, label_x, option.fontMetrics, item
            )

        badge = ''
        if state is not None and commit is not None:
            badge = state.badge(commit, tree.visible_column_keys())
        text = index.data(Qt.DisplayRole)
        if text:
            text_x = int(label_x + labels_width + 8)
            text_rect = rect.adjusted(text_x - rect.left(), 0, 0, 0)
            if badge:
                # The text stops short of the badge instead of running under it.
                badge_rect = state.badge_rect(badge, rect, option.fontMetrics)
                text_rect.setRight(max(text_rect.left(), badge_rect.left() - 4))
            if state is not None:
                state.paint_spans(painter, text, text_x, text_rect, option.fontMetrics)
            painter.setPen(option.palette.text().color())
            painter.drawText(
                text_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                text,
            )

        if badge:
            state.paint_badge(painter, badge, rect, option)

        painter.restore()

    def _get_spacing(self, condensed_text: str | None) -> int:
        if condensed_text is not None:
            return 0
        return self.LABEL_SPACING

    def _draw_labels(
        self,
        painter: QtGui.QPainter | None,
        y: int,
        tags: list[str],
        start_x: int,
        font_metrics: QtGui.QFontMetrics,
        item: object | None,
    ):
        """Draw branch/tag labels and return total width used."""
        current_x = start_x
        x_offset = self.LABEL_TEXT_OFFSET
        y_offset = 0

        for i, (tag, display_text, condensed_text) in enumerate(_prepare_labels(tags)):
            pen = self.text_pen
            brush = self.other_color

            if tag == 'HEAD':
                brush = self.remote_color
            elif tag.startswith(_REMOTES_PREFIX):
                pass
            elif tag.startswith(_TAGS_PREFIX):
                brush = self.remote_color
            elif tag.startswith(_HEADS_PREFIX):
                pen = self.head_pen
                brush = self.head_color

            if painter is not None:
                painter.setPen(pen)
                painter.setBrush(brush)

            shown, text_width = self._label_shown_text(
                condensed_text, display_text, font_metrics, item, i
            )
            # Use 80% of font height for tighter vertical fit
            text_height = font_metrics.height() * 0.8

            text_rect = QtCore.QRectF(
                current_x, y - text_height / 2, text_width, text_height
            )

            box_rect = text_rect.adjusted(-x_offset, -y_offset, x_offset, y_offset)

            if painter is not None:
                painter.drawRoundedRect(box_rect, self.LABEL_BORDER, self.LABEL_BORDER)
                painter.save()
                painter.setClipRect(box_rect)
                painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, shown)
                painter.restore()

            current_x += text_width + x_offset * 2 + self._get_spacing(condensed_text)

        return current_x - start_x

    def _labels_width(self, font_metrics: QtGui.QFontMetrics, tags: list[str]):
        """Calculate total width needed for all labels."""
        return self._draw_labels(None, 0, tags, 0, font_metrics, None)

    def _label_shown_text(
        self,
        condensed_text: str | None,
        display_text: str,
        font_metrics: QtGui.QFontMetrics,
        item: object | None,
        label_idx: int,
    ) -> tuple[str, int]:
        """Return (text_to_draw, pixel_width) for a label"""
        if not condensed_text:
            return display_text, font_metrics.horizontalAdvance(display_text)
        is_hovered = (
            item is not None
            and item is self._hover_item
            and label_idx == self._hover_label_idx
        )
        if not is_hovered or self._expand_progress == 0.0:
            return condensed_text, font_metrics.horizontalAdvance(condensed_text)
        condensed_w = font_metrics.horizontalAdvance(condensed_text)
        full_w = font_metrics.horizontalAdvance(display_text)
        width = condensed_w + (full_w - condensed_w) * self._expand_progress
        return display_text, width

    def _graph_width(self, row, prev_row):
        """Calculate the width needed for the graph."""
        # A filtered list draws each commit's dot in the first lane only.
        state = getattr(self.parent(), 'find_state', None)
        if state is not None and state.filtering:
            return self.LANE_WIDTH
        if row is None and prev_row is None:
            return 0
        max_col = 0
        if row is not None:
            max_col = max(max_col, row.commit_column)
            for edge in row.edges_to_parent:
                max_col = max(max_col, edge.from_column, edge.to_column)
        if prev_row is not None:
            for edge in prev_row.edges_to_parent:
                max_col = max(max_col, edge.from_column, edge.to_column)
        return (max_col + 1) * self.LANE_WIDTH

    def sizeHint(self, option, index):
        graph_row = index.data(GRAPH_ROW_ROLE)
        prev_row = index.data(GRAPH_PREV_ROW_ROLE)
        graph_width = self._graph_width(graph_row, prev_row)

        commit = index.data(COMMIT_ROLE)

        labels_width = 0
        if commit and commit.tags:
            labels_width = self._labels_width(option.fontMetrics, commit.tags)

        # Add space for text if present.
        text = index.data(Qt.DisplayRole)
        if text:
            text_width = option.fontMetrics.horizontalAdvance(text) + 16
        else:
            text_width = 0

        total_width = graph_width + 8 + labels_width + 8 + text_width
        total_width = max(total_width, self.LANE_WIDTH * 4)
        height = option.fontMetrics.height()
        return QtCore.QSize(total_width, height)

    def _label_hit_test(
        self,
        pos,
        rect: QtCore.QRectF,
        font_metrics: QtGui.QFontMetrics,
        index: int,
        item: object | None,
    ) -> tuple[int, bool]:
        """Return (index, is_condensed) if pos is over a label, else (-1, False)."""
        commit = index.data(COMMIT_ROLE)
        if not commit or not commit.tags:
            return -1, False
        row = index.data(GRAPH_ROW_ROLE)
        prev_row = index.data(GRAPH_PREV_ROW_ROLE)
        x_offset = self.LABEL_TEXT_OFFSET
        current_x = rect.left() + self._graph_width(row, prev_row) + 8
        mid_y = rect.center().y()
        text_height = font_metrics.height()
        for i, (_, display_text, condensed_text) in enumerate(
            _prepare_labels(commit.tags)
        ):
            _, text_width = self._label_shown_text(
                condensed_text, display_text, font_metrics, item, i
            )
            box_left = current_x - x_offset
            box_right = current_x + text_width + x_offset
            box_top = mid_y - text_height / 2
            box_bottom = mid_y + text_height / 2
            if box_left <= pos.x() <= box_right and box_top <= pos.y() <= box_bottom:
                return i, condensed_text is not None
            current_x += text_width + x_offset * 2 + self._get_spacing(condensed_text)
        return -1, False

    def update_label_hover(
        self,
        pos,
        rect: QtCore.QRectF,
        font_metrics: QtGui.QFontMetrics,
        index: int,
        item: object | None,
    ) -> None:
        if item is None:
            self.set_hover(None, -1)
            return
        label_idx, is_condensed = self._label_hit_test(
            pos, rect, font_metrics, index, item
        )
        if label_idx >= 0 and is_condensed is not None:
            self.set_hover(item, label_idx)
        else:
            self.set_hover(None, -1)


def date_formatter(context):
    """Return a function that renders a commit's author date for display

    The preferences and the current time are read once so that formatting a
    large number of commits does not repeat the same lookups per row.
    """
    mode = prefs.dag_date_mode(context)
    custom_format = prefs.dag_date_format(context)
    pretty = prefs.dag_date_pretty(context)
    now = datetime.datetime.now()

    def format_date(timestamp, git_date):
        return dates.format_timestamp(
            timestamp,
            mode,
            custom_format,
            pretty,
            git_date=git_date or '',
            now=now,
        )

    return format_date


class CommitColumn:
    """A column in the commit list

    ``getter`` is called with a commit and the renderer that holds the
    pre-computed display preferences, and returns the cell's text.
    """

    def __init__(self, key, label, getter, visible=False, required=False, width=0):
        self.key = key
        self.label = label
        self.getter = getter
        self.visible = visible
        self.required = required
        """Required columns cannot be hidden"""
        self.width = width
        """The width used the first time the column is revealed"""


class CommitRenderer:
    """Display preferences shared by every cell in a single refresh"""

    def __init__(self, context):
        self.format_date = date_formatter(context)
        self.abbrev = prefs.abbrev(context)


def commit_columns():
    """Return the commit list columns in their default order

    The summary must stay first: the inline graph delegate and the graph row
    data are both attached to the first column.
    """
    return [
        CommitColumn(
            'summary',
            N_('Summary'),
            lambda commit, renderer: commit.summary,
            visible=True,
            required=True,
        ),
        CommitColumn(
            'author',
            N_('Author'),
            lambda commit, renderer: commit.author,
            visible=True,
        ),
        CommitColumn(
            'date',
            N_('Date, Time'),
            lambda commit, renderer: renderer.format_date(
                commit.timestamp, commit.authdate
            ),
            visible=True,
        ),
        CommitColumn(
            'oid',
            N_('Hash'),
            lambda commit, renderer: commit.oid[: renderer.abbrev],
            width=defs.scale(96),
        ),
        CommitColumn(
            'refs',
            N_('Refs'),
            lambda commit, renderer: ' '.join(commit.tags),
            width=defs.scale(160),
        ),
        CommitColumn(
            'email',
            N_('Author Email'),
            lambda commit, renderer: commit.email,
            width=defs.scale(200),
        ),
        CommitColumn(
            'committer',
            N_('Committer'),
            lambda commit, renderer: commit.committer,
            width=defs.scale(140),
        ),
        CommitColumn(
            'committer_email',
            N_('Committer Email'),
            lambda commit, renderer: commit.committer_email,
            width=defs.scale(200),
        ),
        CommitColumn(
            'commitdate',
            N_('Commit Date, Time'),
            lambda commit, renderer: renderer.format_date(
                commit.committer_timestamp, commit.commitdate
            ),
            width=defs.scale(160),
        ),
    ]


class CommitTreeWidgetItem(QtWidgets.QTreeWidgetItem):
    """Custom TreeWidgetItem used in to build the commit tree widget"""

    SUMMARY = 0
    AUTHOR = 1
    DATE = 2

    def __init__(self, commit, columns, renderer, parent=None):
        """Populate the (index, column) pairs in ``columns`` for ``commit``

        Hidden columns are not passed in; their text is filled in later if the
        user reveals them, which keeps refreshes cheap on large histories.
        """
        QtWidgets.QTreeWidgetItem.__init__(self, parent)
        self.commit = commit
        self.position = 0
        """Index of the commit in CommitTreeWidget.commits, which gives its row"""
        for idx, column in columns:
            self.setText(idx, column.getter(commit, renderer) or '')


class CommitTreeWidget(standard.TreeWidget, ViewerMixin):
    """Display commits using a flat treewidget in "list" mode"""

    commits_selected = Signal(object)
    diff_commits = Signal(object, object)
    search_line_range_in_oid = Signal(object)
    zoom_to_fit = Signal()
    commits_changed = Signal()
    """Emitted when rows are added or the tree is cleared"""

    def __init__(self, context, parent):
        standard.TreeWidget.__init__(self, parent)
        ViewerMixin.__init__(self)

        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        # Every row is a single line of text in the same font, so all rows are
        # the same height. Telling Qt this lets it size the view from one row
        # instead of calling the delegate's sizeHint() for every commit, which
        # is a large saving on big histories.
        self.setUniformRowHeights(True)

        self.columns = commit_columns()
        self.setHeaderLabels([column.label for column in self.columns])
        header = self.header()
        # The last visible section stretches so that the columns always fill the
        # view, whichever columns the user has chosen to display.
        header.setStretchLastSection(True)
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self.show_header_menu)
        for idx, column in enumerate(self.columns):
            self.setColumnHidden(idx, not column.visible)

        self.graph_delegate = GraphDelegate(self)
        self.setItemDelegate(FindHighlightDelegate(self))
        self.find_state = None
        """The find bar's FindState while it has text, for highlighting"""
        self._hidden_positions = set()
        """Positions in self.commits of the rows the find bar hides"""
        self.context = context
        self.oidmap = {}
        # Full commit messages shown as hover tooltips, keyed by oid. Tooltip
        # events run git synchronously on the GUI thread, so each message is
        # read once and kept until the tree is cleared.
        self._message_tooltips = {}
        self.menu_actions = None
        self.selecting = False
        self.commits = []
        self._selected_oids = frozenset()
        """The oids this list shows as selected, to skip the echo of its own selection"""
        self._column_init_state = ColumnInitState.NONE
        self.action_up = qtutils.add_action(
            self, N_('Go Up'), self.go_up, hotkeys.MOVE_UP
        )

        self.action_down = qtutils.add_action(
            self, N_('Go Down'), self.go_down, hotkeys.MOVE_DOWN
        )

        self.zoom_to_fit_action = qtutils.add_action(
            self, N_('Zoom to Fit'), self.zoom_to_fit.emit, hotkeys.FIT
        )

        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        self.itemSelectionChanged.connect(
            self.selection_changed, type=Qt.QueuedConnection
        )

    def visible_columns(self):
        """Return the (index, column) pairs for the columns being displayed"""
        return [
            (idx, column)
            for idx, column in enumerate(self.columns)
            if not self.isColumnHidden(idx)
        ]

    def visible_column_keys(self):
        """The keys of the columns being displayed"""
        return {column.key for _idx, column in self.visible_columns()}

    def set_find_state(self, state):
        """Highlight the find bar's matches, or stop with None"""
        self.find_state = state
        self.viewport().update()

    def show_only_rows(self, rows):
        """Hide every row not in rows, or show them all again with None"""
        last = len(self.commits) - 1
        if rows is None:
            hidden = set()
        else:
            hidden = set(range(len(self.commits))).difference(
                last - row for row in rows
            )
        to_hide = hidden - self._hidden_positions
        to_show = self._hidden_positions - hidden
        if not to_hide and not to_show:
            return
        root = QtCore.QModelIndex()
        for position in to_hide:
            self.setRowHidden(last - position, root, True)
        for position in to_show:
            self.setRowHidden(last - position, root, False)
        self._hidden_positions = hidden

    def render_commits(self, oids):
        """Re-render the visible cells of the commits, e.g. after their labels changed"""
        renderer = CommitRenderer(self.context)
        columns = self.visible_columns()
        for oid in oids:
            item = self.oidmap.get(oid)
            if item is not None:
                for idx, column in columns:
                    item.setText(idx, column.getter(item.commit, renderer) or '')
        self.viewport().update()

    def show_header_menu(self, point):
        """Present the column visibility menu when the header is right-clicked"""
        menu = QtWidgets.QMenu(self)
        menu.setTitle(N_('Columns'))
        for idx, column in enumerate(self.columns):
            action = menu.addAction(column.label)
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(idx))
            action.setEnabled(not column.required)
            action.toggled.connect(partial(self.set_column_visible, idx))
        menu.exec_(self.header().mapToGlobal(point))

    def set_column_visible(self, idx, visible):
        """Show or hide a column, filling in its text the first time it is shown"""
        if visible == (not self.isColumnHidden(idx)):
            return
        column = self.columns[idx]
        if visible:
            renderer = CommitRenderer(self.context)
            for item_idx in range(self.topLevelItemCount()):
                item = self.topLevelItem(item_idx)
                if item is not None:
                    item.setText(idx, column.getter(item.commit, renderer) or '')
        self.setColumnHidden(idx, not visible)
        # A column that has never been displayed has no width of its own.
        if visible and column.width and self.columnWidth(idx) == 0:
            self.setColumnWidth(idx, column.width)

    def column_order(self):
        """Return the column keys in the order they are displayed"""
        header = self.header()
        order = sorted(
            range(len(self.columns)), key=lambda idx: header.visualIndex(idx)
        )
        return [self.columns[idx].key for idx in order]

    def set_column_order(self, keys):
        """Restore the display order of the columns"""
        header = self.header()
        indexes = {column.key: idx for idx, column in enumerate(self.columns)}
        for visual_index, key in enumerate(keys):
            idx = indexes.get(key)
            if idx is None:
                continue
            current = header.visualIndex(idx)
            if current != -1 and current != visual_index:
                header.moveSection(current, visual_index)

    def export_state(self):
        """Export the widget's state"""
        # The base class method is intentionally overridden because we only
        # care about the details below for this sub-widget.
        state = {}
        state['column_widths'] = self.column_widths()
        state['column_visibility'] = {
            column.key: not self.isColumnHidden(idx)
            for idx, column in enumerate(self.columns)
        }
        state['column_order'] = self.column_order()
        return state

    def apply_state(self, state):
        """Apply the exported widget state"""
        visibility = state.get('column_visibility') or {}
        for idx, column in enumerate(self.columns):
            if column.required:
                continue
            visible = visibility.get(column.key)
            if visible is not None:
                self.setColumnHidden(idx, not visible)
        column_order = state.get('column_order')
        if column_order:
            self.set_column_order(column_order)

        try:
            column_widths = state['column_widths']
        except (KeyError, ValueError):
            column_widths = None
        if column_widths:
            # The last visible section stretches, so Qt overrides its width.
            self.set_column_widths(column_widths)
            if prefs.dag_sticky_columns(self.context):
                # Sticky columns keep the saved widths verbatim; skip the
                # resize-to-contents pass that runs once the graph is loaded.
                self._column_init_state = ColumnInitState.COMPLETE
            else:
                self._column_init_state = ColumnInitState.SHOW_EVENT
        return True

    # Qt overrides
    def showEvent(self, event):
        """Override QWidget::showEvent() to size columns when we are shown"""
        standard.TreeWidget.showEvent(self, event)
        # Defer resizing columns until the widget has been shown so that width() returns
        # the correct value.
        if self._column_init_state < ColumnInitState.SHOW_EVENT:
            self._column_init_state = ColumnInitState.SHOW_EVENT
            width = self.header().width()
            one_half = width // 2
            one_quarter = width // 4
            # Set initial SUMMARY column width; it will be adjusted when graph loads.
            self.setColumnWidth(CommitTreeWidgetItem.SUMMARY, one_half)
            self.setColumnWidth(CommitTreeWidgetItem.AUTHOR, one_quarter)

    def display_inline_graph(self, enabled):
        """Enable and disable the display of inline graph in the commit list"""
        if enabled:
            delegate = self.graph_delegate
        else:
            delegate = None
        self.setItemDelegateForColumn(CommitTreeWidgetItem.SUMMARY, delegate)

    # ViewerMixin
    def go_up(self):
        """Select the item above the current item"""
        self.goto(self.itemAbove)

    def go_down(self):
        """Select the item below the current item"""
        self.goto(self.itemBelow)

    def goto(self, finder_fn):
        """Move the selection using a finder strategy"""
        items = self.selected_items()
        item = items[0] if items else None
        if item is None:
            return
        found = finder_fn(item)
        if found:
            self.select([found.commit.oid])

    def go_top(self):
        """Select the first (newest) commit"""
        count = self.topLevelItemCount()
        if count:
            self.select([self.topLevelItem(0).commit.oid])

    def go_bottom(self):
        """Select the last (oldest) commit"""
        count = self.topLevelItemCount()
        if count:
            self.select([self.topLevelItem(count - 1).commit.oid])

    def keyPressEvent(self, event):
        """Cmd+Up/Cmd+Down select the first/last commit on macOS

        Qt treats Ctrl (Cmd) with the arrows as "move the focus without
        selecting", so the selection and diff stayed put. Other platforms keep
        that behaviour. macOS tags arrow keys with KeypadModifier, so ignore it.
        """
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if core.IS_DARWIN and modifiers == Qt.ControlModifier:
            key = event.key()
            if key == Qt.Key_Up:
                self.go_top()
                event.accept()
                return
            if key == Qt.Key_Down:
                self.go_bottom()
                event.accept()
                return
        super().keyPressEvent(event)

    def selected_commit_range(self):
        """Return a range of selected commits"""
        selected_items = self.selected_items()
        if not selected_items:
            return None, None
        return selected_items[-1].commit.oid, selected_items[0].commit.oid

    def set_selecting(self, selecting):
        """Record the  "are we selecting?" status"""
        self.selecting = selecting

    def selection_changed(self):
        """Respond to itemSelectionChanged notifications"""
        items = self.selected_items()
        commits = sort_by_generation([i.commit for i in items]) if items else []
        self._selected_oids = frozenset(commit.oid for commit in commits)
        self.set_selecting(True)
        self.commits_selected.emit(commits)
        self.set_selecting(False)

    def select_commits(self, commits):
        """Select commits that were selected by the sibling tree/graph widget"""
        # Every selection comes back here through GitDAG.commits_selected,
        # including this list's own. The connections are queued, so the
        # "selecting" flag is already False by then: compare the oids instead.
        if self.selecting or self._selected_oids == {c.oid for c in commits}:
            return
        with qtutils.BlockSignals(self):
            self.select([commit.oid for commit in commits])

    def select(self, oids):
        """Mark items as selected"""
        items = [self.oidmap[oid] for oid in oids if oid in self.oidmap]
        self._selected_oids = frozenset(item.commit.oid for item in items)
        if not items:
            self.clearSelection()
            return
        # Select runs of rows as ranges in a single call. setSelected() per
        # item merged each row into the selection model, which is quadratic:
        # 2.5 s for 5000 commits and 389 s for 50000. Rows are in reverse
        # order of self.commits (add_commits() inserts each chunk at the top).
        last = len(self.commits) - 1
        model = self.model()
        selection = QtCore.QItemSelection()
        rows = sorted({last - item.position for item in items})
        for start, end in row_runs(rows):
            selection.select(model.index(start, 0), model.index(end, 0))
        selection_model = self.selectionModel()
        selection_model.select(
            selection,
            QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
        )
        # Move the current index (and with it the Shift+click anchor) to the
        # first commit, so arrow keys continue from the new selection.
        selection_model.setCurrentIndex(
            model.index(last - items[0].position, 0),
            QtCore.QItemSelectionModel.NoUpdate,
        )
        self.scrollToItem(items[-1])

    def clear(self):
        """Clear the tree"""
        QtWidgets.QTreeWidget.clear(self)
        self.oidmap.clear()
        self._message_tooltips.clear()
        self.commits = []
        self._selected_oids = frozenset()
        self._hidden_positions = set()
        self.commits_changed.emit()

    def event(self, event):
        """Show the full commit message when hovering a row"""
        if event.type() == QtCore.QEvent.ToolTip:
            pos = event.pos()
            item = self.itemAt(pos)
            if item is None:
                return super().event(event)
            text = self.message_tooltip(item.commit)
            if text:
                QtWidgets.QToolTip.showText(event.globalPos(), text, self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().event(event)

    def message_tooltip(self, commit):
        """Return the cached tooltip markup for a commit's full message"""
        oid = commit.oid
        if oid in (dag.STAGE, dag.WORKTREE):
            return ''
        try:
            return self._message_tooltips[oid]
        except KeyError:
            pass
        message = commit_message(self.context, oid)
        text = self._message_tooltips[oid] = format_message_tooltip(message)
        return text

    def add_commits(self, commits, graph_rows=None):
        """Add commits to the tree

        graph_rows is a list of (row, previous_row) pairs for these commits from
        one commit_graph() over the whole list (see ReaderThread). Without it the
        graph is built for these commits alone, which only joins up with commits
        added before or after when they are added together.
        """
        base = len(self.commits)
        self.commits.extend(commits)
        items = []
        renderer = CommitRenderer(self.context)
        columns = self.visible_columns()
        for offset in range(len(commits) - 1, -1, -1):
            commit = commits[offset]
            item = CommitTreeWidgetItem(commit, columns, renderer)
            item.position = base + offset
            items.append(item)
            self.oidmap[commit.oid] = item
            for tag in commit.tags:
                self.oidmap[tag] = item

        if graph_rows is None:
            graph_rows = graph_row_pairs(commit_graph(commits).rows)
        # Before the items join the tree: setData() on an item in a tree
        # notifies the view on every call, about 5 us each and three per row.
        self.apply_graph_rows(graph_rows)
        self.insertTopLevelItems(0, items)
        # Resize column to fit content after graph data is loaded.
        if self._column_init_state < ColumnInitState.GRAPH:
            self._column_init_state = ColumnInitState.GRAPH
            if not prefs.dag_sticky_columns(self.context):
                self.resizeColumnToContents(CommitTreeWidgetItem.SUMMARY)
        self.commits_changed.emit()

    def apply_graph_result(self, graph_result) -> None:
        """Attach the graph rows to the items of the commits just added"""
        self.apply_graph_rows(graph_row_pairs(graph_result.rows))

    def apply_graph_rows(self, graph_rows) -> None:
        """Attach (row, previous_row) pairs to the items of their commits"""
        # Resolve the rows through the oid->item map instead of rescanning
        # every tree item.
        for row, prev_row in graph_rows:
            item = self.oidmap.get(row.commit_oid)
            if item is not None:
                item.setData(CommitTreeWidgetItem.SUMMARY, GRAPH_ROW_ROLE, row)
                item.setData(CommitTreeWidgetItem.SUMMARY, COMMIT_ROLE, item.commit)
                if prev_row is not None:
                    item.setData(
                        CommitTreeWidgetItem.SUMMARY,
                        GRAPH_PREV_ROW_ROLE,
                        prev_row,
                    )

    def create_patch(self):
        """Export a patch from the selected items"""
        items = self.selectedItems()
        if not items:
            return
        context = self.context
        oids = [item.commit.oid for item in reversed(items)]
        all_oids = [commit.oid for commit in self.commits]
        cmds.do(cmds.FormatPatch, context, oids, all_oids)

    # Qt overrides
    def contextMenuEvent(self, event):
        """Create a custom context menu and execute it"""
        self.context_menu_event(event)

    def mousePressEvent(self, event):
        """Intercept the right-click event to retain selection state"""
        item = self.itemAt(event.pos())
        if item is None:
            self.clicked = None
        else:
            self.clicked = item.commit
        if event.button() == Qt.RightButton:
            event.accept()
            return
        QtWidgets.QTreeWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        pos = event.pos()
        item = self.itemAt(pos)
        rect = self.visualItemRect(item) if item is not None else QtCore.QRectF()
        index = (
            self.indexFromItem(item, CommitTreeWidgetItem.SUMMARY)
            if item is not None
            else -1
        )
        self.graph_delegate.update_label_hover(
            pos, rect, self.fontMetrics(), index, item
        )
        QtWidgets.QTreeWidget.mouseMoveEvent(self, event)

    def leaveEvent(self, event):
        self.graph_delegate.set_hover(None, -1)
        QtWidgets.QTreeWidget.leaveEvent(self, event)


# How long typing must pause before the find bar asks git about full messages
# and changed files, and the shortest text worth asking about.
FIND_GIT_DELAY_MS = 250
FIND_GIT_MIN_CHARS = 2


def commit_haystack(commit):
    """The in-memory text that find searches: summary, people and refs

    Fields are joined with NUL, which a needle never contains, so a match
    can't span two fields.
    """
    fields = [
        commit.summary,
        commit.author,
        commit.email,
        commit.committer,
        commit.committer_email,
    ]
    fields.extend(commit.tags)
    return '\0'.join(field for field in fields if field)


def commit_matches(commit, needle, case_sensitive=False):
    """Does the summary, author, committer, email, a ref or an oid prefix match?"""
    if not needle:
        return False
    haystack = commit_haystack(commit)
    oid = commit.oid or ''
    if not case_sensitive:
        needle = needle.lower()
        haystack = haystack.lower()
        oid = oid.lower()
    return oid.startswith(needle) or needle in haystack


def find_matches(commits, needle, case_sensitive=False):
    """Return the indices of the commits that match, in the given order"""
    return [
        idx
        for idx, commit in enumerate(commits)
        if commit_matches(commit, needle, case_sensitive)
    ]


def file_pathspec(needle, case_sensitive=False):
    """A pathspec matching any path that contains needle"""
    escaped = ''.join('\\' + char if char in '\\*?[]' else char for char in needle)
    magic = 'glob' if case_sensitive else 'icase,glob'
    return f':({magic})**/*{escaped}*'


def find_git_args(needle, kind, case_sensitive=False):
    """The "git log" arguments that find commits by full message or changed path"""
    args = ['log', '--no-walk=unsorted', '--stdin', '--format=%H']
    if kind == 'message':
        args += ['--fixed-strings', '--grep=' + needle]
        if not case_sensitive:
            args.append('--regexp-ignore-case')
    else:
        args += ['--', file_pathspec(needle, case_sensitive)]
    return args


class GitFindSearch:
    """One find-bar search by git, which the GUI thread can stop

    Only the given commits are read (--stdin with --no-walk), so the cost
    follows the length of the list, not the history: about 20 ms for 1000
    commits by message and 300 ms by file, but 11 s by file for 50000. A
    search the find bar no longer needs is stopped rather than left to run.
    """

    def __init__(self, context, oids, needle, kind, case_sensitive=False):
        self.context = context
        self.oids = oids
        self.needle = needle
        self.kind = kind
        self.case_sensitive = case_sensitive
        self.cancelled = False
        self._proc = None
        self._lock = threading.Lock()

    def run(self):
        """Return the oids whose full message (kind 'message') or paths match"""
        if not self.oids or not self.needle:
            return set()
        cmd = ['git'] + find_git_args(self.needle, self.kind, self.case_sensitive)
        with self._lock:
            if self.cancelled:
                return set()
            try:
                self._proc = proc = core.start_command(cmd, stderr=subprocess.DEVNULL)
            except OSError:
                return set()
        stdin = ('\n'.join(self.oids) + '\n').encode('utf-8')
        out, _ = proc.communicate(stdin)
        if self.cancelled or proc.returncode != 0:
            return set()
        return set(core.decode(out).split())

    def cancel(self):
        """Stop the search, killing its git process if it has started"""
        with self._lock:
            self.cancelled = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()


def find_commits_with_git(context, oids, needle, kind, case_sensitive=False):
    """Return the oids, from oids, whose full message or changed paths match"""
    return GitFindSearch(context, oids, needle, kind, case_sensitive).run()


def next_match(matches, row, backwards=False, inclusive=False):
    """Return the match after (or before) row, wrapping around the ends

    matches is a sorted list of row indices. With inclusive=True a match on
    row itself is returned, so find-as-you-type stays put while the current
    row still matches. Returns None when there are no matches.
    """
    if not matches:
        return None
    if backwards:
        if inclusive:
            idx = bisect.bisect_right(matches, row) - 1
        else:
            idx = bisect.bisect_left(matches, row) - 1
        return matches[idx]  # -1 wraps around to the last match
    if inclusive:
        idx = bisect.bisect_left(matches, row)
    else:
        idx = bisect.bisect_right(matches, row)
    if idx >= len(matches):
        idx = 0
    return matches[idx]


class CommitFindWidget(QtWidgets.QWidget):
    """The find bar that jumps between matching commits in the commit list

    Summaries, authors, committers, emails, refs and oid prefixes are matched
    in memory as you type. Full messages and changed file paths are not in
    memory, so once typing pauses git searches the listed commits for them in
    the background, and its matches join the others as they arrive.
    """

    def __init__(self, tree, parent):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self._tree = tree
        self._matches = None
        """Cached row indices of matching commits, None when stale"""
        self._haystacks = None
        """commit_haystack() per commit in tree.commits order, None when stale"""
        self._lower_haystacks = None
        self._text_search = None
        """(needle, case_sensitive, indices into tree.commits) of the last scan"""
        self._git_oids = set()
        """Oids git matched for the current criteria"""
        self._matched_kinds = {}
        """oid -> the kinds ('message', 'files') git matched it by"""
        self._git_pending = 0
        self._searches = []
        """The GitFindSearch objects of the current criteria, stopped when stale"""
        self._generation = 0
        """Bumped whenever the criteria or the commits change, to drop stale results"""
        self._origin_row = -1
        """The current row when the text last changed, where matches are counted from"""
        self._jumped_oid = None
        """The commit find-as-you-type selected, None once the user moves away"""
        self._return_focus = None
        """The widget that had focus before the find bar, refocused on Esc"""
        self._runtask = qtutils.RunTask(parent=self)
        self._git_timer = QtCore.QTimer(self)
        self._git_timer.setSingleShot(True)
        self._git_timer.setInterval(FIND_GIT_DELAY_MS)
        self._git_timer.timeout.connect(self._start_git_search)

        self.text = HintedDefaultLineEdit(N_('Find commits'), parent=self)
        self.text.setToolTip(
            N_(
                'Find commits by summary, message, author, committer, email, '
                'ref, commit ID or changed file'
            )
        )
        self.prev_button = qtutils.create_action_button(
            tooltip=N_('Find the previous matching commit'), icon=icons.up()
        )
        self.next_button = qtutils.create_action_button(
            tooltip=N_('Find the next matching commit'), icon=icons.down()
        )
        self.match_case_checkbox = qtutils.checkbox(N_('Match Case'))
        self.messages_checkbox = qtutils.checkbox(
            N_('Messages'),
            tooltip=N_('Also search the full commit messages'),
            checked=True,
        )
        self.files_checkbox = qtutils.checkbox(
            N_('Files'),
            tooltip=N_('Also search the paths of the files each commit changed'),
            checked=True,
        )
        self.only_matches_checkbox = qtutils.checkbox(
            N_('Only Matches'),
            tooltip=N_('Hide the commits that do not match'),
            checked=True,
        )
        self.count_label = QtWidgets.QLabel(self)
        self.close_button = qtutils.create_action_button(
            tooltip=N_('Close the find bar'), icon=icons.close()
        )

        layout = qtutils.hbox(
            defs.margin,
            defs.button_spacing,
            self.text,
            self.prev_button,
            self.next_button,
            self.match_case_checkbox,
            self.messages_checkbox,
            self.files_checkbox,
            self.only_matches_checkbox,
            self.count_label,
            qtutils.STRETCH,
            self.close_button,
        )
        self.setLayout(layout)
        self.setFocusProxy(self.text)

        self.text.esc_pressed.connect(self.hide_find)
        self.text.returnPressed.connect(self.search)
        self.text.textChanged.connect(self._criteria_changed)
        tree.commits_changed.connect(self.invalidate)

        qtutils.connect_button(self.next_button, self.search)
        qtutils.connect_button(self.prev_button, self.search_backwards)
        qtutils.connect_button(self.close_button, self.hide_find)
        for checkbox in (
            self.match_case_checkbox,
            self.messages_checkbox,
            self.files_checkbox,
        ):
            qtutils.connect_checkbox(checkbox, lambda _: self._criteria_changed())
        qtutils.connect_checkbox(
            self.only_matches_checkbox, lambda _: self._refresh_view()
        )

    def show_find(self):
        """Show the find bar and focus it, selecting any previous text"""
        focus = QtWidgets.QApplication.focusWidget()
        if focus is not None and not self.isAncestorOf(focus):
            self._return_focus = focus
        self.show()
        self.text.setFocus()
        self.text.selectAll()
        # hide_find() dropped git's matches: search again for the kept text.
        if self.text.get():
            self._matches = None
            self._schedule_git_search()
            self._refresh_view()

    def hide_find(self):
        """Hide the find bar and return focus to where it was opened from"""
        self._cancel_git_search()
        self.hide()
        self._refresh_view()
        widget = self._return_focus
        self._return_focus = None
        if widget is None or not widget.isVisible():
            widget = self._tree
        widget.setFocus()

    def invalidate(self):
        """Forget the matches when the commits change, so the next search rescans"""
        self._matches = None
        self._haystacks = None
        self._lower_haystacks = None
        self._text_search = None
        # Commits arrive in chunks while the list loads: search the new ones too.
        if self.isVisible() and self.text.get():
            self._schedule_git_search()
            self._refresh_view()

    def search(self):
        """Select the next matching commit"""
        self._jump(backwards=False, inclusive=False)

    def search_backwards(self):
        """Select the previous matching commit"""
        self._jump(backwards=True, inclusive=False)

    def _criteria_changed(self, *_args):
        """Find as you type, staying on the current commit while it matches"""
        self._matches = None
        self._schedule_git_search()
        current = self._tree.currentItem()
        if current is None or current.commit.oid != self._jumped_oid:
            self._origin_row = self._current_row()
        self._jump(backwards=False, inclusive=True, row=self._origin_row)
        current = self._tree.currentItem()
        self._jumped_oid = current.commit.oid if current is not None else None

    def _git_kinds(self):
        """The git searches the current criteria need"""
        if len(self.text.get()) < FIND_GIT_MIN_CHARS:
            return []
        kinds = []
        if self.messages_checkbox.isChecked():
            kinds.append('message')
        if self.files_checkbox.isChecked():
            kinds.append('files')
        return kinds

    def _cancel_git_search(self):
        """Drop the git matches and any search still running"""
        self._generation += 1
        self._git_timer.stop()
        self._git_oids = set()
        self._matched_kinds = {}
        self._git_pending = 0
        searches, self._searches = self._searches, []
        for search in searches:
            search.cancel()

    def _schedule_git_search(self):
        """Search with git once typing pauses"""
        self._cancel_git_search()
        if self._git_kinds():
            self._git_pending = len(self._git_kinds())
            self._git_timer.start()

    def _start_git_search(self):
        """Ask git which listed commits match by full message or changed files"""
        kinds = self._git_kinds()
        self._git_pending = len(kinds)
        if not kinds:
            return
        oids = [
            commit.oid
            for commit in self._tree.commits
            if commit.oid and commit.oid not in (dag.STAGE, dag.WORKTREE)
        ]
        needle = self.text.get()
        case_sensitive = self.match_case_checkbox.isChecked()
        generation = self._generation
        context = self._tree.context
        for kind in kinds:
            search = GitFindSearch(context, oids, needle, kind, case_sensitive)
            self._searches.append(search)
            task = qtutils.SimpleTask(search.run)
            self._runtask.start(
                task,
                result=lambda found, kind=kind: self._apply_git_matches(
                    generation, found, kind
                ),
            )

    def _apply_git_matches(self, generation, found, kind=None):
        """Add git's matches, and jump to one if the selection isn't a match"""
        if generation != self._generation:
            return
        self._git_pending = max(0, self._git_pending - 1)
        if kind is not None:
            for oid in found:
                self._matched_kinds.setdefault(oid, set()).add(kind)
        new = found - self._git_oids
        if not new:
            self._update_count(self._matching_rows())
            self._refresh_view()
            return
        self._git_oids |= new
        self._matches = None
        matches = self._matching_rows()
        current = self._tree.currentItem()
        untouched = current is not None and current.commit.oid == self._jumped_oid
        if untouched or self._current_row() not in matches:
            # Git's matches can land before the one find-as-you-type picked, so
            # pick again from where the search started, unless the user moved.
            self._jump(backwards=False, inclusive=True, row=self._origin_row)
            current = self._tree.currentItem()
            self._jumped_oid = current.commit.oid if current is not None else None
        else:
            self._update_count(matches)
        self._refresh_view()

    def _refresh_view(self):
        """Highlight the matches in the list, and hide the rest if asked to"""
        tree = self._tree
        needle = self.text.get()
        if not needle or not self.isVisible():
            tree.set_find_state(None)
            tree.show_only_rows(None)
            return
        filtering = self.only_matches_checkbox.isChecked()
        tree.set_find_state(
            FindState(
                needle,
                case_sensitive=self.match_case_checkbox.isChecked(),
                git_kinds=self._matched_kinds,
                filtering=filtering,
            )
        )
        tree.show_only_rows(set(self._matching_rows()) if filtering else None)

    def _text_indices(self, needle, case_sensitive):
        """Indices into tree.commits of the in-memory matches"""
        commits = self._tree.commits
        if self._haystacks is None:
            self._haystacks = [commit_haystack(commit) for commit in commits]
            self._lower_haystacks = None
        if case_sensitive:
            haystacks = self._haystacks
        else:
            if self._lower_haystacks is None:
                self._lower_haystacks = [hay.lower() for hay in self._haystacks]
            haystacks = self._lower_haystacks
            needle = needle.lower()
        # Typing extends the text, so only the previous matches can still match.
        candidates = range(len(commits))
        previous = self._text_search
        if (
            previous is not None
            and previous[1] == case_sensitive
            and needle.startswith(previous[0])
        ):
            candidates = previous[2]
        indices = []
        for idx in candidates:
            oid = commits[idx].oid or ''
            if not case_sensitive:
                oid = oid.lower()
            if oid.startswith(needle) or needle in haystacks[idx]:
                indices.append(idx)
        self._text_search = (needle, case_sensitive, indices)
        return indices

    def _matching_rows(self):
        """Return the matching row indices, rescanning the list when stale"""
        if self._matches is None:
            needle = self.text.get()
            if not needle:
                self._matches = []
                return self._matches
            tree = self._tree
            # Rows are tree.commits in reverse order.
            last = len(tree.commits) - 1
            case_sensitive = self.match_case_checkbox.isChecked()
            rows = {last - idx for idx in self._text_indices(needle, case_sensitive)}
            for oid in self._git_oids:
                item = tree.oidmap.get(oid)
                if item is not None:
                    rows.add(last - item.position)
            self._matches = sorted(rows)
        return self._matches

    def _current_row(self):
        """Return the current row of the commit list, or -1 when there is none"""
        item = self._tree.currentItem()
        if item is None:
            return -1
        return len(self._tree.commits) - 1 - item.position

    def _update_count(self, matches, row=None):
        """Show "position/total", and that git is still searching"""
        if row is None:
            row = self._current_row()
        text = ''
        if matches and row in matches:
            position = bisect.bisect_left(matches, row) + 1
            text = f'{position}/{len(matches)}'
        elif matches:
            text = str(len(matches))
        if self._git_pending:
            text = (text + ' ' + N_('searching...')).strip()
        self.count_label.setText(text)

    def _jump(self, backwards, inclusive, row=None):
        """Select the next or previous match and update the match count"""
        matches = self._matching_rows()
        if row is None:
            row = self._current_row()
        row = next_match(matches, row, backwards, inclusive)
        if row is not None:
            item = self._tree.topLevelItem(row)
            self._tree.select([item.commit.oid])
        self._update_count(matches, row)
        self._refresh_view()


class GitDAG(standard.MainWindow):
    """The git-dag widget."""

    commits_selected = Signal(object)

    def __init__(self, context, params, parent=None, prefs_model=None):
        super().__init__(parent)

        self.setMinimumSize(420, 420)

        # change when widgets are added/removed
        self.widget_version = 2
        self.context = context
        self.params = params
        self.model = context.model
        if prefs_model is None:
            prefs_model = prefs.PreferencesModel(context)
        self.prefs_model = prefs_model

        self.commits = {}
        self.commit_list = []
        self.selection = []
        self.old_selection = []
        self.old_refs = set()
        self.old_oids = None
        self.old_count = 0
        self.old_display_status = None
        self.force_refresh = False
        self._widgets_initialized = False
        self._graph_stale = False
        """The graphview needs (re)building from the current commit_list"""
        self._graph_build_id = 0
        """Bumped on every reload so a superseded deferred build bails out"""
        self._commits_loaded = False
        """Set once the reader finishes, so a lazy build sees the full history"""
        self._scope_history = []
        """Previous revision scopes, pushed on re-scope and popped by navigate_back"""

        self.thread = None
        self._stopping_threads = set()
        """Replaced readers still finishing, kept alive until they do"""
        self.revtext = GitDagLineEdit(context)
        self.maxresults = standard.SpinBox(digits=None, maxi=9999999, wrap=True)

        # Surface the revision field's filters (pickaxe, line range, author,
        # message grep, ignore merges) as a toolbar button. They otherwise
        # live only in the field's right-click menu, which is hard to find.
        self.filter_button = qtutils.create_action_button(
            tooltip=N_('Filter commits'), icon=icons.search()
        )
        self.filter_menu = qtutils.create_menu(N_('Filter commits'), self)
        self.show_all_history_action = qtutils.add_action(
            self, N_('Show entire history'), self.show_entire_history
        )
        self.filter_menu.addAction(self.show_all_history_action)
        self.filter_menu.addSeparator()
        for action in self.revtext.filter_actions():
            self.filter_menu.addAction(action)
        self.filter_button.setMenu(self.filter_menu)

        self.zoom_out = qtutils.create_action_button(
            tooltip=N_('Zoom Out'), icon=icons.zoom_out()
        )

        self.zoom_in = qtutils.create_action_button(
            tooltip=N_('Zoom In'), icon=icons.zoom_in()
        )

        self.zoom_to_fit = qtutils.create_action_button(
            tooltip=N_('Zoom to Fit'), icon=icons.zoom_fit_best()
        )

        self.treewidget = CommitTreeWidget(context, self)
        self.diffwidget = diff.CommitDiffWidget(context, self, is_commit=True)
        self.filewidget = filelist.FileWidget(context, self)
        self.graphview = GraphView(context, self)

        self._init_display_timer()

        # Once the selection settles, warm the diff and file list caches for
        # the commits around it, so stepping on with the arrow keys renders
        # from the cache instead of waiting for git.
        self._prefetch_row = None
        self._prefetch_timer = QtCore.QTimer(self)
        self._prefetch_timer.setSingleShot(True)
        self._prefetch_timer.setInterval(PREFETCH_SETTLE_MSEC)
        self._prefetch_timer.timeout.connect(self.prefetch_neighbours)

        self.treewidget.commits_selected.connect(
            self.commits_selected, type=Qt.QueuedConnection
        )
        self.graphview.commits_selected.connect(
            self.commits_selected, type=Qt.QueuedConnection
        )

        self.commits_selected.connect(self.select_commits, type=Qt.QueuedConnection)
        self.commits_selected.connect(
            self.diffwidget.commits_selected, type=Qt.QueuedConnection
        )
        self.commits_selected.connect(
            self.filewidget.commits_selected, type=Qt.QueuedConnection
        )
        self.commits_selected.connect(
            self.graphview.select_commits, type=Qt.QueuedConnection
        )
        self.commits_selected.connect(
            self.treewidget.select_commits, type=Qt.QueuedConnection
        )
        self.filewidget.files_selected.connect(
            self.diffwidget.files_selected, type=Qt.QueuedConnection
        )
        self.filewidget.difftool_selected.connect(
            self.difftool_selected, type=Qt.QueuedConnection
        )
        self.filewidget.histories_selected.connect(
            self.histories_selected, type=Qt.QueuedConnection
        )
        self.filewidget.file_history_selected.connect(
            self.file_history_selected, type=Qt.QueuedConnection
        )

        self.proxy = FocusRedirectProxy(
            self.treewidget, self.graphview, self.filewidget
        )

        # Tab / Shift+Tab cycles focus between the commit list and the file
        # list rather than walking Qt's default focus chain, so the keyboard
        # stays on the two list panes.
        self.treewidget.installEventFilter(self)
        self.filewidget.installEventFilter(self)

        self.treewidget.menu_actions = viewer_actions(self.treewidget, self.proxy)
        self.graphview.menu_actions = viewer_actions(self.graphview, self.proxy)
        # The diff panel offers the same clipboard actions as the commit list.
        # The hotkeys are bound there too so they work while the diff has focus.
        tree = self.treewidget
        self.diffwidget_copy_actions = []
        for title, func, hotkey in (
            (N_('Copy Commit'), tree.copy_to_clipboard, hotkeys.COPY_COMMIT_ID),
            (
                N_('Copy Commit Title'),
                tree.copy_title_to_clipboard,
                hotkeys.COPY_COMMIT_TITLE,
            ),
            (
                N_('Copy Commit (Short) and Title'),
                tree.copy_oneline_to_clipboard,
                hotkeys.COPY_COMMIT_ONELINE,
            ),
            (
                N_('Copy Commit Message'),
                tree.copy_message_to_clipboard,
                hotkeys.COPY_COMMIT_MESSAGE,
            ),
            (
                N_('Copy Commit Message (Unwrapped)'),
                tree.copy_message_unwrapped_to_clipboard,
                None,
            ),
            (
                N_('Copy Commit Link'),
                tree.copy_link_to_clipboard,
                hotkeys.COPY_COMMIT_LINK,
            ),
            (
                N_('Copy Commit Link (Markdown)'),
                tree.copy_markdown_link_to_clipboard,
                None,
            ),
        ):
            args = (hotkey,) if hotkey is not None else ()
            action = qtutils.add_action(self.diffwidget.diff, title, func, *args)
            self.diffwidget_copy_actions.append(set_icon(icons.copy(), action))
        self.diffwidget_copy_commit = self.diffwidget_copy_actions[0]
        self.diffwidget.diff.menu_actions.extend(self.diffwidget_copy_actions)

        self.controls_layout = qtutils.hbox(
            defs.no_margin,
            defs.spacing,
            self.revtext,
            self.filter_button,
            self.maxresults,
        )
        self.controls_layout.setAlignment(self.maxresults, Qt.AlignTop)
        self.controls_layout.setAlignment(self.filter_button, Qt.AlignTop)

        self.controls_widget = QtWidgets.QWidget()
        self.controls_widget.setLayout(self.controls_layout)

        self.log_dock = qtutils.create_dock(
            'Log', N_('Log'), self, stretch=False, hide_title=True
        )
        self.commit_find = CommitFindWidget(self.treewidget, self)
        self.commit_find.hide()
        self.log_panel = QtWidgets.QWidget()
        self.log_panel.setLayout(
            qtutils.vbox(
                defs.no_margin, defs.spacing, self.treewidget, self.commit_find
            )
        )
        self.log_panel.setFocusProxy(self.treewidget)
        self.log_dock.setWidget(self.log_panel)
        log_dock_titlebar = self.log_dock.titleBarWidget()
        log_dock_titlebar.add_corner_widget(self.controls_widget)

        self.file_dock = qtutils.create_dock(
            'Files', N_('Files'), self, hide_title=True
        )
        self.file_dock.setWidget(self.filewidget)

        self.diff_panel = diff.DiffPanel(self.diffwidget, self.diffwidget.diff, self)
        self.diff_options = diff.Options(self.diffwidget)
        self.diffwidget.set_options(self.diff_options)
        self.diff_options.hide_advanced_options()
        self.diff_options.show_commit_options()
        self.diff_options.set_diff_type(main.Types.TEXT)

        self.diff_dock = qtutils.create_dock('Diff', N_('Diff'), self, hide_title=True)
        self.diff_dock.setWidget(self.diff_panel)

        diff_titlebar = self.diff_dock.titleBarWidget()
        diff_titlebar.add_title_widget(self.diff_options)

        self.graph_controls_layout = qtutils.hbox(
            defs.no_margin,
            defs.button_spacing,
            self.zoom_out,
            self.zoom_in,
            self.zoom_to_fit,
            defs.spacing,
        )

        self.graph_controls_widget = QtWidgets.QWidget()
        self.graph_controls_widget.setLayout(self.graph_controls_layout)

        self.graphview_dock = qtutils.create_dock(
            'Graph', N_('Graph'), self, hide_title=True
        )
        self.graphview_dock.setWidget(self.graphview)
        graph_titlebar = self.graphview_dock.titleBarWidget()
        graph_titlebar.add_corner_widget(self.graph_controls_widget)

        self.display_inline_graph_action = qtutils.add_action_bool(
            self,
            N_('Display Inline Graph'),
            self.treewidget.display_inline_graph,
            False,
        )
        self.display_status_action = qtutils.add_action_bool(
            self, N_('Display Worktree Status'), self._display_worktree_status, False
        )
        self.lock_layout_action = qtutils.add_action_bool(
            self, N_('Lock Layout'), self.set_lock_layout, False
        )

        self.refresh_action = qtutils.add_action(
            self, N_('Refresh'), self.refresh, hotkeys.REFRESH
        )
        self.preferences_action = self._create_preferences_action()

        # Cmd+F finds commits from the commit list and the graph. The diff
        # panel binds the same keys for its own find bar. Each binding is
        # scoped to its panel, so they never clash.
        for widget in (self.log_panel, self.graphview):
            for title, func, hotkey in (
                (N_('Find Commits'), self.show_commit_find, hotkeys.SEARCH),
                (N_('Find Next Commit'), self.commit_find.search, hotkeys.SEARCH_NEXT),
                (
                    N_('Find Previous Commit'),
                    self.commit_find.search_backwards,
                    hotkeys.SEARCH_PREV,
                ),
            ):
                qtutils.add_action(widget, title, func, hotkey)

        # Create the application menu
        self.menubar = QtWidgets.QMenuBar(self)
        self.setMenuBar(self.menubar)

        # View Menu
        self.view_menu = qtutils.add_menu(N_('View'), self.menubar)
        self.view_menu.addAction(self.refresh_action)
        self.view_menu.addAction(self.display_inline_graph_action)
        self.view_menu.addAction(self.display_status_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.log_dock.toggleViewAction())
        self.view_menu.addAction(self.graphview_dock.toggleViewAction())
        self.view_menu.addAction(self.diff_dock.toggleViewAction())
        self.view_menu.addAction(self.file_dock.toggleViewAction())
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.lock_layout_action)
        # On macOS the PreferencesRole moves this into the application menu.
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.preferences_action)
        if core.IS_DARWIN:
            self.window_menu = qtutils.add_window_menu(self, self.menubar)

        left = Qt.LeftDockWidgetArea
        right = Qt.RightDockWidgetArea
        self.addDockWidget(left, self.log_dock)
        self.addDockWidget(left, self.diff_dock)
        self.addDockWidget(right, self.graphview_dock)
        self.addDockWidget(right, self.file_dock)

        # Also re-loads dag.* from the saved state
        self.init_state(context.settings, self.resize_to_desktop)

        qtutils.connect_button(self.zoom_out, self.graphview.zoom_out)
        qtutils.connect_button(self.zoom_in, self.graphview.zoom_in)
        qtutils.connect_button(self.zoom_to_fit, self.graphview.zoom_to_fit)

        self.treewidget.zoom_to_fit.connect(
            self.graphview.zoom_to_fit, type=Qt.QueuedConnection
        )
        self.treewidget.diff_commits.connect(
            self.diff_commits, type=Qt.QueuedConnection
        )
        self.treewidget.search_line_range_in_oid.connect(
            self.search_line_range_in_oid, type=Qt.QueuedConnection
        )
        self.graphview.diff_commits.connect(self.diff_commits, type=Qt.QueuedConnection)
        self.graphview.search_line_range_in_oid.connect(
            self.search_line_range_in_oid, type=Qt.QueuedConnection
        )
        self.filewidget.grab_file.connect(self.grab_file, type=Qt.QueuedConnection)
        self.filewidget.grab_file_from_parent.connect(
            self.grab_file_from_parent, type=Qt.QueuedConnection
        )
        self.filewidget.select_line_range_for_file.connect(
            self.search_line_range_for_file, type=Qt.QueuedConnection
        )
        self.maxresults.editingFinished.connect(self.display, type=Qt.QueuedConnection)
        self.revtext.textChanged.connect(self.text_changed, type=Qt.QueuedConnection)
        self.revtext.activated.connect(self.display, type=Qt.QueuedConnection)
        self.revtext.enter.connect(self.display, type=Qt.QueuedConnection)
        self.revtext.down.connect(self.focus_tree, type=Qt.QueuedConnection)
        # The model is updated in another thread so use
        # signals/slots to bring control back to the main GUI thread
        self.model.updated.connect(self.model_updated, type=Qt.QueuedConnection)
        # Build the graph canvas the first time it is revealed, so a session
        # that keeps the Graph dock closed never pays for it.
        self.graphview_dock.visibilityChanged.connect(
            self._graphview_visibility_changed, type=Qt.QueuedConnection
        )

        qtutils.add_action(self, 'FocusInput', self.focus_input, hotkeys.FOCUS_INPUT)
        qtutils.add_action(self, 'FocusTree', self.focus_tree, hotkeys.FOCUS_TREE)
        qtutils.add_action(self, 'FocusDiff', self.focus_diff, hotkeys.FOCUS_DIFF)
        self.navigate_back_action = qtutils.add_action(
            self, N_('Back'), self.navigate_back, hotkeys.NAVIGATE_BACK
        )
        qtutils.add_close_action(self)

        self.set_params(params)
        # Start with the keyboard on the commit list. Without this Qt gives
        # focus to the first widget in the focus chain, the diff text, so the
        # arrow keys scrolled the diff instead of moving between commits.
        self.treewidget.setFocus()

    def set_params(self, params):
        self.params = params
        # A fresh set of params starts a new navigation context.
        self._scope_history = []
        # Update fields affected by model
        self.revtext.setText(params.ref)
        self.maxresults.setValue(params.count)
        self.update_window_title()

        self._stop_reader_thread()
        self.thread = self._new_reader_thread()

    def _new_reader_thread(self):
        """Create a reader whose signals only count while it is self.thread"""
        thread = ReaderThread(self.context, self.params)

        def current(func):
            # A replaced reader can still have queued signals on their way.
            return lambda *args: func(*args) if thread is self.thread else None

        thread.begin.connect(current(self.thread_begin), type=Qt.QueuedConnection)
        thread.status.connect(current(self.thread_status), type=Qt.QueuedConnection)
        thread.add.connect(current(self.add_commits), type=Qt.QueuedConnection)
        thread.end.connect(current(self.thread_end), type=Qt.QueuedConnection)
        return thread

    def _stop_reader_thread(self):
        """Stop the reader thread if it is currently running"""
        if self.thread is not None and self.thread.isRunning():
            self.thread.requestInterruption()
            QtCore.QThread.currentThread().yieldCurrentThread()
            self.thread.wait(100)

    def _start_reader_thread(self):
        """Start reading, with a fresh reader if the last one is still stopping

        QThread.start() does nothing while the thread runs, and a reader can
        take longer than _stop_reader_thread() waits: git's first line of a
        --topo-order or -S query, or the graph build over a long history. The
        new query was then silently dropped.
        """
        old = self.thread
        if old.isRunning():
            # Keep the old reader alive until it notices the interruption:
            # destroying a running QThread aborts the process.
            self._stopping_threads.add(old)
            old.finished.connect(lambda: self._stopping_threads.discard(old))
            self.thread = self._new_reader_thread()
        self.thread.start()

    def _display_worktree_status(self, enabled):
        """Enable and disable the display of the WORKTREE and STAGE pseudo-commits"""
        self.params.display_status = enabled
        self.display()

    def show_commit_find(self):
        """Show the commit find bar, revealing the commit list if it is closed"""
        if not self.log_dock.isVisible():
            self.log_dock.show()
            self.log_dock.raise_()
        self.commit_find.show_find()

    def focus_input(self):
        """Focus the revision input field"""
        self.revtext.setFocus()

    def focus_tree(self):
        """Focus the revision tree list widget"""
        self.treewidget.setFocus()

    def focus_diff(self):
        """Focus the diff widget"""
        self.diffwidget.setFocus()

    def eventFilter(self, obj, event):
        """Cycle focus between the commit list and the file list with Tab"""
        if event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Tab, Qt.Key_Backtab):
                if obj is self.treewidget:
                    self.focus_files()
                else:
                    self.treewidget.setFocus(Qt.TabFocusReason)
                return True
        return super().eventFilter(obj, event)

    def focus_files(self):
        """Focus the file list, selecting the first file if none is selected"""
        files = self.filewidget
        files.setFocus(Qt.TabFocusReason)
        # Select (not merely make current) the first file so it is highlighted
        # and arrow keys move from it. setCurrentItem alone only sets the
        # current index, which leaves the row unhighlighted and makes Down jump
        # to the second file.
        if files.topLevelItemCount() and not files.selectedItems():
            first = files.topLevelItem(0)
            files.setCurrentItem(first)
            first.setSelected(True)

    def text_changed(self, txt):
        """Respond to changes to the revision input text"""
        self.params.ref = txt
        self.update_window_title()

    def update_window_title(self):
        """Update the window title to reflect the displayed ref"""
        project = self.model.project
        if self.params.ref:
            self.setWindowTitle(
                N_('%(project)s: %(ref)s - DAG')
                % {
                    'project': project,
                    'ref': self.params.ref,
                }
            )
        else:
            self.setWindowTitle(project + N_(' - DAG'))

    def export_state(self):
        """Store persistent GUI state"""
        state = standard.MainWindow.export_state(self)
        state['count'] = self.params.count
        state['display_inline_graph'] = self.display_inline_graph_action.isChecked()
        state['display_status'] = self.params.display_status
        state['log'] = self.treewidget.export_state()
        state['word_wrap'] = self.diffwidget.options.enable_word_wrapping.isChecked()
        state[
            'unwrap_commit_message'
        ] = self.diffwidget.options.unwrap_commit_message.isChecked()
        state['intraline_diff_preset'] = self.diffwidget.options.intraline_diff_preset()
        state[
            'intraline_diff_timing'
        ] = self.diffwidget.options.intraline_diff_timing.isChecked()
        return state

    def apply_state(self, state):
        """Apply persistent GUI state"""
        result = standard.MainWindow.apply_state(self, state)
        try:
            count = state['count']
            if self.params.overridden('count'):
                count = self.params.count
        except (KeyError, TypeError, ValueError, AttributeError):
            count = self.params.count
            result = False
        self.params.set_count(count)

        display_status = state.get('display_status', True)
        self.params.set_display_status(display_status)
        with qtutils.BlockSignals(self.display_status_action):
            self.display_status_action.setChecked(display_status)

        display_inline_graph = state.get('display_inline_graph', True)
        self.treewidget.display_inline_graph(display_inline_graph)
        with qtutils.BlockSignals(self.display_inline_graph_action):
            self.display_inline_graph_action.setChecked(display_inline_graph)

        self.lock_layout_action.setChecked(state.get('lock_layout', False))
        self.diffwidget.set_word_wrapping(state.get('word_wrap', False), update=True)
        self.diffwidget.set_unwrap_commit_message(
            state.get('unwrap_commit_message', False), update=True
        )

        try:
            log_state = state['log']
        except (KeyError, ValueError):
            log_state = None
        if log_state:
            self.treewidget.apply_state(log_state)

        intraline_diff_preset = state.get(
            'intraline_diff_preset', diff_intraline.INTRALINE_DIFF_PRESET_DEFAULT_ID
        )
        self.diffwidget.set_intraline_diff_preset(intraline_diff_preset, update=True)

        intraline_diff_timing = bool(state.get('intraline_diff_timing', False))
        self.set_intraline_diff_timing(intraline_diff_timing, update=True)

        return result

    def set_intraline_diff_preset(self, preset_id, update=False):
        """Forward the selected text diff preset to the editor."""
        # only the text editor
        self.diffwidget.set_intraline_diff_preset(preset_id, update=update)

    def set_intraline_diff_timing(self, enabled, update=False):
        """Forward the intra-line diff timing option to the editor."""
        self.diffwidget.set_intraline_diff_timing(enabled, update=update)

    def _init_display_timer(self):
        """Create the timer that coalesces model updates into one display()

        fsmonitor and refresh events arrive in bursts, and every display()
        call runs a "git rev-parse" on the GUI thread before its no-op guard
        can decide anything, so bursts are debounced into a single pass.
        """
        self._display_timer = QtCore.QTimer(self)
        self._display_timer.setSingleShot(True)
        self._display_timer.setInterval(200)
        self._display_timer.timeout.connect(self.display)

    def model_updated(self):
        """Refresh the view when the model is updated"""
        # The window title only reads the model, so update it immediately;
        # the expensive display() pass is debounced.
        self._display_timer.start()
        self.update_window_title()

    def _create_preferences_action(self):
        """Create the Preferences action so a standalone DAG can reach it"""
        action = qtutils.add_action_with_icon(
            self,
            icons.configure(),
            N_('Preferences'),
            partial(
                prefs_widget.preferences,
                self.context,
                parent=self,
                model=self.prefs_model,
            ),
            QtGui.QKeySequence.Preferences,
        )
        # Tell Qt's Cocoa plugin this belongs in the macOS application menu.
        # Without an explicit role Qt matches the English label text, which
        # fails once the label is translated.
        action.setMenuRole(QtWidgets.QAction.PreferencesRole)
        return action

    def refresh(self):
        """Unconditionally refresh the DAG"""
        # self.force_refresh triggers an Unconditional redraw
        self.force_refresh = True
        cmds.do(cmds.Refresh, self.context)

    def display(self):
        """Update the view when the Git refs change"""
        # A direct call supersedes any debounced pass still pending.
        self._display_timer.stop()
        ref = get(self.revtext)
        count = get(self.maxresults)
        context = self.context
        display_status = get(self.display_status_action)
        # The DAG tries to avoid updating when the object IDs have not
        # changed.  Without doing this the DAG constantly redraws itself
        # whenever inotify sends update events, which hurts usability.
        #
        # To minimize redraws we leverage `git rev-parse`.  The strategy is to
        # use `git rev-parse` on the input line, which converts each argument
        # into object IDs.  From there it's a simple matter of detecting when
        # the object IDs changed.
        #
        # In addition to object IDs, we also need to know when the set of
        # named references (branches, tags) changes so that an update is
        # triggered when new branches and tags are created.
        refs = self._ref_state()
        argv = utils.shell_split(ref or 'HEAD')
        oids = gitcmds.parse_refs(context, argv)
        update = (
            self.force_refresh
            or count != self.old_count
            or oids != self.old_oids
            or display_status != self.old_display_status
        )
        if refs != self.old_refs and not update:
            # The commits shown come from the oids above, which haven't changed,
            # so only labels moved. Reloading the history for that took 1.9 s
            # with 50000 commits.
            if self._commits_loaded:
                self.relabel_commits(self.old_refs, refs)
            else:
                update = True
        if update:
            self._stop_reader_thread()
            self.params.set_ref(ref)
            self.params.set_count(count)
            self.params.set_display_status(display_status)
            self._start_reader_thread()

        self.old_oids = oids
        self.old_count = count
        self.old_refs = refs
        self.old_display_status = self.params.display_status
        self.force_refresh = False

    def _ref_state(self):
        """Every ref and the commit it points to, plus the checked-out branch"""
        return gitcmds.ref_targets(self.context) | {('HEAD', self.model.currentbranch)}

    def relabel_commits(self, old_refs, new_refs):
        """Re-read the labels of the commits whose refs changed, without a reload"""
        old_map = dict(old_refs)
        new_map = dict(new_refs)
        oids = set()
        for name, value in old_refs ^ new_refs:
            if name == 'HEAD':
                # The checked-out branch changed: relabel its old and new tips.
                for refs_map in (old_map, new_map):
                    oid = refs_map.get('refs/heads/' + value)
                    if oid:
                        oids.add(oid)
            else:
                oids.add(value)
        commits = [
            self.commits[oid]
            for oid in oids
            if oid in self.commits and self.commits[oid].oid == oid
        ]
        if not commits:
            return
        decorations = gitcmds.commit_decorations(
            self.context, [commit.oid for commit in commits]
        )
        tree = self.treewidget
        for commit in commits:
            item = tree.oidmap.get(commit.oid)
            for tag in commit.tags:
                if self.commits.get(tag) is commit:
                    del self.commits[tag]
                if item is not None and tree.oidmap.get(tag) is item:
                    del tree.oidmap[tag]
            commit.set_labels(decorations.get(commit.oid, ''))
            for tag in commit.tags:
                self.commits[tag] = commit
                if item is not None:
                    tree.oidmap[tag] = item
        tree.render_commits([commit.oid for commit in commits])
        tree.commits_changed.emit()
        # The graph canvas lays its nodes out around their labels: rebuild it.
        if not self._graph_stale:
            self.graphview.clear()
            self._graph_stale = True
            self._graph_build_id += 1
            if self.graphview_dock.isVisible():
                QtCore.QTimer.singleShot(
                    0, partial(self._populate_graphview, self._graph_build_id)
                )

    def select_commits(self, commits):
        """Commits were selected"""
        self.selection = commits
        self._prefetch_timer.start()
        enabled = bool(commits)
        for action in self.diffwidget_copy_actions:
            action.setEnabled(enabled)

    def prefetch_neighbours(self):
        """Prefetch the diffs and file lists of the commits around the selection"""
        if len(self.selection) != 1:
            return
        tree = self.treewidget
        row = tree.currentIndex().row()
        if row < 0:
            return
        rows = neighbour_rows(row, self._prefetch_row, tree.topLevelItemCount())
        self._prefetch_row = row
        oids = [tree.topLevelItem(idx).commit.oid for idx in rows]
        self.diffwidget.prefetch(oids)
        self.filewidget.prefetch(oids)

    def clear(self):
        """Clear the view and the list of known commits"""
        self.commits.clear()
        self.commit_list = []
        self.graphview.clear()
        self.treewidget.clear()
        # Drop cached diff text; an external diff command or encoding change
        # could otherwise leave a stale diff on display after a reload.
        self.diffwidget.clear_diff_cache()
        # The reloaded history may change what a cached commit's file list
        # means (e.g. after an amend), so drop the file-list cache too.
        self.filewidget.clear_files_cache()
        # The graph must be rebuilt from the incoming commits. Bumping the build
        # id invalidates any deferred build still queued from a prior pass.
        self._graph_stale = True
        self._graph_build_id += 1
        self._commits_loaded = False

    def add_commits(self, chunk):
        """Add new commits from the reader thread

        chunk is a list of commits, or a (commits, graph_rows) pair from
        ReaderThread, which builds the inline graph over all of them.
        """
        graph_rows = None
        if isinstance(chunk, tuple):
            commits, graph_rows = chunk
        else:
            commits = chunk
        self.commit_list.extend(commits)
        # Keep track of commits
        for commit_obj in commits:
            self.commits[commit_obj.oid] = commit_obj
            for tag in commit_obj.tags:
                self.commits[tag] = commit_obj
        # The treewidget is quick to update.  The graphview is slower when updating
        # incrementally so it is updated just once at thread_end() once all commits have
        # been gathered.
        self.treewidget.add_commits(commits, graph_rows=graph_rows)

    def thread_begin(self):
        """The reader thread has begun"""
        if self.selection:
            self.old_selection = self.selection
        self.clear()

    def thread_end(self):
        """The reader thread has completed"""
        # The full history is now in commit_list, so a lazy build is safe.
        self._commits_loaded = True
        # Restoring the selection drives the tree and diff, which are
        # independent of the graph canvas.
        self.restore_selection()
        # Build the graph canvas only when its dock is visible, and defer it to
        # the next event-loop turn so the tree and diff paint first. A dock that
        # stays hidden is populated later by _graphview_visibility_changed.
        if self.graphview_dock.isVisible():
            QtCore.QTimer.singleShot(
                0, partial(self._populate_graphview, self._graph_build_id)
            )

    def _populate_graphview(self, build_id):
        """Build the graph canvas from the current commit_list

        No-ops when a newer reload has superseded this build or the graph is
        already up to date.
        """
        if build_id != self._graph_build_id or not self._graph_stale:
            return
        self.graphview.add_commits(self.commit_list)
        # The tree/diff selection was restored before the canvas existed, so
        # mirror it onto the freshly built graph and centre the view on it.
        self.graphview.select_commits(self.selection or self.old_selection)
        self.graphview.set_initial_view()
        self._graph_stale = False

    def _graphview_visibility_changed(self, visible):
        """Populate the graph canvas the first time its dock is revealed

        Only builds once the reader has finished; a mid-load reveal would
        otherwise lay out a partial history that thread_end would not correct.
        """
        if visible and self._graph_stale and self._commits_loaded:
            self._populate_graphview(self._graph_build_id)

    def thread_status(self, successful):
        """Indicate an error when the revision input contains an invalid ref"""
        self.revtext.hint.set_error(not successful)

    def restore_selection(self):
        """Restore the selection before the display was refreshed"""
        # The selection can become empty when the widgets are cleared.
        selection = self.selection or self.old_selection
        try:
            commit_obj = self.commit_list[-1]
        except IndexError:
            # No commits, exist, early-out
            return

        raw_commits = [self.commits.get(commit.oid, None) for commit in selection]
        new_commits = [commit for commit in raw_commits if commit is not None]
        if new_commits:
            # The old selection exists in the new state
            self.commits_selected.emit(sort_by_generation(new_commits))
        else:
            # The old selection is now empty.  Select the top-most commit
            self.commits_selected.emit([commit_obj])

        self.graphview.set_initial_view()

    def diff_commits(self, left, right):
        """React to diff_commits signals by displaying a difftool interface"""
        paths = self.params.paths()
        if paths:
            difftool.difftool_launch(self.context, left=left, right=right, paths=paths)
        else:
            difftool.diff_commits(self.context, self, left, right, detect_renames=True)

    def search_line_range_in_oid(self, oid):
        """Open a dialog for generating "git log -L" line range expressions"""
        all_paths = self.filewidget.selected_paths()
        if all_paths:
            paths = all_paths[0]
        else:
            paths = None
        widget = finder.new_finder(
            self.context,
            paths=paths,
            ref=oid,
            title=N_('Trace Evolution of Line Range'),
            ok_text=N_('Select Line Range'),
            parent=self,
        )
        widget.search()
        result = widget.exec_()
        if result != QtWidgets.QDialog.Accepted:
            return
        start, span = widget.selected_line_range()
        filename = widget.filename
        if not filename:
            return
        range_expression = f'-L{start},+{span}:{filename}'
        self.revtext.insert(range_expression)
        self.display()

    def file_history_selected(self, path):
        """Open the File History window for a file from the files widget"""
        from . import filehistory  # lazy import; avoids a circular import

        filehistory.file_history(self.context, path)

    def histories_selected(self, histories):
        """Respond to file-based history selection from the files widget"""
        argv = [self.model.currentbranch, '--']
        argv.extend(histories)
        rev_text = core.list2cmdline(argv)
        self._scope_to(rev_text)

    def show_entire_history(self):
        """Clear any path or filter scope and show the full branch history"""
        self._scope_to(self.model.currentbranch)

    def _scope_to(self, rev_text):
        """Re-scope the DAG, remembering the previous scope for navigate_back()"""
        current = get(self.revtext)
        if rev_text == current:
            return
        self._scope_history.append(current)
        self.revtext.setText(rev_text)
        self.display()

    def navigate_back(self):
        """Restore the revision scope from before the last re-scope"""
        if not self._scope_history:
            return
        self.revtext.setText(self._scope_history.pop())
        self.display()

    def difftool_selected(self, files):
        """Launch difftool across a commit range"""
        bottom, top = self.treewidget.selected_commit_range()
        if not top:
            return
        difftool.difftool_launch(
            self.context, left=bottom, left_take_parent=True, right=top, paths=files
        )

    def grab_file(self, filename):
        """Save the selected file from the file list widget"""
        oid = self.treewidget.selected_oid()
        model = browse.BrowseModel(oid, filename=filename)
        browse.save_path(self.context, filename, model)

    def grab_file_from_parent(self, filename):
        """Save the selected file from parent commit in the file list widget"""
        oid = self.treewidget.selected_oid() + '^'
        model = browse.BrowseModel(oid, filename=filename)
        browse.save_path(self.context, filename, model)

    def search_line_range_for_file(self, filename):
        """Generate a line range expression for the specified filename"""
        oid = self.treewidget.selected_oid()
        if not oid or not filename:
            return
        self.search_line_range_in_oid(oid)

    # Qt overrides
    def closeEvent(self, event):
        """Ensure the revision text popup is closed"""
        self.revtext.close_popup()
        self._stop_reader_thread()
        standard.MainWindow.closeEvent(self, event)

    def showEvent(self, event):
        """Resize widgets once their sizes are known"""
        standard.MainWindow.showEvent(self, event)
        if not self._widgets_initialized:
            self._widgets_initialized = True
            self.maxresults.setMinimumHeight(self.revtext.height())


# Commits reach the commit list in chunks, so the GUI stays responsive.
READER_CHUNK_SIZE = 2048


def commit_graph(commits):
    """Build the inline graph for commits, which are oldest-first"""
    head_oid = None
    for commit in commits:
        if 'HEAD' in commit.tags:
            head_oid = commit.oid
    return build_graph(
        [(commit.oid, [parent.oid for parent in commit.parents]) for commit in commits],
        head_oid=head_oid,
    )


def graph_row_pairs(rows):
    """Pair each graph row with the row above it (None for the top row)"""
    return list(zip(rows, itertools.chain([None], rows)))


class ReaderThread(QtCore.QThread):
    begin = Signal()
    add = Signal(object)
    end = Signal()
    status = Signal(object)

    def __init__(self, context, params):
        super().__init__()
        self.context = context
        self.params = params

    def run(self):
        """Gather commits and emit them to the main thread"""
        context = self.context
        repo = dag.RepoReader(
            context, self.params, should_interrupt=self.isInterruptionRequested
        )
        repo.reset()
        self.begin.emit()

        commits = []
        for commit in repo.get():
            if self.isInterruptionRequested():
                repo.reset()
                return
            commits.append(commit)

        stage, worktree = repo.get_worktree_commits()
        if stage:
            commits.append(stage)
        if worktree:
            commits.append(worktree)

        # Build the inline graph over every commit here, off the GUI thread.
        # Built per chunk, each chunk started with no lanes and cut every lane
        # where it met the next one, every 2048 rows. git's newest-first
        # output has already been read in full, so nothing waits on this.
        rows = graph_row_pairs(commit_graph(commits).rows)
        row_of = {pair[0].commit_oid: pair for pair in rows}
        del rows
        for start in range(0, len(commits), READER_CHUNK_SIZE):
            if self.isInterruptionRequested():
                repo.reset()
                return
            chunk = commits[start : start + READER_CHUNK_SIZE]
            graph_rows = [
                row_of[commit.oid] for commit in chunk if commit.oid in row_of
            ]
            self.add.emit((chunk, graph_rows))

        self.status.emit(repo.returncode == 0)
        self.end.emit()


class Cache:
    _label_font = None

    @classmethod
    def label_font(cls):
        font = cls._label_font
        if font is None:
            font = cls._label_font = QtWidgets.QApplication.font()
            font.setPointSize(6)
        return font


class Edge(QtWidgets.QGraphicsItem):
    item_type = qtutils.standard_item_type_value(1)

    def __init__(self, source, dest):
        QtWidgets.QGraphicsItem.__init__(self)

        self.setAcceptedMouseButtons(Qt.NoButton)
        self.source = source
        self.dest = dest
        self.commit = source.commit
        self.setZValue(-2)

        self.recompute_bound()
        self.path = None
        self.path_valid = False

        # Choose a new color for new branch edges
        if source.x() < dest.x():
            color = EdgeColor.cycle()
        else:
            color = EdgeColor.current()

        # Remembered so refresh_color() can follow a Light/Dark switch.
        self.color_index = EdgeColor.current_color_index
        self.pen = EdgeColor.pen(color)

    def refresh_color(self):
        """Re-pick this edge's color from the current EdgeColor set

        Edges of one color share a pen, so this recolors all of them.
        """
        color = QtGui.QColor(EdgeColor.colors[self.color_index % len(EdgeColor.colors)])
        color.setAlpha(self.pen.color().alpha())
        self.pen.setColor(color)
        self.update()

    def recompute_bound(self):
        # Commit shapes are centred on the commit's position and edges have no
        # parent or transform, so the end points are the commits' positions.
        self.bound = QtCore.QRectF(self.source.pos(), self.dest.pos()).normalized()

    def commits_were_invalidated(self):
        self.recompute_bound()
        self.prepareGeometryChange()
        # The path should not be recomputed immediately because just small part
        # of DAG is actually shown at same time. It will be recomputed on
        # demand in course of 'paint' method.
        self.path_valid = False
        # Hence, just queue redrawing.
        self.update()

    # Qt overrides
    def type(self):
        return self.item_type

    def boundingRect(self):
        return self.bound

    def recompute_path(self):
        QRectF = QtCore.QRectF
        QPointF = QtCore.QPointF

        arc_rect = 10
        connector_length = 5

        path = QtGui.QPainterPath()

        if self.source.x() == self.dest.x():
            path.moveTo(self.source.x(), self.source.y())
            path.lineTo(self.dest.x(), self.dest.y())
        else:
            # Define points starting from the source.
            point1 = QPointF(self.source.x(), self.source.y())
            point2 = QPointF(point1.x(), point1.y() - connector_length)
            point3 = QPointF(point2.x() + arc_rect, point2.y() - arc_rect)

            # Define points starting from the destination.
            point4 = QPointF(self.dest.x(), self.dest.y())
            point5 = QPointF(point4.x(), point3.y() - arc_rect)
            point6 = QPointF(point5.x() - arc_rect, point5.y() + arc_rect)

            start_angle_arc1 = 180
            span_angle_arc1 = 90
            start_angle_arc2 = 90
            span_angle_arc2 = -90

            # If the destination is at the left of the source, then we need to
            # reverse some values.
            if self.source.x() > self.dest.x():
                point3 = QPointF(point2.x() - arc_rect, point3.y())
                point6 = QPointF(point5.x() + arc_rect, point6.y())

                span_angle_arc1 = 90

            path.moveTo(point1)
            path.lineTo(point2)
            path.arcTo(QRectF(point2, point3), start_angle_arc1, span_angle_arc1)
            path.lineTo(point6)
            path.arcTo(QRectF(point6, point5), start_angle_arc2, span_angle_arc2)
            path.lineTo(point4)

        self.path = path
        self.path_valid = True

    def paint(self, painter, _option, _widget):
        if not self.path_valid:
            self.recompute_path()
        painter.setPen(self.pen)
        painter.drawPath(self.path)


def _edge_base_colors():
    """Return the edge colors shared by the light and dark sets"""
    return (
        QtGui.QColor(Qt.red),
        QtGui.QColor(Qt.cyan),
        QtGui.QColor(Qt.magenta),
        QtGui.QColor(Qt.green),
        # Orange; Qt.yellow is too low-contrast
        qtutils.rgba(0xFF, 0x66, 0x00),
    )


class EdgeColor:
    """An edge color factory"""

    current_color_index = 0
    colors = _edge_base_colors()
    # Edges share one pen per color, so a large graph builds a few pens
    # rather than one per edge.
    _pens = {}

    @classmethod
    def update_colors(cls, dark):
        """Rebuild the colors for a dark or light background

        The tuple is rebuilt rather than extended, so opening more DAG windows
        or switching between Light and Dark never mixes the two sets.
        """
        if dark:
            extra = (
                QtGui.QColor(Qt.red).lighter(),
                QtGui.QColor(Qt.cyan).lighter(),
                QtGui.QColor(Qt.magenta).lighter(),
                QtGui.QColor(Qt.green).lighter(),
                QtGui.QColor(Qt.yellow).lighter(),
            )
        else:
            extra = (
                QtGui.QColor(Qt.blue),
                QtGui.QColor(Qt.darkRed),
                QtGui.QColor(Qt.darkCyan),
                QtGui.QColor(Qt.darkMagenta),
                QtGui.QColor(Qt.darkGreen),
                QtGui.QColor(Qt.darkYellow),
                QtGui.QColor(Qt.darkBlue),
            )
        cls.colors = _edge_base_colors() + extra
        # Existing edges keep their pens, which refresh_color() recolors.
        cls._pens = {}

    @classmethod
    def pen(cls, color):
        """Return the shared edge pen for a color"""
        key = color.rgba()
        pen = cls._pens.get(key)
        if pen is None:
            pen = QtGui.QPen(color, 2.0, Qt.SolidLine, Qt.SquareCap, Qt.RoundJoin)
            cls._pens[key] = pen
        return pen

    @classmethod
    def cycle(cls):
        cls.current_color_index += 1
        cls.current_color_index %= len(cls.colors)
        color = cls.colors[cls.current_color_index]
        color.setAlpha(128)
        return color

    @classmethod
    def current(cls):
        return cls.colors[cls.current_color_index]

    @classmethod
    def reset(cls):
        cls.current_color_index = 0


class Commit(QtWidgets.QGraphicsItem):
    item_type = qtutils.standard_item_type_value(2)
    commit_radius = 12.0
    merge_radius = 18.0

    item_shape = QtGui.QPainterPath()
    item_shape.addRect(
        commit_radius / -2.0, commit_radius / -2.0, commit_radius, commit_radius
    )
    item_bbox = item_shape.boundingRect()

    inner_rect = QtGui.QPainterPath()
    inner_rect.addRect(
        commit_radius / -2.0 + 2.0,
        commit_radius / -2.0 + 2.0,
        commit_radius - 4.0,
        commit_radius - 4.0,
    )
    inner_rect = inner_rect.boundingRect()

    commit_color = QtGui.QColor(Qt.white)
    outline_color = commit_color.darker()
    merge_color = QtGui.QColor(Qt.lightGray)

    commit_selected_color = QtGui.QColor(Qt.green)
    selected_outline_color = commit_selected_color.darker()

    commit_pen = QtGui.QPen()
    commit_pen.setWidth(1)
    commit_pen.setColor(outline_color)

    def __init__(
        self,
        commit,
        selectable=QtWidgets.QGraphicsItem.ItemIsSelectable,
        cursor=Qt.PointingHandCursor,
        xpos=commit_radius / 2.0 + 1.0,
    ):
        QtWidgets.QGraphicsItem.__init__(self)

        self.commit = commit
        self.selected = False

        self.setFlag(selectable)
        self.setCursor(cursor)
        # Tooltips are built lazily on hover (see GraphView.viewportEvent);
        # building one per node up front is measurable on large histories.
        self._tooltip_set = False

        if commit.tags:
            self.label = label = Label(commit)
            label.setParentItem(self)
            label.setPos(xpos + 1, -self.commit_radius / 2.0)
        else:
            self.label = None

        self.is_merge = len(commit.parents) > 1

        self.pressed = False
        self.dragged = False
        self.edges = {}

    def ensure_tooltip(self):
        """Build the tooltip on first hover; later hovers reuse it"""
        if self._tooltip_set:
            return
        commit = self.commit
        self.setToolTip(commit.oid[:12] + ': ' + commit.summary)
        self._tooltip_set = True

    def type(self):
        return self.item_type

    def boundingRect(self):
        return self.item_bbox

    def shape(self):
        return self.item_shape

    def paint(self, painter, option, _widget):
        # Do not draw outside the exposed rectangle.
        painter.setClipRect(option.exposedRect)

        # The colours are picked here rather than cached on selection: an
        # itemChange() override costs a Python call for every flag, cursor and
        # position change of every node, and a cached colour would miss a
        # Light/Dark switch.
        if self.isSelected():
            pen = QtGui.QPen(self.selected_outline_color)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(self.commit_selected_color)
        else:
            painter.setPen(self.commit_pen)
            painter.setBrush(self.merge_color if self.is_merge else self.commit_color)
        painter.drawEllipse(self.inner_rect)

    def mousePressEvent(self, event):
        QtWidgets.QGraphicsItem.mousePressEvent(self, event)
        self.pressed = True
        self.selected = self.isSelected()

    def mouseMoveEvent(self, event):
        if self.pressed:
            self.dragged = True
        QtWidgets.QGraphicsItem.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event):
        QtWidgets.QGraphicsItem.mouseReleaseEvent(self, event)
        if not self.dragged and self.selected and event.button() == Qt.LeftButton:
            return
        self.pressed = False
        self.dragged = False


class Label(QtWidgets.QGraphicsItem):
    item_type = qtutils.graphics_item_type_value(3)

    head_color = QtGui.QColor(Qt.green)
    other_color = QtGui.QColor(Qt.white)
    remote_color = QtGui.QColor(Qt.yellow)

    head_pen = QtGui.QPen()
    head_pen.setColor(QtGui.QColor(Qt.black))
    head_pen.setWidth(1)

    text_pen = QtGui.QPen()
    text_pen.setColor(QtGui.QColor(Qt.black))
    text_pen.setWidth(1)

    border = 1
    item_spacing = 8
    text_x_offset = 3
    text_y_offset = 0

    def __init__(self, commit):
        QtWidgets.QGraphicsItem.__init__(self)
        self.setZValue(-1)
        self.commit = commit
        # Cached bounding rect and the font it was computed for. The rect only
        # depends on the (immutable) tags and the label font, so it is computed
        # once and reused -- Qt queries boundingRect() often (indexing, hit
        # testing, paint culling) and the per-tag QPainterPath work below is
        # expensive to repeat.
        self._bounding_rect = None
        self._bounding_rect_font = None

    def type(self):
        return self.item_type

    def boundingRect(self, cache=Cache):
        font = cache.label_font()
        if self._bounding_rect is not None and self._bounding_rect_font is font:
            return self._bounding_rect

        QPainterPath = QtGui.QPainterPath
        QRectF = QtCore.QRectF

        width = 72
        height = 18
        current_width = 0
        spacing = self.item_spacing
        border_x = self.border + self.text_x_offset
        border_y = self.border + self.text_y_offset

        item_shape = QPainterPath()

        base_rect = QRectF(0, 0, width, height)
        base_rect = base_rect.adjusted(-border_x, -border_y, border_x, border_y)
        item_shape.addRect(base_rect)

        for tag in self.commit.tags:
            text_shape = QPainterPath()
            text_shape.addText(current_width, 0, font, tag)
            text_rect = text_shape.boundingRect()
            box_rect = text_rect.adjusted(-border_x, -border_y, border_x, border_y)
            item_shape.addRect(box_rect)
            current_width = item_shape.boundingRect().width() + spacing

        self._bounding_rect = item_shape.boundingRect()
        self._bounding_rect_font = font
        return self._bounding_rect

    def paint(self, painter, _option, _widget, cache=Cache):
        # Draw tags and branches
        font = cache.label_font()
        painter.setFont(font)

        current_width = 3
        border = self.border
        x_offset = self.text_x_offset
        y_offset = self.text_y_offset
        spacing = self.item_spacing
        QRectF = QtCore.QRectF

        HEAD = 'HEAD'
        remotes_prefix = 'remotes/'
        tags_prefix = 'tags/'
        heads_prefix = 'heads/'
        remotes_len = len(remotes_prefix)
        tags_len = len(tags_prefix)
        heads_len = len(heads_prefix)

        for tag in self.commit.tags:
            if tag == HEAD:
                painter.setPen(self.text_pen)
                painter.setBrush(self.remote_color)
            elif tag.startswith(remotes_prefix):
                tag = tag[remotes_len:]
                painter.setPen(self.text_pen)
                painter.setBrush(self.other_color)
            elif tag.startswith(tags_prefix):
                tag = tag[tags_len:]
                painter.setPen(self.text_pen)
                painter.setBrush(self.remote_color)
            elif tag.startswith(heads_prefix):
                tag = tag[heads_len:]
                painter.setPen(self.head_pen)
                painter.setBrush(self.head_color)
            else:
                painter.setPen(self.text_pen)
                painter.setBrush(self.other_color)

            text_rect = painter.boundingRect(
                QRectF(current_width, 0, 0, 0), Qt.TextSingleLine, tag
            )
            box_rect = text_rect.adjusted(-x_offset, -y_offset, x_offset, y_offset)

            painter.drawRoundedRect(box_rect, border, border)
            painter.drawText(text_rect, Qt.TextSingleLine, tag)
            current_width += text_rect.width() + spacing


class GraphView(QtWidgets.QGraphicsView, ViewerMixin):
    commits_selected = Signal(object)
    diff_commits = Signal(object, object)
    search_line_range_in_oid = Signal(object)

    x_adjust = int(Commit.commit_radius * 4 / 3)
    y_adjust = int(Commit.commit_radius * 4 / 3)

    x_off = -18
    y_off = -20

    def __init__(self, context, parent):
        QtWidgets.QGraphicsView.__init__(self, parent)
        ViewerMixin.__init__(self)
        theme = context.app.theme
        EdgeColor.update_colors(theme.is_dark or bool(theme.is_palette_dark))
        highlight = theme.selection_color()
        Commit.commit_selected_color = highlight
        Commit.selected_outline_color = highlight.darker()

        self.context = context
        self.columns = {}
        self.menu_actions = None
        self.commits = []
        self.items = {}
        self._selected_oids = frozenset()
        """The oids this graph shows as selected, to skip the echo of its own selection"""
        self.mouse_start = [0, 0]
        self.saved_matrix = self.transform()
        self.max_column = 0
        self.min_column = 0
        self.frontier = {}
        # row -> the columns of the tagged commits in that row
        self.tagged_rows = {}

        self.x_start = 24
        self.x_min = 24
        self.x_offsets = collections.defaultdict(lambda: self.x_min)

        self.is_panning = False
        self.pressed = False
        self.selecting = False
        self.last_mouse = [0, 0]
        self.zoom = 2
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)

        scene = QtWidgets.QGraphicsScene(self)
        scene.setItemIndexMethod(QtWidgets.QGraphicsScene.BspTreeIndex)
        scene.selectionChanged.connect(self.selection_changed, type=Qt.QueuedConnection)
        self.setScene(scene)

        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.SmartViewportUpdate)
        self.setCacheMode(QtWidgets.QGraphicsView.CacheBackground)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.NoAnchor)

        background_color = qtutils.css_color(context.app.theme.background_color_rgb())
        self.setBackgroundBrush(background_color)

        qtutils.add_action(
            self,
            N_('Zoom In'),
            self.zoom_in,
            hotkeys.ZOOM_IN,
            hotkeys.ZOOM_IN_SECONDARY,
        )

        qtutils.add_action(self, N_('Zoom Out'), self.zoom_out, hotkeys.ZOOM_OUT)

        qtutils.add_action(self, N_('Zoom to Fit'), self.zoom_to_fit, hotkeys.FIT)

        qtutils.add_action(
            self, N_('Select Parent'), self._select_parent, hotkeys.MOVE_DOWN_TERTIARY
        )

        qtutils.add_action(
            self,
            N_('Select Oldest Parent'),
            self._select_oldest_parent,
            hotkeys.MOVE_DOWN,
        )

        qtutils.add_action(
            self, N_('Select Child'), self._select_child, hotkeys.MOVE_UP_TERTIARY
        )

        qtutils.add_action(
            self, N_('Select Newest Child'), self._select_newest_child, hotkeys.MOVE_UP
        )

    def refresh_appearance(self) -> None:
        """Update palette-derived colors after a system appearance change."""
        theme = self.context.app.theme
        background_color = qtutils.css_color(theme.background_color_rgb())
        self.setBackgroundBrush(background_color)
        # Read the darkness from the live palette: the theme object may not
        # have been rebuilt yet.
        palette_dark = self.palette().window().color().lightnessF() < 0.5
        EdgeColor.update_colors(theme.is_dark or palette_dark)
        for item in self.scene().items():
            if isinstance(item, Edge):
                item.refresh_color()
        highlight = theme.selection_color()
        Commit.commit_selected_color = highlight
        Commit.selected_outline_color = highlight.darker()
        self.viewport().update()

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.PaletteChange:
            # Run after the application has rebuilt its theme for the new
            # palette (App.refresh_system_appearance is also queued).
            QtCore.QTimer.singleShot(0, self.refresh_appearance)
        super().changeEvent(event)

    def clear(self):
        EdgeColor.reset()
        self.scene().clear()
        self.scene().invalidate()
        self.items.clear()
        self.x_offsets.clear()
        self.x_min = 24
        self.commits = []
        self._selected_oids = frozenset()

    # ViewerMixin interface
    def selected_items(self):
        """Return the currently selected items"""
        return self.scene().selectedItems()

    def zoom_in(self):
        self.scale_view(1.5)

    def zoom_out(self):
        self.scale_view(1.0 / 1.5)

    def selection_changed(self):
        # Broadcast selection to other widgets
        selected_items = self.scene().selectedItems()
        commits = sort_by_generation([item.commit for item in selected_items])
        self._selected_oids = frozenset(commit.oid for commit in commits)
        self.set_selecting(True)
        self.commits_selected.emit(commits)
        self.set_selecting(False)

    def select_commits(self, commits):
        # The graph's own selection comes back through the queued
        # GitDAG.commits_selected after "selecting" is reset: skip it.
        if self.selecting or self._selected_oids == {c.oid for c in commits}:
            return
        with qtutils.BlockSignals(self.scene()):
            self.select([commit.oid for commit in commits])

    def select(self, oids):
        """Select the item for the oids"""
        self.scene().clearSelection()
        last = None
        selected = set()
        for oid in oids:
            try:
                item = self.items[oid]
            except KeyError:
                continue
            item.setSelected(True)
            selected.add(item.commit.oid)
            last = item
        self._selected_oids = frozenset(selected)
        # Scroll once: each ensureVisible() can scroll, and only the last counts.
        if last is not None:
            self.ensureVisible(last.sceneTransform().mapRect(last.boundingRect()))

    def _get_item_by_generation(self, commits, criteria_func):
        """Return the item for the commit matching criteria"""
        if not commits:
            return None
        generation = None
        for commit in commits:
            if generation is None or criteria_func(generation, commit.generation):
                oid = commit.oid
                generation = commit.generation
        try:
            return self.items[oid]
        except KeyError:
            return None

    def _oldest_item(self, commits):
        """Return the item for the commit with the oldest generation number"""
        return self._get_item_by_generation(commits, lambda a, b: a > b)

    def _newest_item(self, commits):
        """Return the item for the commit with the newest generation number"""
        return self._get_item_by_generation(commits, lambda a, b: a < b)

    def create_patch(self):
        items = self.selected_items()
        if not items:
            return
        context = self.context
        selected_commits = sort_by_generation([n.commit for n in items])
        oids = [commit.oid for commit in selected_commits]
        all_oids = [commit.oid for commit in sort_by_generation(self.commits)]
        cmds.do(cmds.FormatPatch, context, oids, all_oids)

    def _select_parent(self):
        """Select the parent with the newest generation number"""
        selected_item = self.selected_item()
        if selected_item is None:
            return
        parent_item = self._newest_item(selected_item.commit.parents)
        if parent_item is None:
            return
        selected_item.setSelected(False)
        parent_item.setSelected(True)
        self.ensureVisible(parent_item.mapRectToScene(parent_item.boundingRect()))

    def _select_oldest_parent(self):
        """Select the parent with the oldest generation number"""
        selected_item = self.selected_item()
        if selected_item is None:
            return
        parent_item = self._oldest_item(selected_item.commit.parents)
        if parent_item is None:
            return
        selected_item.setSelected(False)
        parent_item.setSelected(True)
        scene_rect = parent_item.mapRectToScene(parent_item.boundingRect())
        self.ensureVisible(scene_rect)

    def _select_child(self):
        """Select the child with the oldest generation number"""
        selected_item = self.selected_item()
        if selected_item is None:
            return
        child_item = self._oldest_item(selected_item.commit.children)
        if child_item is None:
            return
        selected_item.setSelected(False)
        child_item.setSelected(True)
        scene_rect = child_item.mapRectToScene(child_item.boundingRect())
        self.ensureVisible(scene_rect)

    def _select_newest_child(self):
        """Select the Nth child with the newest generation number (N > 1)"""
        selected_item = self.selected_item()
        if selected_item is None:
            return
        if len(selected_item.commit.children) > 1:
            children = selected_item.commit.children[1:]
        else:
            children = selected_item.commit.children
        child_item = self._newest_item(children)
        if child_item is None:
            return
        selected_item.setSelected(False)
        child_item.setSelected(True)
        scene_rect = child_item.mapRectToScene(child_item.boundingRect())
        self.ensureVisible(scene_rect)

    def set_initial_view(self):
        items = []
        selected = self.selected_items()
        if selected:
            items.extend(selected)

        if not selected and self.commits:
            commit = self.commits[-1]
            items.append(self.items[commit.oid])

        bounds = self.scene().itemsBoundingRect()
        bounds.adjust(-64, 0, 0, 0)
        self.setSceneRect(bounds)
        self.fit_view_to_items(items)

    def zoom_to_fit(self):
        """Fit selected items into the viewport"""
        items = self.selected_items()
        self.fit_view_to_items(items)

    def fit_view_to_items(self, items):
        if not items:
            rect = self.scene().itemsBoundingRect()
        else:
            x_min = y_min = sys.maxsize
            x_max = y_max = -sys.maxsize

            for item in items:
                pos = item.pos()
                x_val = pos.x()
                y_val = pos.y()
                x_min = min(x_min, x_val)
                x_max = max(x_max, x_val)
                y_min = min(y_min, y_val)
                y_max = max(y_max, y_val)

            rect = QtCore.QRectF(x_min, y_min, abs(x_max - x_min), abs(y_max - y_min))

        x_adjust = abs(GraphView.x_adjust)
        y_adjust = abs(GraphView.y_adjust)

        count = max(2.0, 10.0 - len(items) / 2.0)
        y_offset = int(y_adjust * count)
        x_offset = int(x_adjust * count)
        rect.setX(rect.x() - x_offset // 2)
        rect.setY(rect.y() - y_adjust // 2)
        rect.setHeight(rect.height() + y_offset)
        rect.setWidth(rect.width() + x_offset)

        self.fitInView(rect, Qt.KeepAspectRatio)
        self.scene().invalidate()

    def handle_event(self, event_handler, event, update=True):
        event_handler(self, event)
        if update:
            self.update()

    def set_selecting(self, selecting):
        self.selecting = selecting

    def pan(self, event):
        pos = event.pos()
        x_offset = pos.x() - self.mouse_start[0]
        y_offset = pos.y() - self.mouse_start[1]

        if x_offset == 0 and y_offset == 0:
            return

        rect = QtCore.QRect(0, 0, abs(x_offset), abs(y_offset))
        delta = self.mapToScene(rect).boundingRect()

        x_translate = delta.width()
        if x_offset < 0.0:
            x_translate = -x_translate

        y_translate = delta.height()
        if y_offset < 0.0:
            y_translate = -y_translate

        matrix = self.transform()
        matrix.reset()
        matrix *= self.saved_matrix
        matrix.translate(x_translate, y_translate)

        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setTransform(matrix)

    def wheel_zoom(self, event):
        """Handle mouse wheel zooming."""
        delta = qtcompat.wheel_delta(event)
        zoom = math.pow(2.0, delta / 512.0)
        factor = (
            self.transform()
            .scale(zoom, zoom)
            .mapRect(QtCore.QRectF(0.0, 0.0, 1.0, 1.0))
            .width()
        )
        if factor < 0.014 or factor > 42.0:
            return
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.zoom = zoom
        self.scale(zoom, zoom)

    def wheel_pan(self, event):
        """Handle mouse wheel panning."""
        unit = QtCore.QRectF(0.0, 0.0, 1.0, 1.0)
        factor = 1.0 / self.transform().mapRect(unit).width()
        tx, ty = qtcompat.wheel_translation(event)

        matrix = self.transform().translate(tx * factor, ty * factor)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setTransform(matrix)

    def scale_view(self, scale):
        factor = (
            self.transform()
            .scale(scale, scale)
            .mapRect(QtCore.QRectF(0, 0, 1, 1))
            .width()
        )
        if factor < 0.07 or factor > 100.0:
            return
        self.zoom = scale

        adjust_scrollbars = False
        scrollbar = self.verticalScrollBar()
        scrollbar_offset = 1.0
        if scrollbar:
            value = get(scrollbar)
            minimum = scrollbar.minimum()
            maximum = scrollbar.maximum()
            scrollbar_range = maximum - minimum
            distance = value - minimum
            nonzero_range = scrollbar_range > 0.1
            if nonzero_range:
                scrollbar_offset = distance / scrollbar_range
                adjust_scrollbars = True

        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.scale(scale, scale)

        scrollbar = self.verticalScrollBar()
        if scrollbar and adjust_scrollbars:
            minimum = scrollbar.minimum()
            maximum = scrollbar.maximum()
            scrollbar_range = maximum - minimum
            value = minimum + int(float(scrollbar_range) * scrollbar_offset)
            scrollbar.setValue(value)

    def add_commits(self, commits):
        """Traverse commits and add them to the view."""
        self.commits.extend(commits)
        scene = self.scene()
        for commit in commits:
            item = Commit(commit)
            self.items[commit.oid] = item
            for ref in commit.tags:
                self.items[ref] = item
            scene.addItem(item)

        self.layout_commits()
        self.link(commits)

    def link(self, commits):
        """Create edges linking commits with their parents"""
        scene = self.scene()
        for commit in commits:
            try:
                commit_item = self.items[commit.oid]
            except KeyError:
                continue  # The history is truncated.
            for parent in reversed(commit.parents):
                try:
                    parent_item = self.items[parent.oid]
                except KeyError:
                    continue  # The history is truncated.
                try:
                    edge = parent_item.edges[commit.oid]
                except KeyError:
                    edge = Edge(parent_item, commit_item)
                else:
                    continue
                parent_item.edges[commit.oid] = edge
                commit_item.edges[parent.oid] = edge
                scene.addItem(edge)

    def layout_commits(self):
        positions = self.position_nodes()

        # Each edge is accounted in two commits. Hence, accumulate invalid
        # edges to prevent double edge invalidation.
        invalid_edges = set()

        for oid, (x_val, y_val) in positions.items():
            item = self.items[oid]

            # Compare the coordinates: a QPointF never equals a tuple, which
            # made every node look moved.
            pos = item.pos()
            if pos.x() != x_val or pos.y() != y_val:
                item.setPos(x_val, y_val)

                for edge in item.edges.values():
                    invalid_edges.add(edge)

        for edge in invalid_edges:
            edge.commits_were_invalidated()

    # Commit node layout technique
    #
    # Nodes are aligned by a mesh. Columns and rows are distributed using
    # algorithms described below.
    #
    # Row assignment algorithm
    #
    # The algorithm aims consequent.
    #     1. A commit should be above all its parents.
    #     2. No commit should be at right side of a commit with a tag in same row.
    # This prevents overlapping of tag labels with commits and other labels.
    #     3. Commit density should be maximized.
    #
    #     The algorithm requires that all parents of a commit were assigned column.
    # Nodes must be traversed in generation ascend order. This guarantees that all
    # parents of a commit were assigned row. So, the algorithm may operate in
    # course of column assignment algorithm.
    #
    #    Row assignment uses frontier. A frontier is a dictionary that contains
    # minimum available row index for each column. It propagates during the
    # algorithm. Set of cells with tags is also maintained to meet second aim.
    #
    #    Initialization is performed by reset_rows method. Each new column should
    # be declared using declare_column method. Getting row for a cell is
    # implemented in alloc_cell method. Frontier must be propagated for any child
    # of fork commit which occupies different column. This meets first aim.
    #
    # Column assignment algorithm
    #
    #     The algorithm traverses nodes in generation ascend order. This guarantees
    # that a node will be visited after all its parents.
    #
    #     The set of occupied columns are maintained during work. Initially it is
    # empty and no node occupied a column. Empty columns are allocated on demand.
    # Free index for column being allocated is searched in following way.
    #     1. Start from desired column and look towards graph center (0 column).
    #     2. Start from center and look in both directions simultaneously.
    # Desired column is defaulted to 0. Fork node should set desired column for
    # children equal to its one. This prevents branch from jumping too far from
    # its fork.
    #
    #     Initialization is performed by reset_columns method. Column allocation is
    # implemented in alloc_column method. Initialization and main loop are in
    # recompute_grid method. The method also embeds row assignment algorithm by
    # implementation.
    #
    # Actions for each node are follow.
    #     1. If the node was not assigned a column then it is assigned empty one.
    #     2. Allocate row.
    #     3. Allocate columns for children.
    #     If a child have a column assigned then it should no be overridden. One of
    # children is assigned same column as the node. If the node is a fork then the
    # child is chosen in generation descent order. This is a heuristic and it only
    # affects resulting appearance of the graph. Other children are assigned empty
    # columns in same order. It is the heuristic too.
    #     4. If no child occupies column of the node then leave it.
    #     It is possible in consequent situations.
    #     4.1 The node is a leaf.
    #     4.2 The node is a fork and all its children are already assigned side
    # column. It is possible if all the children are merges.
    #     4.3 Single node child is a merge that is already assigned a column.
    #     5. Propagate frontier with respect to this node.
    #     Each frontier entry corresponding to column occupied by any node's child
    # must be gather than node row index. This meets first aim of the row
    # assignment algorithm.
    #     Note that frontier of child that occupies same row was propagated during
    # step 2. Hence, it must be propagated for children on side columns.

    def reset_columns(self):
        # Some children of displayed commits might not be accounted in
        # 'commits' list. It is common case during loading of big graph.
        # But, they are assigned a column that must be reset. Hence, use
        # depth-first traversal to reset all columns assigned.
        for node in self.commits:
            if node.column is None:
                continue
            stack = [node]
            while stack:
                node = stack.pop()
                node.column = None
                for child in node.children:
                    if child.column is not None:
                        stack.append(child)

        self.columns = {}
        self.max_column = 0
        self.min_column = 0

    def reset_rows(self):
        self.frontier = {}
        # row -> the columns of the tagged commits in that row
        self.tagged_rows = {}

    def declare_column(self, column):
        if self.frontier:
            # Align new column frontier by frontier of nearest column. If all
            # columns were left then select maximum frontier value.
            if not self.columns:
                self.frontier[column] = max(self.frontier.values())
                return
            # This is heuristic that mostly affects roots. Note that the
            # frontier values for fork children will be overridden in course of
            # propagate_frontier.
            for offset in itertools.count(1):
                for value in (column + offset, column - offset):
                    if value not in self.columns:
                        # Column is not occupied.
                        continue
                    try:
                        frontier = self.frontier[value]
                    except KeyError:
                        # Column 'c' was never allocated.
                        continue

                    frontier -= 1
                    # The frontier of the column may be higher because of
                    # tag overlapping prevention performed for previous head.
                    try:
                        if self.frontier[column] >= frontier:
                            break
                    except KeyError:
                        pass

                    self.frontier[column] = frontier
                    break
                else:
                    continue
                break
        else:
            # First commit must be assigned 0 row.
            self.frontier[column] = 0

    def alloc_column(self, column=0):
        columns = self.columns
        # First, look for free column by moving from desired column to graph
        # center (column 0).
        for col in range(column, 0, -1 if column > 0 else 1):
            if col not in columns:
                if col > self.max_column:
                    self.max_column = col
                elif col < self.min_column:
                    self.min_column = col
                break
        else:
            # If no free column was found between graph center and desired
            # column then look for free one by moving from center along both
            # directions simultaneously.
            for col in itertools.count(0):
                if col not in columns:
                    self.max_column = max(self.max_column, col)
                    break
                col = -col
                if col not in columns:
                    self.min_column = min(self.min_column, col)
                    break
        self.declare_column(col)
        columns[col] = 1
        return col

    def alloc_cell(self, column, tags):
        # Get empty cell from frontier.
        cell_row = self.frontier[column]

        if tags:
            # Prevent overlapping of tag with cells already allocated a row.
            if self.x_off > 0:
                can_overlap = list(range(column + 1, self.max_column + 1))
            else:
                can_overlap = list(range(column - 1, self.min_column - 1, -1))
            for value in can_overlap:
                frontier = self.frontier[value]
                cell_row = max(cell_row, frontier)

        # Avoid overlapping with tags of commits at cell_row.
        if self.x_off > 0:
            can_overlap = range(self.min_column, column)
        else:
            can_overlap = range(self.max_column, column, -1)
        # Only the few tagged columns in a row are checked, rather than every
        # column that could overlap, which is slow on a wide graph.
        for cell_row in itertools.count(cell_row):
            tagged = self.tagged_rows.get(cell_row)
            if not tagged or not any(value in can_overlap for value in tagged):
                # No overlapping was found.
                break
            # Overlapping. Try next row.

        if tags:
            self.tagged_rows.setdefault(cell_row, set()).add(column)

        # Propagate frontier.
        self.frontier[column] = cell_row + 1
        return cell_row

    def propagate_frontier(self, column, value):
        current = self.frontier[column]
        if current < value:
            self.frontier[column] = value

    def leave_column(self, column):
        count = self.columns[column]
        if count == 1:
            del self.columns[column]
        else:
            self.columns[column] = count - 1

    def recompute_grid(self):
        self.reset_columns()
        self.reset_rows()

        for node in sort_by_generation(list(self.commits)):
            if node.column is None:
                # Node is either root or its parent is not in items. This
                # happens when tree loading is in progress. Allocate new
                # columns for such nodes.
                node.column = self.alloc_column()

            node.row = self.alloc_cell(node.column, node.tags)

            # Allocate columns for children which are still without one. Also
            # propagate frontier for children.
            if node.is_fork():
                sorted_children = sorted(
                    node.children, key=lambda c: c.generation, reverse=True
                )
                citer = iter(sorted_children)
                for child in citer:
                    if child.column is None:
                        # Top most child occupies column of parent.
                        child.column = node.column
                        # Note that frontier is propagated in course of
                        # alloc_cell.
                        break
                    self.propagate_frontier(child.column, node.row + 1)
                else:
                    # No child occupies same column.
                    self.leave_column(node.column)
                    # Note that the loop below will pass no iteration.

                # Rest children are allocated new column.
                for child in citer:
                    if child.column is None:
                        child.column = self.alloc_column(node.column)
                    self.propagate_frontier(child.column, node.row + 1)
            elif node.children:
                child = node.children[0]
                if child.column is None:
                    child.column = node.column
                    # Note that frontier is propagated in course of alloc_cell.
                elif child.column != node.column:
                    # Child node have other parents and occupies column of one
                    # of them.
                    self.leave_column(node.column)
                    # But frontier must be propagated with respect to this
                    # parent.
                    self.propagate_frontier(child.column, node.row + 1)
            else:
                # This is a leaf node.
                self.leave_column(node.column)

    def position_nodes(self):
        self.recompute_grid()

        x_start = self.x_start
        x_min = self.x_min
        x_off = self.x_off
        y_off = self.y_off

        positions = {}

        for node in self.commits:
            x_val = x_start + node.column * x_off
            y_val = y_off + node.row * y_off

            positions[node.oid] = (x_val, y_val)
            x_min = min(x_min, x_val)

        self.x_min = x_min

        return positions

    # Qt overrides
    def viewportEvent(self, event):
        # Tooltips are built lazily: materialise the hovered node's tooltip
        # just before Qt's stock help-event machinery shows it. items() (not
        # itemAt()) copes with a Label or Edge overlapping the node.
        if event.type() == QtCore.QEvent.ToolTip:
            for item in self.items(event.pos()):
                if isinstance(item, Commit):
                    item.ensure_tooltip()
                    break
        return super().viewportEvent(event)

    def contextMenuEvent(self, event):
        self.context_menu_event(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            pos = event.pos()
            self.mouse_start = [pos.x(), pos.y()]
            self.saved_matrix = self.transform()
            self.is_panning = True
            return
        if event.button() == Qt.RightButton:
            event.ignore()
            return
        if event.button() == Qt.LeftButton:
            self.pressed = True
        self.handle_event(QtWidgets.QGraphicsView.mousePressEvent, event)

    def mouseMoveEvent(self, event):
        if self.is_panning:
            self.pan(event)
            return
        pos = self.mapToScene(event.pos())
        self.last_mouse[0] = pos.x()
        self.last_mouse[1] = pos.y()
        self.handle_event(QtWidgets.QGraphicsView.mouseMoveEvent, event, update=False)

    def mouseReleaseEvent(self, event):
        self.pressed = False
        if event.button() == Qt.MiddleButton:
            self.is_panning = False
            return
        self.handle_event(QtWidgets.QGraphicsView.mouseReleaseEvent, event)
        self.viewport().update()

    def wheelEvent(self, event):
        """Handle Qt mouse wheel events."""
        if event.modifiers() & Qt.ControlModifier:
            self.wheel_zoom(event)
        else:
            self.wheel_pan(event)

    def fitInView(self, rect, flags=Qt.IgnoreAspectRatio):
        """Override fitInView to remove unwanted margins

        https://bugreports.qt.io/browse/QTBUG-42331 - based on QT sources

        """
        if self.scene() is None or rect.isNull():
            return
        unity = self.transform().mapRect(QtCore.QRectF(0, 0, 1, 1))
        self.scale(1.0 / unity.width(), 1.0 / unity.height())
        view_rect = self.viewport().rect()
        scene_rect = self.transform().mapRect(rect)
        xratio = view_rect.width() / scene_rect.width()
        yratio = view_rect.height() / scene_rect.height()
        if flags == Qt.KeepAspectRatio:
            xratio = yratio = min(xratio, yratio)
        elif flags == Qt.KeepAspectRatioByExpanding:
            xratio = yratio = max(xratio, yratio)
        self.scale(xratio, yratio)
        self.centerOn(rect.center())


def row_runs(rows):
    """Yield (first, last) for each run of consecutive rows in sorted rows"""
    start = prev = None
    for row in rows:
        if start is None:
            start = prev = row
        elif row == prev + 1:
            prev = row
        else:
            yield start, prev
            start = prev = row
    if start is not None:
        yield start, prev


def sort_by_generation(commits):
    """Sort commits by their generation. Ensures consistent diffs and patch exports"""
    if len(commits) <= 1:
        return commits
    commits.sort(key=lambda x: x.generation)
    return commits


# Glossary
# ========
# oid -- Git objects IDs (i.e. SHA-1 / SHA-256 IDs)
# ref -- Git references that resolve to a commit-ish (HEAD, branches, tags)
# oid -- Git objects IDs (i.e. SHA-1 / SHA-256 IDs)
# ref -- Git references that resolve to a commit-ish (HEAD, branches, tags)
