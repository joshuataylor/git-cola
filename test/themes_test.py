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


class _FakeAppKit:
    NSAppearanceNameAqua = 'Aqua'
    NSAppearanceNameDarkAqua = 'DarkAqua'
    NSAppearanceNameVibrantLight = 'VibrantLight'
    NSAppearanceNameVibrantDark = 'VibrantDark'

    def __init__(self):
        self.applied = []
        fake = self

        class NSAppearance:
            @staticmethod
            def appearanceNamed_(name):
                return name

        class App:
            def setAppearance_(self, appearance):
                fake.applied.append(appearance)

        class NSApplication:
            @staticmethod
            def sharedApplication():
                return App()

        self.NSAppearance = NSAppearance
        self.NSApplication = NSApplication


def test_vibrant_themes_apply_supported_appearances(monkeypatch):
    """Vibrant appearances must not be set on the app (Apple docs)"""
    from cola import themes

    fake = _FakeAppKit()
    monkeypatch.setattr(themes, 'AppKit', fake)
    macos = themes.get_macos_themes()
    assert macos['macos-vibrant-light'].macos_appearance == 'Aqua'
    assert macos['macos-vibrant-dark'].macos_appearance == 'DarkAqua'


def test_flat_dark_theme_sets_dark_window_chrome_on_macos(monkeypatch):
    from cola import themes

    fake = _FakeAppKit()
    monkeypatch.setattr(themes, 'AppKit', fake)
    monkeypatch.setattr(themes.core, 'IS_DARWIN', True)
    by_name = {theme.name: theme for theme in themes.get_all_themes()}
    flat_dark = next(
        theme
        for theme in by_name.values()
        if theme.style_sheet == themes.EStylesheet.FLAT and theme.is_dark
    )
    flat_light = next(
        theme
        for theme in by_name.values()
        if theme.style_sheet == themes.EStylesheet.FLAT and not theme.is_dark
    )
    themes.apply_window_appearance(flat_dark)
    themes.apply_window_appearance(flat_light)
    themes.apply_window_appearance(by_name['default'])
    assert fake.applied == ['DarkAqua', 'Aqua']
