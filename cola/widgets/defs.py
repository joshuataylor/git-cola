import math
import os
import sys

try:
    scale_factor = float(os.getenv('GIT_COLA_SCALE', '1'))
except ValueError:
    scale_factor = 1.0


def scale(value, factor=scale_factor):
    return int(value * factor)


def metrics_for(platform):
    """Return the unscaled dialog layout metrics for a sys.platform name

    macOS uses the Aqua values that Qt's QMacStyle encodes in
    layoutSpacing()/pixelMetric(): 20px dialog edges, 8px between related
    controls, 5px between stacked radio buttons, 16px between groups and
    14px between the last group and the button row, and no extra margin on
    nested layouts.  Every other platform
    keeps the legacy 4px metrics so nothing changes outside macOS.
    """
    if platform == 'darwin':
        return {
            'dialog_margin': 20,
            'control_spacing': 8,
            'radio_spacing': 5,
            'group_spacing': 16,
            'button_row_spacing': 14,
            'inner_margin': 0,
        }
    return {
        'dialog_margin': 4,
        'control_spacing': 4,
        'radio_spacing': 4,
        'group_spacing': 12,
        'button_row_spacing': 12,
        'inner_margin': 12,
    }


platform = sys.platform
# QDialogButtonBox gives the native Accept-right/Reject-left order on macOS.
# Other platforms keep the hand-ordered hbox so their look is unchanged.
native_dialog_buttons = platform == 'darwin'
_metrics = metrics_for(platform)
dialog_margin = scale(_metrics['dialog_margin'])
control_spacing = scale(_metrics['control_spacing'])
radio_spacing = scale(_metrics['radio_spacing'])
group_spacing = scale(_metrics['group_spacing'])
button_row_spacing = scale(_metrics['button_row_spacing'])
# Margin for a nested layout inside a dialog that already has dialog_margin.
inner_margin = scale(_metrics['inner_margin'])


no_margin = 0
small_margin = scale(2)
margin = scale(4)
large_margin = scale(12)

no_spacing = 0
spacing = scale(4)
titlebar_spacing = scale(8)
button_spacing = scale(12)

cursor_width = scale(2)
handle_width = scale(4)
tool_button_height = scale(28)

default_icon = scale(16)
small_icon = scale(12)
action_icon = scale(14)
medium_icon = scale(48)
large_icon = scale(96)
huge_icon = scale(192)

max_size = scale(4096)

border = max(1, scale(0.5))
checkbox = scale(12)
radio = scale(22)

action_text = scale(10)
logo_text = 24

radio_border = max(1, scale(1.0 - (1.0 / math.pi)))

separator = scale(3)

dialog_w = scale(720)
dialog_h = scale(445)

msgbox_h = scale(128)
