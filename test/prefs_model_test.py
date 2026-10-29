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
