"""Test the cola.settings module"""
import os

import pytest

from cola.settings import Settings

from . import helper


@pytest.fixture(autouse=True)
def settings_fixture():
    """Provide Settings that save into a temporary location to all tests"""
    filename = helper.tmp_path('settings')
    Settings.config_path = filename

    yield Settings.read()

    if os.path.exists(filename):
        os.remove(filename)


def test_gui_save_restore(settings_fixture):
    """Test saving and restoring gui state"""
    settings = settings_fixture
    settings.gui_state['test-gui'] = {'foo': 'bar'}
    settings.save()

    settings = Settings.read()
    state = settings.gui_state.get('test-gui', {})
    assert 'foo' in state
    assert state['foo'] == 'bar'


def test_bookmarks_save_restore():
    """Test the bookmark save/restore feature"""
    # We automatically purge missing entries so we mock-out
    # git.is_git_worktree() so that this bookmark is kept.
    bookmark = {'path': '/tmp/python/thinks/this/exists', 'name': 'exists'}

    def mock_verify(path):
        return path == bookmark['path']

    settings = Settings.read()
    settings.add_bookmark(bookmark['path'], bookmark['name'])
    settings.save()

    settings = Settings.read(verify=mock_verify)

    bookmarks = settings.bookmarks
    assert len(settings.bookmarks) == 1
    assert bookmark in bookmarks

    settings.remove_bookmark(bookmark['path'], bookmark['name'])
    bookmarks = settings.bookmarks
    expect = 0
    actual = len(bookmarks)
    assert expect == actual
    assert bookmark not in bookmarks


def test_bookmarks_removes_missing_entries():
    """Test that missing entries are removed after a reload"""
    # verify returns False so all entries will be removed.
    bookmark = {'path': '.', 'name': 'does-not-exist'}
    settings = Settings.read(verify=lambda x: False)
    settings.add_bookmark(bookmark['path'], bookmark['name'])
    settings.remove_missing_bookmarks()
    settings.save()

    settings = Settings.read()
    bookmarks = settings.bookmarks
    expect = 0
    actual = len(bookmarks)
    assert expect == actual
    assert bookmark not in bookmarks


def test_rename_bookmark():
    settings = Settings.read()
    settings.add_bookmark('/tmp/repo', 'a')
    settings.add_bookmark('/tmp/repo', 'b')
    settings.add_bookmark('/tmp/repo', 'c')

    settings.rename_bookmark('/tmp/repo', 'b', 'test')

    expect = ['a', 'test', 'c']
    actual = [i['name'] for i in settings.bookmarks]
    assert expect == actual


def test_recent_matches_the_same_repo_through_a_symlink(tmp_path, monkeypatch):
    """/tmp/repo and /private/tmp/repo are one repository"""
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(real)
    settings = Settings(verify=lambda path: True)
    settings.values['recent'] = []
    settings.add_recent(str(link), 8)
    settings.add_recent(str(real), 8)
    assert len(settings.recent) == 1


def test_recent_ignores_case_on_macos(tmp_path, monkeypatch):
    from cola import settings as settings_module

    monkeypatch.setattr(settings_module.core, 'IS_DARWIN', True)
    settings = Settings(verify=lambda path: True)
    settings.values['recent'] = []
    settings.add_recent(str(tmp_path / 'Repo'), 8)
    settings.add_recent(str(tmp_path / 'repo'), 8)
    assert len(settings.recent) == 1
    settings.remove_recent(str(tmp_path / 'REPO'))
    assert settings.recent == []


def test_recent_keeps_case_distinct_elsewhere(tmp_path, monkeypatch):
    from cola import settings as settings_module

    monkeypatch.setattr(settings_module.core, 'IS_DARWIN', False)
    settings = Settings(verify=lambda path: True)
    settings.values['recent'] = []
    settings.add_recent(str(tmp_path / 'Repo'), 8)
    settings.add_recent(str(tmp_path / 'repo'), 8)
    assert len(settings.recent) == 2
