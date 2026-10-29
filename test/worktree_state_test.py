"""The "git status" worktree reader must match the diff-index/diff-files one"""
import os

import pytest

from cola import core
from cola import gitcmds

from . import helper
from .helper import app_context

# Prevent unused imports lint errors.
assert app_context is not None


@pytest.fixture(autouse=True)
def no_optional_locks(monkeypatch):
    """The app runs git with GIT_OPTIONAL_LOCKS=0, so a read never writes"""
    monkeypatch.setenv('GIT_OPTIONAL_LOCKS', '0')


def _git(*args):
    """Run git where a non-zero exit is expected (e.g. a conflicted merge)"""
    return core.run_command(['git'] + list(args))


def _both(context, update_index=True, display_untracked=True, paths=None):
    """Read the worktree both ways, returning (status, diff)"""
    ignore = gitcmds._ignore_submodules(context)
    by_status = gitcmds.status_files(
        context, update_index, display_untracked, paths, ignore
    )
    by_diff = gitcmds.diff_files_state(
        context, 'HEAD', update_index, display_untracked, paths, ignore
    )
    return by_status, by_diff


def _assert_same(context, **kwargs):
    by_status, by_diff = _both(context, **kwargs)
    assert by_status is not None
    assert by_status == by_diff
    return by_status


def _changes():
    """Staged, unstaged and mixed changes of every kind"""
    helper.write_file('modified', 'one\n')
    helper.write_file('staged', 'one\n')
    helper.write_file('deleted', 'one\n')
    helper.write_file('staged-deleted', 'one\n')
    helper.write_file('both', 'one\n')
    helper.write_file('mode', 'one\n')
    helper.write_file('link', 'one\n')
    helper.run_git('add', '.')
    helper.commit_files()
    helper.write_file('modified', 'two\n')
    helper.write_file('staged', 'two\n')
    helper.run_git('add', 'staged')
    os.remove('deleted')
    helper.run_git('rm', '-q', 'staged-deleted')
    helper.write_file('both', 'two\n')
    helper.run_git('add', 'both')
    helper.write_file('both', 'three\n')
    os.chmod('mode', 0o755)
    os.remove('link')
    os.symlink('modified', 'link')
    helper.write_file('added', 'one\n')
    helper.run_git('add', 'added')
    helper.write_file('added-then-deleted', 'one\n')
    helper.run_git('add', 'added-then-deleted')
    os.remove('added-then-deleted')


def _untracked():
    """Untracked files, directories and ignored files with awkward names"""
    helper.write_file('.gitignore', '*.ignored\nignored-dir/\n')
    helper.run_git('add', '.gitignore')
    helper.commit_files()
    helper.touch('top level.txt', 'unicode-é.txt', '[glob]*.txt', 'x.ignored')
    os.makedirs('newdir/sub/deeper')
    helper.touch('newdir/a', 'newdir/sub/b', 'newdir/sub/deeper/c', 'newdir/d.ignored')
    os.makedirs('[glob-dir]*')
    helper.touch('[glob-dir]*/e')
    os.makedirs('only-ignored')
    helper.touch('only-ignored/f.ignored')
    os.makedirs('ignored-dir')
    helper.touch('ignored-dir/g')
    os.makedirs('empty-dir')
    os.makedirs('tracked-dir')
    helper.touch('tracked-dir/tracked')
    helper.run_git('add', 'tracked-dir/tracked')
    helper.commit_files()
    os.makedirs('tracked-dir/new-sub')
    helper.touch('tracked-dir/untracked', 'tracked-dir/new-sub/h')


def test_changes_match(app_context):
    _changes()
    files = _assert_same(app_context)
    assert 'staged' in files.staged
    assert 'modified' in files.modified
    assert 'both' in files.staged and 'both' in files.modified
    assert 'link' in files.modified
    assert 'mode' in files.modified
    assert files.staged_deleted == {'staged-deleted'}
    assert files.unstaged_deleted == {'added-then-deleted', 'deleted'}


def test_untracked_files_and_directories_match(app_context):
    _untracked()
    files = _assert_same(app_context)
    assert 'newdir/sub/deeper/c' in files.untracked
    assert '[glob-dir]*/e' in files.untracked
    assert 'tracked-dir/new-sub/h' in files.untracked
    assert not any(path.endswith('.ignored') for path in files.untracked)
    assert not any(path.startswith('ignored-dir') for path in files.untracked)


def test_untracked_hidden_matches(app_context):
    _untracked()
    files = _assert_same(app_context, display_untracked=False)
    assert files.untracked == []


@pytest.mark.parametrize(
    'paths',
    [
        ['tracked-dir'],
        ['newdir'],
        ['newdir/sub/b'],
        ['top level.txt', 'newdir/sub'],
        ['missing'],
    ],
)
def test_path_filters_match(app_context, paths):
    _untracked()
    helper.write_file('tracked-dir/tracked', 'changed\n')
    _assert_same(app_context, paths=paths)


