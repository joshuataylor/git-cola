"""Tests for the platform-aware dialog layout metrics in cola.widgets.defs"""
import sys

from cola.widgets import defs


def test_metrics_for_darwin_uses_aqua_values():
    metrics = defs.metrics_for('darwin')
    assert metrics == {
        'dialog_margin': 20,
        'control_spacing': 8,
        'radio_spacing': 5,
        'group_spacing': 16,
        'button_row_spacing': 14,
    }


def test_metrics_for_other_platforms_keep_legacy_values():
    # These must stay equal to the historical margin/spacing literals so that
    # the dialog layouts do not change outside macOS.
    for platform in ('linux', 'win32', 'cygwin', 'freebsd13'):
        metrics = defs.metrics_for(platform)
        assert metrics['dialog_margin'] == 4
        assert metrics['control_spacing'] == 4
        assert metrics['radio_spacing'] == 4
        assert metrics['group_spacing'] == 12
        assert metrics['button_row_spacing'] == 12


def test_module_constants_follow_sys_platform():
    metrics = defs.metrics_for(sys.platform)
    assert defs.platform == sys.platform
    assert defs.dialog_margin == defs.scale(metrics['dialog_margin'])
    assert defs.control_spacing == defs.scale(metrics['control_spacing'])
    assert defs.radio_spacing == defs.scale(metrics['radio_spacing'])
    assert defs.group_spacing == defs.scale(metrics['group_spacing'])
    assert defs.button_row_spacing == defs.scale(metrics['button_row_spacing'])
    assert defs.native_dialog_buttons == (sys.platform == 'darwin')
