import sys
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from cola import app
from cola import core
from cola import icons
from cola import resources
from qtpy import QtGui
from qtpy import QtWidgets


def test_from_filename_unicode():
    filename = chr(0x400) + '.py'
    expect = 'file-code.svg'
    actual = icons.basename_from_filename(filename)
    assert expect == actual

    actual = icons.basename_from_filename(core.encode(filename))
    assert expect == actual


def test_basename_from_filename_is_memoized():
    """Repeated lookups of the same filename guess the mimetype only once."""
    filename = 'memoize_probe_unique_name.py'
    icons.basename_from_filename.cache.pop((filename,), None)

    with patch('cola.icons.core.guess_mimetype', return_value='text/x-python') as guess:
        first = icons.basename_from_filename(filename)
        second = icons.basename_from_filename(filename)

    assert first == second == 'file-code.svg'
    assert guess.call_count == 1


@pytest.fixture
def qapp():
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(sys.argv[:1] if sys.argv else ['test'])
    palette = instance.palette()
    yield instance
    instance.setPalette(palette)
    icons.palette_changed()


def _set_dark(qapp, dark):
    palette = QtGui.QPalette(qapp.palette())
    window, text = ('#202020', '#f0f0f0') if dark else ('#f0f0f0', '#202020')
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(window))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(text))
    qapp.setPalette(palette)
    icons.palette_changed()


def _image(qicon):
    return qicon.pixmap(32, 32).toImage()


def test_follow_system_icons_switch_with_the_palette(qapp):
    """A live Light/Dark switch redraws cola's icons from the other set"""
    qicon = QtGui.QIcon(icons.FollowSystemIconEngine('check.svg'))
    light_file = QtGui.QIcon(resources.icon_dir('light') + '/check.svg')
    dark_file = QtGui.QIcon(resources.icon_dir('dark') + '/check.svg')

    _set_dark(qapp, False)
    assert not icons.palette_is_dark()
    assert _image(qicon) == _image(light_file)
    _set_dark(qapp, True)
    assert icons.palette_is_dark()
    assert _image(qicon) == _image(dark_file)
    assert _image(light_file) != _image(dark_file)


def test_icon_follows_the_system_only_when_asked(qapp, monkeypatch):
    """Every "icons:" name follows, including the status list's file icons"""
    # memoize keeps one dict on the wrapped function: clear it in place.
    cache = icons.from_name.cache
    saved = dict(cache)
    try:
        cache.clear()
        monkeypatch.setattr(icons, '_follow_system', True)
        assert icons.icon('check.svg') is icons.from_engine('check.svg')
        assert icons.from_filename('main.c') is icons.from_engine('file-code.svg')

        cache.clear()
        monkeypatch.setattr(icons, '_follow_system', False)
        qicon = icons.icon('check.svg')
        assert qicon is not icons.from_engine('check.svg')
        assert qicon is icons.from_name('icons:check.svg')
    finally:
        cache.clear()
        cache.update(saved)


@pytest.mark.parametrize(
    'configured, env, follows',
    [
        ([], '', True),
        (['default'], '', True),
        (['dark'], '', False),
        (['default', 'dark'], '', False),
        ([], 'light', False),
    ],
)
def test_icon_theme_follows_system_unless_one_is_chosen(
    monkeypatch, configured, env, follows
):
    context = MagicMock()
    context.cfg.get_all.return_value = configured
    monkeypatch.setattr(app.core, 'getenv', lambda name, default=None: env or None)
    assert app.icon_theme_follows_system(context) is follows
