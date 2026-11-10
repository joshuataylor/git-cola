"""Tests for runtime appearance refresh behavior."""
import sys
from unittest.mock import MagicMock

import pytest

from cola import app as cola_app
from cola.widgets.diff import DiffLineNumbers
from cola.widgets.diff import DiffSyntaxHighlighter
from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for widget tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


def _make_palette(*, dark: bool) -> QtGui.QPalette:
    palette = QtGui.QPalette()
    base = QtGui.QColor('#202025' if dark else '#ffffff')
    palette.setColor(QtGui.QPalette.Base, base)
    return palette


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
    )


def _make_context():
    context = MagicMock()
    context.cfg.color.side_effect = lambda _key, default: _hex_to_rgb(default)
    return context


def test_refresh_system_appearance_rebuilds_default_theme():
    """The default theme follows the system palette and must be rebuilt."""
    cola = cola_app.ColaApplication.__new__(cola_app.ColaApplication)
    cola.context = MagicMock()
    cola.context.cfg.get.return_value = 'default'
    cola._install_style = MagicMock()

    cola.refresh_system_appearance()

    cola._install_style.assert_called_once_with(None)


def test_refresh_system_appearance_skips_non_default_theme():
    """User-selected themes replace the palette and must not be overridden."""
    cola = cola_app.ColaApplication.__new__(cola_app.ColaApplication)
    cola.context = MagicMock()
    cola.context.cfg.get.return_value = 'flat-dark-blue'
    cola._install_style = MagicMock()

    cola.refresh_system_appearance()

    cola._install_style.assert_not_called()


def test_diff_syntax_highlighter_uses_light_palette_defaults(qapp):
    context = _make_context()
    doc = QtGui.QTextDocument()
    highlighter = DiffSyntaxHighlighter(context, doc)

    highlighter._configure_colors(context, _make_palette(dark=False))

    assert highlighter.color_add.red() == 0xD2
    assert highlighter.color_add.green() == 0xFF
    assert highlighter.color_add.blue() == 0xE4
    assert highlighter.color_remove.red() == 0xFE
    assert highlighter.color_remove.green() == 0xE0
    assert highlighter.color_remove.blue() == 0xE4


def test_diff_syntax_highlighter_uses_dark_palette_defaults(qapp):
    context = _make_context()
    doc = QtGui.QTextDocument()
    highlighter = DiffSyntaxHighlighter(context, doc)

    highlighter._configure_colors(context, _make_palette(dark=True))

    assert highlighter.color_add.red() == 0x77
    assert highlighter.color_add.green() == 0xAA
    assert highlighter.color_add.blue() == 0x77
    assert highlighter.color_remove.red() == 0xAA
    assert highlighter.color_remove.green() == 0x77
    assert highlighter.color_remove.blue() == 0x77


def test_diff_syntax_highlighter_refresh_palette_rehighlights(qapp):
    context = _make_context()
    doc = QtGui.QTextDocument()
    highlighter = DiffSyntaxHighlighter(context, doc)
    highlighter.rehighlight = MagicMock()

    highlighter.refresh_palette(context)

    highlighter.rehighlight.assert_called_once()


def test_diff_line_numbers_change_event_refreshes_on_palette_change(qapp):
    """DiffLineNumbers must refresh itself on PaletteChange events."""
    context = MagicMock()
    context.cfg.get.return_value = None
    numbers = DiffLineNumbers(context, QtWidgets.QPlainTextEdit())
    numbers.refresh_palette = MagicMock()

    numbers.changeEvent(QtCore.QEvent(QtCore.QEvent.PaletteChange))

    numbers.refresh_palette.assert_called_once()


def _live_theme_app(monkeypatch, config):
    """A ColaApplication whose theme calls are recorded, config from a dict"""
    calls = []
    monkeypatch.setattr(
        cola_app.themes,
        'apply_window_appearance',
        lambda theme: calls.append(('window', theme.name)),
    )
    monkeypatch.setattr(
        cola_app.themes,
        'apply_platform_theme',
        lambda name: calls.append(('platform', name)),
    )
    monkeypatch.setattr(
        cola_app.themes,
        'reset_platform_appearance',
        lambda: calls.append(('reset appearance',)),
    )
    cola = cola_app.ColaApplication.__new__(cola_app.ColaApplication)
    cola.context = MagicMock()
    cola.context.cfg.get = lambda key, default=None: config.get(key, default)
    cola._app = MagicMock()
    cola._app.palette.return_value = QtGui.QPalette()
    cola._app.setPalette.side_effect = lambda palette: calls.append(
        ('palette', palette == QtGui.QPalette())
    )
    cola.theme = None
    cola._palette_set = False
    cola._appearance_set = False
    return cola, calls


def test_theme_change_applies_without_a_restart(qapp, monkeypatch):
    """Changing cola.theme re-themes the running app"""
    config = {'cola.theme': 'default'}
    cola, calls = _live_theme_app(monkeypatch, config)
    cola._install_style(None)
    assert calls == []

    config['cola.theme'] = 'flat-dark-blue'
    cola._config_changed('cola.theme', 'flat-dark-blue')
    assert cola.theme.name == 'flat-dark-blue'
    assert calls == [('palette', False), ('window', 'flat-dark-blue')]

    # Back to the default: the flat theme's palette and appearance are undone.
    del calls[:]
    config['cola.theme'] = 'default'
    cola._config_changed('cola.theme', 'default')
    assert cola.theme.name == 'default'
    assert calls == [('palette', True), ('reset appearance',)]
    assert not cola._palette_set
    assert not cola._appearance_set


def test_other_config_changes_leave_the_theme_alone(qapp, monkeypatch):
    cola, _calls = _live_theme_app(monkeypatch, {'cola.theme': 'default'})
    cola._install_style = MagicMock()
    cola._config_changed('cola.tabwidth', 4)
    cola._install_style.assert_not_called()
    cola._config_changed('cola.boldfonts', True)
    cola._install_style.assert_called_once_with(None)


def test_switching_between_dark_themes_keeps_the_appearance(qapp, monkeypatch):
    """A theme that sets the appearance anyway doesn't reset it first

    Each appearance change announces a palette change, which re-themes the
    app, so resetting and setting it again would theme it twice.
    """
    config = {'cola.theme': 'flat-dark-blue'}
    cola, calls = _live_theme_app(monkeypatch, config)
    cola._install_style(None)
    del calls[:]
    config['cola.theme'] = 'macos-aqua-dark'
    cola._config_changed('cola.theme', 'macos-aqua-dark')
    assert calls == [('palette', True), ('platform', 'macos-aqua-dark')]
    assert cola._appearance_set
    assert not cola._palette_set


def test_refresh_system_appearance_rebuilds_macos_themes():
    """The macOS themes' palette arrives after the appearance they set"""
    cola = cola_app.ColaApplication.__new__(cola_app.ColaApplication)
    cola.context = MagicMock()
    cola.context.cfg.get.return_value = 'macos-aqua-dark'
    cola._install_style = MagicMock()

    cola.refresh_system_appearance()

    cola._install_style.assert_called_once_with(None)
