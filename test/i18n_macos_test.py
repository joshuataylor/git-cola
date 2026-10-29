"""Tests for picking the UI language on macOS without $LANG"""
import pytest

from cola import i18n


@pytest.fixture
def finder_launch(monkeypatch):
    """A Finder/Dock launch: no locale variables at all"""
    for name in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(i18n.core, 'IS_DARWIN', True)
    monkeypatch.setattr(i18n.core, 'IS_WIN32', False)
    monkeypatch.setattr(i18n, 'get_current_locale', lambda: None)


def test_uses_the_first_translated_macos_language(monkeypatch, finder_launch):
    monkeypatch.setattr(
        i18n, 'macos_preferred_languages', lambda: ['xx_XX', 'de_DE', 'fr_FR']
    )
    assert i18n.get_default_locale() == 'de_DE'


def test_english_first_stays_untranslated(monkeypatch, finder_launch):
    monkeypatch.setattr(i18n, 'macos_preferred_languages', lambda: ['en_AU', 'de_DE'])
    assert i18n.get_default_locale() is None


def test_lang_still_wins_over_macos_settings(monkeypatch, finder_launch):
    monkeypatch.setenv('LANG', 'fr_FR.UTF-8')
    monkeypatch.setattr(i18n, 'macos_preferred_languages', lambda: ['de_DE'])
    assert i18n.get_default_locale() == 'fr_FR.UTF-8'


def test_macos_chinese_scripts_map_to_cola_translations(monkeypatch):
    class FakeLocale:
        def uiLanguages(self):
            return ['zh-Hans-CN', 'zh-Hant-TW', 'pt-BR', 'de-Latn-DE', 'de-DE']

    from qtpy import QtCore

    monkeypatch.setattr(QtCore.QLocale, 'system', staticmethod(FakeLocale))
    assert i18n.macos_preferred_languages() == ['zh_CN', 'zh_TW', 'pt_BR', 'de_DE']