def test_nested_repository_matches(app_context):
    helper.commit_files()
    os.makedirs('nested')
    helper.run_git('init', '-q', 'nested')
    helper.touch('nested/inside')
    files = _assert_same(app_context)
    assert 'nested/' in files.untracked


def test_conflicts_match(app_context):
    helper.write_file('both-modified', 'base\n')
    helper.write_file('deleted-by-them', 'base\n')
    helper.run_git('add', '.')
    helper.commit_files()
    helper.run_git('checkout', '-q', '-b', 'other')
    helper.write_file('both-modified', 'other\n')
    helper.run_git('rm', '-q', 'deleted-by-them')
    helper.write_file('both-added', 'other\n')
    helper.run_git('add', '.')
    helper.run_git('commit', '-q', '-m', 'other')
    helper.run_git('checkout', '-q', 'main')
    helper.write_file('both-modified', 'main\n')
    helper.write_file('deleted-by-them', 'main\n')
    helper.write_file('both-added', 'main\n')
    helper.run_git('add', '.')
    helper.run_git('commit', '-q', '-m', 'main')
    status, _, _ = _git('merge', '-q', 'other')
    assert status != 0
    files = _assert_same(app_context)
    assert files.unmerged == ['both-added', 'both-modified', 'deleted-by-them']
    assert files.modified == []


def test_intent_to_add_matches(app_context):
    helper.commit_files()
    helper.write_file('intent', 'one\n')
    helper.run_git('add', '-N', 'intent')
    _assert_same(app_context)


def test_unborn_branch_matches(app_context):
    # initialize_repo() stages A and B without committing them.
    files = _assert_same(app_context)
    assert files.staged == ['A', 'B']


def _submodule_repo():
    helper.commit_files()
    upstream = os.path.abspath(
        os.path.join('..', os.path.basename(os.getcwd()) + '-sub')
    )
    os.makedirs(upstream)
    helper.run_git('-C', upstream, 'init', '-q')
    helper.run_git('-C', upstream, 'config', 'user.name', 'Your Name')
    helper.run_git('-C', upstream, 'config', 'user.email', 'you@example.com')
    helper.run_git('-C', upstream, 'config', 'commit.gpgsign', 'false')
    helper.touch(os.path.join(upstream, 'file'))
    helper.run_git('-C', upstream, 'add', 'file')
    helper.run_git('-C', upstream, 'commit', '-q', '-m', 'sub')
    helper.run_git(
        '-c', 'protocol.file.allow=always', 'submodule', '-q', 'add', upstream, 'sub'
    )
    helper.run_git('commit', '-q', '-m', 'add sub')


@pytest.mark.parametrize('state', ['clean', 'dirty', 'untracked', 'new-commit'])
@pytest.mark.parametrize('ignore', [None, 'all'])
def test_submodules_match(app_context, state, ignore):
    _submodule_repo()
    if state == 'dirty':
        helper.write_file('sub/file', 'changed\n')
    elif state == 'untracked':
        helper.touch('sub/new')
    elif state == 'new-commit':
        helper.write_file('sub/file', 'changed\n')
        helper.run_git('-C', 'sub', 'commit', '-q', '-a', '-m', 'next')
    if ignore:
        helper.run_git('config', 'diff.ignoreSubmodules', ignore)
        app_context.cfg.reset()
    files = _assert_same(app_context)
    # Only a changed submodule is listed. Untracked content does not count.
    changed = state in ('dirty', 'new-commit')
    assert ('sub' in files.submodules) == changed
    assert ('sub' in files.modified) == (changed and not ignore)


def test_stat_only_change_is_hidden_without_a_refresh(app_context):
    """status refreshes in memory, where diff-files needs update_index"""
    helper.commit_files()
    helper.write_file('A', '')
    os.utime('A', (0, 0))
    by_status, by_diff = _both(app_context, update_index=False)
    assert by_status.modified == []
    assert by_diff.modified == ['A']


def test_worktree_state_falls_back_when_amending(app_context):
    """Amend mode diffs the index against HEAD^, which status cannot do"""
    helper.commit_files()
    helper.write_file('A', 'changed\n')
    helper.run_git('commit', '-q', '-a', '-m', 'second')
    state = gitcmds.worktree_state(app_context, head='HEAD^')
    assert state['staged'] == ['A']


def test_worktree_state_falls_back_when_status_fails(app_context, monkeypatch):
    helper.commit_files()
    helper.touch('C')
    monkeypatch.setattr(gitcmds, 'status_files', lambda *args: None)
    assert gitcmds.worktree_state(app_context)['untracked'] == ['C']
