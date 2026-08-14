"""Test the batched metadata walk used by the worktree browser"""
from unittest.mock import Mock

from cola import core
from cola.models import browse

from . import helper
from .helper import app_context

# Prevent unused imports lint errors.
assert app_context is not None


def _meta(oid, age, message, author):
    """Build one metadata token as emitted by the %x00-framed log format"""
    return f'{oid}\x1f{age}\x01{message}\x01{author}'


def test_parse_metadata_log_newest_commit_wins():
    out = (
        '\0'
        + _meta('oid1', '1 minute ago', 'newest', 'Alice')
        + '\0\na/b.txt\0c.txt\0'
        + '\0'
        + _meta('oid2', '2 days ago', 'older', 'Bob')
        + '\0\na/b.txt\0'
    )
    head_oid, metadata, truncated = browse.parse_metadata_log(out, 10)

    assert head_oid == 'oid1'
    assert not truncated
    assert metadata['a/b.txt'] == ('1 minute ago', 'newest', 'Alice')
    assert metadata['c.txt'] == ('1 minute ago', 'newest', 'Alice')
    # Ancestor directories are credited with their newest entry.
    assert metadata['a'] == ('1 minute ago', 'newest', 'Alice')


def test_parse_metadata_log_deep_ancestors_and_older_paths():
    out = (
        '\0'
        + _meta('oid1', 'now', 'new', 'Alice')
        + '\0\na/b/c/d.txt\0'
        + '\0'
        + _meta('oid2', 'then', 'old', 'Bob')
        + '\0\na/b/e.txt\0'
    )
    _, metadata, _ = browse.parse_metadata_log(out, 10)

    assert metadata['a/b/c/d.txt'] == ('now', 'new', 'Alice')
    assert metadata['a/b/c'] == ('now', 'new', 'Alice')
    assert metadata['a/b'] == ('now', 'new', 'Alice')
    assert metadata['a'] == ('now', 'new', 'Alice')
    # The older commit still provides the first metadata for its own path.
    assert metadata['a/b/e.txt'] == ('then', 'old', 'Bob')


def test_parse_metadata_log_handles_empty_diffs_and_truncation():
    # A merge with a suppressed diff emits metadata with no path tokens.
    out = (
        '\0'
        + _meta('merge', 'now', 'merge commit', 'Alice')
        + '\0'
        + '\0'
        + _meta('oid2', 'then', 'change', 'Bob')
        + '\0\na.txt\0'
    )
    head_oid, metadata, truncated = browse.parse_metadata_log(out, 2)

    assert head_oid == 'merge'
    assert truncated  # two commits hit the limit of two
    assert metadata['a.txt'] == ('then', 'change', 'Bob')


def test_parse_metadata_log_empty_output():
    assert browse.parse_metadata_log('', 10) == (None, {}, False)


def _make_model():
    """Build a GitRepoModel with only the batching state, bypassing Qt"""
    model = browse.GitRepoModel.__new__(browse.GitRepoModel)
    model.turbo = False
    model.entries = {}
    model.default_author = 'Default'
    model._metadata = {}
    model._metadata_head = None
    model._metadata_state = browse.METADATA_EMPTY
    model._metadata_truncated = False
    model._metadata_pending = set()
    model._cached_status_sets = (set(), set(), set(), set(), set())
    model._runtask = Mock()
    model.context = Mock()
    # The test paths do not exist on disk, so stat() raises like the real one.
    model.context.ops.stat.side_effect = OSError
    model.apply_data = Mock()  # shadow the method to capture payloads
    return model


def test_update_entry_starts_one_walk_and_queues_misses():
    model = _make_model()
    model.entries = {'a.txt': object(), 'b.txt': object()}

    model.update_entry('a.txt')
    model.update_entry('b.txt')

    assert model._metadata_state == browse.METADATA_RUNNING
    assert model._metadata_pending == {'a.txt', 'b.txt'}
    assert model._runtask.start.call_count == 1
    assert model.apply_data.call_count == 0


def test_apply_metadata_flushes_pending_entries():
    model = _make_model()
    model.entries = {'a.txt': object(), 'missing.txt': object()}
    model._metadata_state = browse.METADATA_RUNNING
    model._metadata_pending = {'a.txt', 'missing.txt'}

    meta = {'a.txt': ('age', 'message', 'author')}
    model._apply_metadata(('head-oid', meta, False))

    assert model._metadata_state == browse.METADATA_COMPLETE
    assert model._metadata_pending == set()
    payloads = {
        call.args[0][0]: call.args[0] for call in model.apply_data.call_args_list
    }
    assert payloads['a.txt'][2:] == ('message', 'author', 'age')
    # Paths without history fall back to mtime metadata.
    assert payloads['missing.txt'][2] == '-'
    assert payloads['missing.txt'][3] == 'Default'


def test_update_entry_uses_per_path_fallback_when_truncated():
    model = _make_model()
    model.entries = {'old.txt': object()}
    model._metadata_state = browse.METADATA_COMPLETE
    model._metadata_truncated = True

    model.update_entry('old.txt')

    assert model.apply_data.call_count == 0
    assert model._runtask.start.call_count == 1
    task = model._runtask.start.call_args.args[0]
    assert isinstance(task, browse.GitRepoInfoTask)


def test_verify_metadata_head_rebuilds_on_new_head():
    model = _make_model()
    model.entries = {'a.txt': object()}
    model._metadata = {'a.txt': ('age', 'message', 'author')}
    model._metadata_head = 'old-head'
    model._metadata_state = browse.METADATA_COMPLETE

    model._verify_metadata_head('old-head')
    assert model._metadata_state == browse.METADATA_COMPLETE
    assert model.apply_data.call_count == 0

    model._verify_metadata_head('new-head')
    assert model._metadata_state == browse.METADATA_RUNNING
    assert model._metadata == {}
    assert model._metadata_pending == {'a.txt'}
    assert model._runtask.start.call_count == 1


def test_gather_metadata_matches_per_path_git_log(app_context):
    """The batched walk reproduces "git log -1 -- <path>" for every path"""
    core.makedirs('sub/dir')
    helper.write_file('sub/dir/deep.txt', 'deep')
    helper.write_file('top.txt', 'top')
    helper.run_git('add', 'sub', 'top.txt')
    helper.run_git('commit', '-m', 'first commit')
    helper.write_file('top.txt', 'changed')
    helper.run_git('add', 'top.txt')
    helper.run_git('commit', '-m', 'second commit')
    helper.run_git('mv', 'top.txt', 'renamed.txt')
    helper.run_git('commit', '-m', 'rename commit')
    helper.write_file('untracked.txt', 'untracked')

    head_oid, metadata, truncated = browse.gather_metadata(app_context)

    assert head_oid == helper.run_git('rev-parse', 'HEAD').strip()
    assert not truncated
    assert 'untracked.txt' not in metadata
    for path in ('sub/dir/deep.txt', 'sub/dir', 'sub', 'renamed.txt'):
        expect = helper.run_git(
            'log', '-1', '--pretty=format:%ar\x01%s\x01%an', '--', path
        )
        assert metadata[path] == tuple(expect.split('\x01', 2)), path
