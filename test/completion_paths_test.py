"""Tests for path completion: candidate caching, the item cap and lazy gathering"""
import sys
from unittest.mock import MagicMock

import pytest

from cola import utils
from cola.widgets import completion
from qtpy import QtWidgets

PATHS = [
    'README.md',
    'docs/Guide.txt',
    'docs/api/index.rst',
    'src/main.c',
    'src/Util/strings.c',
    'src/util2/numbers.c',
    'tests/test_main.py',
]


@pytest.fixture(scope='module')
def qapp():
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(sys.argv[:1] if sys.argv else ['test'])
    yield instance


def _old_filter_path_matches(match_text, file_list, case_sensitive):
    """The implementation before candidates were cached, for comparison"""
    files = set(file_list)
    files_and_dirs = utils.add_parents(files)
    dirs = files_and_dirs.difference(files)
    paths = completion.filter_matches(match_text, files_and_dirs, case_sensitive)
    return (paths, dirs)


@pytest.mark.parametrize('text', ['', 'src', 'SRC', 'util', 'Util', '.c', 'nothing'])
@pytest.mark.parametrize('case_sensitive', [False, True])
def test_filter_path_matches_is_unchanged(text, case_sensitive):
    paths = list(PATHS)
    assert completion.filter_path_matches(
        text, paths, case_sensitive
    ) == _old_filter_path_matches(text, paths, case_sensitive)


def test_path_candidates_are_reused_while_the_list_is_unchanged():
    paths = list(PATHS)
    first = completion.path_candidates(paths)
    assert completion.path_candidates(paths) is first
    # A list grown in place, or a new list, gets new candidates.
    paths.append('src/new.c')
    grown = completion.path_candidates(paths)
    assert grown is not first
    assert 'src/new.c' in grown.match('new', False)
    assert completion.path_candidates(list(paths)) is not grown


def test_apply_matches_builds_at_most_the_cap(qapp):
    model = completion.CompletionModel(MagicMock(), None)
    paths = [f'dir/file{idx:05}.txt' for idx in range(1500)]
    model.apply_matches((['main', 'topic'], paths, {'dir'}))
    assert model.rowCount() == 2 + completion.MAX_PATH_COMPLETIONS
    assert model.item(2).text() == paths[0]
    model.dispose()


def test_tracked_paths_are_read_lazily_after_a_refresh(qapp, monkeypatch):
    """A refresh marks the paths stale instead of running git ls-files"""
    calls = []

    def tracked_files(context):
        calls.append(1)
        return list(PATHS)

    monkeypatch.setattr(completion.gitcmds, 'tracked_files', tracked_files)
    model = completion.GitTrackedCompletionModel(MagicMock(), None)
    assert calls == []

    model.gather_matches(False)
    model.gather_matches(False)
    assert calls == [1]

    model.model_updated.emit()
    qapp.processEvents()  # the connection is queued
    assert calls == [1]
    model.gather_matches(False)
    assert calls == [1, 1]
    model.dispose()
