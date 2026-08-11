"""Tests for the themes module"""
from cola import icons
from cola import themes


def test_flat_theme_checked_indicators_have_images():
    """Flat themes must paint a check/dot glyph on checked indicators.

    Styling QCheckBox/QRadioButton indicators via QSS stops Qt from drawing the
    native control, so the checked state needs an explicit ``image:`` or it renders
    as a blank filled square (git-cola issue: Flat themes on macOS).
    """
    theme = themes.find_theme('flat-dark-blue')
    css = theme.style_sheet_flat(False)

    assert 'QCheckBox::indicator:checked' in css
    assert f'image: url({icons.check_name()})' in css
    assert f'image: url({icons.dot_name()})' in css
