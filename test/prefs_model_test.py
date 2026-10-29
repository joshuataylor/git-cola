"""Tests for cola.models.prefs defaults"""
import sys
from unittest.mock import Mock

from cola.models import prefs


def _context(config=None, executables=()):
    config = config or {}
    context = Mock()
    context.cfg.get.side_effect = lambda key, default=None: config.get(key, default)
    context.ops.find_executable.side_effect = lambda name: (
        f'/usr/bin/{name}' if name in executables else None
    )
    return context


def test_background_editor_opens_the_default_text_editor_on_macos(monkeypatch):
    """macOS ships no gvim, so an unconfigured editor falls back to open -t"""
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', True)
    monkeypatch.setattr(prefs, 'fallback_editor', lambda: prefs.Defaults.editor)
    assert prefs.background_editor(_context()) == 'open -t'


def test_background_editor_keeps_an_installed_gvim_on_macos(monkeypatch):
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', True)
    monkeypatch.setattr(prefs, 'fallback_editor', lambda: prefs.Defaults.editor)
    context = _context(executables=('gvim',))
    assert prefs.background_editor(context) == 'gvim'


def test_background_editor_keeps_a_configured_editor_on_macos(monkeypatch):
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', True)
    context = _context({prefs.EDITOR: 'subl -w'})
    assert prefs.background_editor(context) == 'subl -w'


def test_background_editor_unchanged_off_macos(monkeypatch):
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', False)
    monkeypatch.setattr(prefs, 'fallback_editor', lambda: prefs.Defaults.editor)
    assert prefs.background_editor(_context()) == 'gvim'


def test_default_history_browser_is_the_dag_on_macos(monkeypatch):
    """macOS's git does not ship gitk"""
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', True)
    monkeypatch.setattr(prefs.core, 'IS_WIN32', False)
    browser = prefs.default_history_browser()
    assert browser.endswith(' dag')
    assert sys.executable in browser


def test_default_history_browser_is_gitk_on_linux(monkeypatch):
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', False)
    monkeypatch.setattr(prefs.core, 'IS_WIN32', False)
    assert prefs.default_history_browser() == 'gitk'


def test_blocking_editor_waits_for_textedit_on_macos(monkeypatch):
    """GIT_EDITOR must block until editing is done (rebase reword)"""
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', True)
    monkeypatch.setattr(prefs, 'fallback_editor', lambda: prefs.Defaults.editor)
    assert prefs.editor(_context()) == 'open -W -n -t'


def test_blocking_editor_keeps_configured_or_installed_editors(monkeypatch):
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', True)
    monkeypatch.setattr(prefs, 'fallback_editor', lambda: prefs.Defaults.editor)
    assert prefs.editor(_context(executables=('gvim',))) == 'gvim'
    assert prefs.editor(_context({prefs.EDITOR: 'vim'})) == 'gvim -f'


def test_background_editor_setting_wins(monkeypatch):
    monkeypatch.setattr(prefs.core, 'IS_DARWIN', True)
    context = _context({prefs.BACKGROUND_EDITOR: 'code'})
    assert prefs.background_editor(context) == 'code'


def _config_context(repo, user):
    """A context whose cfg reads and writes the given repo/user dicts"""
    context = Mock()
    context.timestamp = 0
    # Command sets self.model to the MainModel, which has no get_config().
    context.model = Mock(spec=[])
    context.cfg.get_repo.side_effect = repo.get
    context.cfg.set_repo.side_effect = repo.__setitem__
    context.cfg.get.side_effect = lambda key, default=None: user.get(
        key, repo.get(key, default)
    )
    context.cfg.set_user.side_effect = user.__setitem__
    return context


def test_set_config_local_records_the_old_value_and_undoes():
    repo = {prefs.MERGE_SUMMARY: True}
    context = _config_context(repo, {})
    model = prefs.PreferencesModel(context)
    cmd = prefs.SetConfig(context, model, 'local', prefs.MERGE_SUMMARY, False)
    cmd.do()
    assert cmd.old_value is True
    assert repo[prefs.MERGE_SUMMARY] is False
    cmd.undo()
    assert repo[prefs.MERGE_SUMMARY] is True


def test_set_config_global_records_the_old_value_and_undoes():
    user = {prefs.TABWIDTH: 8}
    context = _config_context({}, user)
    model = prefs.PreferencesModel(context)
    cmd = prefs.SetConfig(context, model, 'global', prefs.TABWIDTH, 4)
    cmd.do()
    assert cmd.old_value == 8
    assert user[prefs.TABWIDTH] == 4
    cmd.undo()
    assert user[prefs.TABWIDTH] == 8
