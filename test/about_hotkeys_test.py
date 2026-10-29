"""Tests for the macOS rendering of the keyboard shortcuts page"""
from cola import resources
from cola.widgets import about


def _row(key):
    return f'<td class="shortcut">{key}</td>'


def test_macos_hotkeys_use_cmd_option_and_control():
    html = ''.join(
        _row(key)
        for key in (
            'Ctrl + S',
            'Alt + A',
            'Ctrl + Alt + C',
            'Ctrl + #',
            'Ctrl + Shift + #',
            'Ctrl + Space',
            'J / Alt + J',
        )
    )
    assert about.macos_hotkeys_html(html) == ''.join(
        _row(key)
        for key in (
            'Cmd + S',
            'Option + A',
            'Cmd + Option + C',
            'Control + #',
            'Control + Shift + #',
            'Option + Esc / Control + Space',
            'J / Option + J',
        )
    )


def test_macos_hotkeys_leave_words_containing_alt_alone():
    assert about.macos_hotkeys_html('Alternatively, Altitude') == (
        'Alternatively, Altitude'
    )


def test_macos_hotkeys_page_has_no_ctrl_or_alt_left():
    with open(resources.data_path('hotkeys.html'), encoding='utf-8') as page:
        html = about.macos_hotkeys_html(page.read())
    assert 'Ctrl' not in html
    assert ' Alt ' not in html
    assert 'Cmd + #' not in html
    assert 'Cmd + Space' not in html
