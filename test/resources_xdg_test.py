"""Tests for the XDG base directory helpers"""
import os

import pytest

from cola import resources


@pytest.mark.parametrize(
    ('function', 'variable', 'default'),
    [
        (resources.xdg_config_home, 'XDG_CONFIG_HOME', ('.config',)),
        (resources.xdg_data_home, 'XDG_DATA_HOME', ('.local', 'share')),
    ],
)
@pytest.mark.parametrize('value', ['', 'relative/dir'])
def test_empty_or_relative_xdg_dirs_fall_back_to_home(
    monkeypatch, function, variable, default, value
):
    """An empty or relative value must not resolve against the cwd"""
    monkeypatch.setenv('HOME', '/home/user')
    monkeypatch.setenv(variable, value)
    assert function('git-cola') == os.path.join('/home/user', *default, 'git-cola')


@pytest.mark.parametrize(
    ('function', 'variable'),
    [
        (resources.xdg_config_home, 'XDG_CONFIG_HOME'),
        (resources.xdg_data_home, 'XDG_DATA_HOME'),
        (resources.xdg_cache_home, 'XDG_CACHE_HOME'),
    ],
)
def test_absolute_xdg_dirs_are_used(monkeypatch, function, variable):
    monkeypatch.setenv(variable, '/custom')
    assert function('git-cola') == os.path.join('/custom', 'git-cola')


def test_relative_xdg_cache_home_is_ignored(monkeypatch):
    monkeypatch.setenv('HOME', '/home/user')
    monkeypatch.setenv('XDG_CACHE_HOME', 'cache')
    assert not resources.xdg_cache_home().startswith('cache')
