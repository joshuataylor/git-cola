"""Test the file-history data layer"""
from cola.models import filehistory

from . import helper
from .helper import app_context

# Prevent unused imports lint errors.
assert app_context is not None


def _meta(oid, summary, author='Alice', email='alice@example.com', date='2026-01-01'):
    """Build one metadata token as emitted by the %x00-framed log format"""
    return f'{oid}\x1f{summary}\x1f{author}\x1f{email}\x1f{date}'


def test_parse_follow_log_modify_and_add():
    out = (
        '\0'
        + _meta('oid1', 'modify')
        + '\0\nM\0file.txt\0'
        + '\0'
        + _meta('oid2', 'add')
        + '\0\nA\0file.txt\0'
    )
    entries = filehistory.parse_follow_log(out, 'file.txt')

    assert len(entries) == 2
    assert entries[0].oid == 'oid1'
    assert entries[0].summary == 'modify'
    assert entries[0].author == 'Alice'
    assert entries[0].email == 'alice@example.com'
    assert entries[0].authdate == '2026-01-01'
    assert entries[0].status == 'M'
    assert entries[0].path == 'file.txt'
    assert entries[0].old_path == ''
    assert entries[1].status == 'A'


def test_parse_follow_log_rename_chain():
    out = (
        '\0'
        + _meta('oid1', 'rename')
        + '\0\nR100\0old.txt\0new.txt\0'
        + '\0'
        + _meta('oid2', 'add')
        + '\0\nA\0old.txt\0'
    )
    entries = filehistory.parse_follow_log(out, 'new.txt')

    assert entries[0].status == 'R100'
    assert entries[0].path == 'new.txt'
    assert entries[0].old_path == 'old.txt'
    assert entries[1].path == 'old.txt'
    # Renames diff both sides so git pairs them; plain entries use one path.
    assert filehistory.diff_pathspec(entries[0]) == ('new.txt', 'old.txt')
    assert filehistory.diff_pathspec(entries[1]) == 'old.txt'


def test_parse_follow_log_merge_inherits_neighbouring_path():
    # Merge commits can emit metadata with no status or path tokens. An
    # older pathless entry lives at the newer entry's pre-image path, and a
    # newest pathless entry falls back to the requested path.
    out = (
        '\0'
        + _meta('merge-new', 'newest merge')
        + '\0'
        + _meta('rename', 'rename')
        + '\0\nR100\0old.txt\0new.txt\0'
        + '\0'
        + _meta('merge-old', 'older merge')
        + '\0'
        + _meta('add', 'add')
        + '\0\nA\0old.txt\0'
    )
    entries = filehistory.parse_follow_log(out, 'new.txt')

    assert [entry.oid for entry in entries] == [
        'merge-new',
        'rename',
        'merge-old',
        'add',
    ]
    assert entries[0].path == 'new.txt'  # requested path
    assert entries[2].path == 'old.txt'  # the rename's pre-image path
    assert entries[0].status == ''
    assert entries[2].status == ''


def test_parse_follow_log_empty_output():
    assert filehistory.parse_follow_log('', 'file.txt') == []


def test_load_history_follows_renames(app_context):
    """load_history() returns the full chain across a git mv"""
    helper.write_file('first.txt', 'content')
    helper.run_git('add', 'first.txt')
    helper.run_git('commit', '-m', 'add first.txt')
    helper.write_file('first.txt', 'changed')
    helper.run_git('add', 'first.txt')
    helper.run_git('commit', '-m', 'change first.txt')
    helper.run_git('mv', 'first.txt', 'second.txt')
    helper.run_git('commit', '-m', 'rename to second.txt')

    entries = filehistory.load_history(app_context, 'second.txt')

    assert [entry.summary for entry in entries] == [
        'rename to second.txt',
        'change first.txt',
        'add first.txt',
    ]
    assert entries[0].status.startswith('R')
    assert entries[0].path == 'second.txt'
    assert entries[0].old_path == 'first.txt'
    # Older entries carry their own post-image path, valid for "git show".
    assert entries[1].path == 'first.txt'
    assert entries[2].path == 'first.txt'
    oids = helper.run_git('log', '--pretty=format:%H', '--follow', '--', 'second.txt')
    assert [entry.oid for entry in entries] == oids.splitlines()
