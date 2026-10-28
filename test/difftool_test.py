"""Tests for cola.difftool"""
from unittest.mock import MagicMock

import pytest

from cola import core
from cola import difftool


@pytest.fixture
def launch(monkeypatch):
    """Build a LaunchDifftool for one unmerged path, capturing core.fork()"""
    forked = []
    monkeypatch.setattr(core, 'IS_WIN32', False)
    monkeypatch.setattr(core, 'fork', lambda argv, **kwargs: forked.append(argv))
    monkeypatch.setattr(difftool.prefs, 'mergetool', lambda context: 'meld')
    context = MagicMock()
    context.selection.selection.return_value.unmerged = ['conflicted.txt']
    context.cfg.get.side_effect = lambda key, default=None: default
    command = difftool.LaunchDifftool(context)
    return command, context, forked


def test_unmerged_without_a_terminal_runs_mergetool_directly(launch):
    """No terminal emulator (stock macOS) must not crash"""
    command, context, forked = launch
    context.cfg.terminal.return_value = None

    assert command.do()

    assert forked == [[
        'git',
        'mergetool',
        '--no-prompt',
        '--tool=meld',
        '--',
        'conflicted.txt',
    ]]


def test_unmerged_with_a_terminal_runs_mergetool_inside_it(launch):
    command, context, forked = launch
    context.cfg.terminal.return_value = 'xterm -e'

    assert command.do()

    assert forked == [[
        'xterm',
        '-e',
        'git',
        'mergetool',
        '--no-prompt',
        '--tool=meld',
        '--',
        'conflicted.txt',
    ]]
