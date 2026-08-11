import argparse
import os
import plistlib
import sys
import types
from unittest.mock import MagicMock

from cola import app
from cola import core
from qtpy import QtCore

from .helper import app_context

# Prevent unused imports lint errors.
assert app_context is not None


def test_setup_environment():
    # If the function doesn't throw an exception we are happy.
    assert hasattr(app, 'setup_environment')
    app.setup_environment()


def test_add_common_arguments():
    # If the function doesn't throw an exception we are happy.
    parser = argparse.ArgumentParser()
    assert hasattr(app, 'add_common_arguments')
    app.add_common_arguments(parser)


def test_set_application_name_sets_qt_application_name():
    """The Qt application name is set unconditionally on every platform."""
    app.set_application_name('Test App')
    assert QtCore.QCoreApplication.applicationName() == 'Test App'


class _ExplodingAppKit(types.ModuleType):
    """Stand-in module whose attribute access always fails.

    Used to assert that the code under test does NOT reach the AppKit
    branch -- if it does, the test fails with a clear AssertionError.
    """

    def __init__(self):
        super().__init__('AppKit')

    def __getattr__(self, name):
        raise AssertionError(f'AppKit.{name} accessed unexpectedly')


def test_set_application_name_is_a_noop_on_non_darwin(monkeypatch):
    """On non-darwin platforms the Cocoa surfaces are never touched."""
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setitem(sys.modules, 'AppKit', _ExplodingAppKit())

    # Must not raise, must not touch AppKit.
    app.set_application_name('Test App')
    assert QtCore.QCoreApplication.applicationName() == 'Test App'


def test_set_application_name_survives_missing_appkit(monkeypatch):
    """If PyObjC isn't installed, the function returns without crashing."""
    monkeypatch.setattr(sys, 'platform', 'darwin')
    # Force `from AppKit import ...` inside the function to raise ImportError
    # by stashing a non-module object under that key.
    monkeypatch.setitem(sys.modules, 'AppKit', None)

    # Must not raise.
    app.set_application_name('Test App')
    assert QtCore.QCoreApplication.applicationName() == 'Test App'


def _make_fake_appkit():
    """Build a fake AppKit module that captures every Cocoa write we make."""
    fake_info = {}
    info_dict = MagicMock()
    info_dict.setObject_forKey_.side_effect = lambda value, key: fake_info.__setitem__(
        key, value
    )

    bundle = MagicMock()
    bundle.localizedInfoDictionary.return_value = None
    bundle.infoDictionary.return_value = info_dict

    ns_bundle = MagicMock()
    ns_bundle.mainBundle.return_value = bundle

    process_info_instance = MagicMock()
    ns_process_info = MagicMock()
    ns_process_info.processInfo.return_value = process_info_instance

    module = types.ModuleType('AppKit')
    module.NSBundle = ns_bundle
    module.NSProcessInfo = ns_process_info
    return module, fake_info, process_info_instance


def test_set_application_name_patches_cocoa_surfaces_on_darwin(monkeypatch):
    """On darwin we set CFBundleName, CFBundleDisplayName, and processName."""
    monkeypatch.setattr(sys, 'platform', 'darwin')
    fake_appkit, captured_info, process_info_instance = _make_fake_appkit()
    monkeypatch.setitem(sys.modules, 'AppKit', fake_appkit)

    app.set_application_name('Test App')

    assert captured_info == {
        'CFBundleName': 'Test App',
        'CFBundleDisplayName': 'Test App',
    }
    process_info_instance.setProcessName_.assert_called_once_with('Test App')


def test_set_application_name_swallows_cocoa_exceptions(monkeypatch):
    """A failure inside the Cocoa block must not crash app startup.

    The function is best-effort -- it intentionally swallows any AppKit
    exception because a cosmetic naming failure must not prevent git-cola
    from launching.
    """
    monkeypatch.setattr(sys, 'platform', 'darwin')
    fake_appkit, _captured_info, _process_info = _make_fake_appkit()
    # Make every NSBundle call blow up.
    fake_appkit.NSBundle.mainBundle.side_effect = RuntimeError('boom')
    monkeypatch.setitem(sys.modules, 'AppKit', fake_appkit)

    # Must not raise.
    app.set_application_name('Test App')
    # Qt-side name still got set despite the AppKit explosion.
    assert QtCore.QCoreApplication.applicationName() == 'Test App'


# FileOpen handling (macOS "Open With" / drop-on-dock / double-clicked repos)


def test_worktree_for_path_resolves_repo_root(app_context):
    root = core.getcwd()
    assert app.worktree_for_path(root) == core.abspath(root)


def test_worktree_for_path_resolves_subdirectory(app_context):
    root = core.getcwd()
    sub = os.path.join(root, 'sub')
    os.mkdir(sub)
    assert app.worktree_for_path(sub) == core.abspath(root)


def test_worktree_for_path_resolves_file_inside_repo(app_context):
    root = core.getcwd()
    assert app.worktree_for_path(os.path.join(root, 'A')) == core.abspath(root)


def test_worktree_for_path_returns_none_outside_repo(tmp_path):
    assert app.worktree_for_path(str(tmp_path)) is None


def test_worktree_for_path_returns_none_for_empty():
    assert app.worktree_for_path('') is None


def test_open_worktree_spawns_new_window_for_valid_repo(monkeypatch):
    calls = []
    monkeypatch.setattr(app.cmds, 'do', lambda cmd, ctx, arg: calls.append((cmd, arg)))
    context = MagicMock()
    context.git.is_valid.return_value = True

    app.open_worktree(context, '/repo')

    assert calls == [(app.cmds.OpenNewRepo, '/repo')]


def test_open_worktree_adopts_empty_startup_window(monkeypatch):
    calls = []
    monkeypatch.setattr(app.cmds, 'do', lambda cmd, ctx, arg: calls.append((cmd, arg)))
    context = MagicMock()
    context.git.is_valid.return_value = False

    app.open_worktree(context, '/repo')

    assert calls == [(app.cmds.OpenRepo, '/repo')]


def _make_qapplication(context):
    """Build a ColaQApplication without invoking the QApplication singleton."""
    instance = app.ColaQApplication.__new__(app.ColaQApplication)
    instance.context = context
    instance._pending_repo_paths = []
    return instance


def test_open_repo_path_opens_immediately_when_view_exists(monkeypatch, app_context):
    opened = []
    monkeypatch.setattr(app, 'open_worktree', lambda ctx, wt: opened.append(wt))
    context = MagicMock()
    context.view = MagicMock()
    instance = _make_qapplication(context)

    instance.open_repo_path(core.getcwd())

    assert opened == [core.abspath(core.getcwd())]
    assert instance._pending_repo_paths == []


def test_open_repo_path_buffers_until_view_exists(monkeypatch, app_context):
    opened = []
    monkeypatch.setattr(app, 'open_worktree', lambda ctx, wt: opened.append(wt))
    context = MagicMock()
    context.view = None
    instance = _make_qapplication(context)
    root = core.abspath(core.getcwd())

    instance.open_repo_path(root)
    # Buffered, not opened, while the main window does not yet exist.
    assert opened == []
    assert instance._pending_repo_paths == [root]

    # Once the view exists the buffered repository is opened and cleared.
    context.view = MagicMock()
    instance.flush_pending_repo_paths()
    assert opened == [root]
    assert instance._pending_repo_paths == []


def test_open_repo_path_ignores_non_repository(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(app, 'open_worktree', lambda ctx, wt: opened.append(wt))
    context = MagicMock()
    context.view = MagicMock()
    instance = _make_qapplication(context)

    instance.open_repo_path(str(tmp_path))

    assert opened == []
    assert instance._pending_repo_paths == []


def _load_darwin_plist():
    path = os.path.join(
        os.path.dirname(__file__), '..', 'contrib', 'darwin', 'Info.plist'
    )
    with open(path, 'rb') as plist_file:
        return plistlib.load(plist_file)


def test_darwin_plist_defines_a_single_bundle_name():
    """A duplicate CFBundleName is invalid; only the first wins."""
    plist = _load_darwin_plist()
    assert plist['CFBundleName'] == 'Git Cola'


def test_darwin_plist_associates_folders_with_modern_utis():
    """Modern macOS keys folder association off LSItemContentTypes, not OSTypes.

    Without the folder UTIs Finder's 'Open With' and drop-on-dock associations
    are unreliable, which undercuts the FileOpen handling.
    """
    plist = _load_darwin_plist()
    document_types = plist['CFBundleDocumentTypes']
    content_types = document_types[0]['LSItemContentTypes']
    assert 'public.folder' in content_types


def test_set_process_title_imports_lazily(monkeypatch):
    """The setproctitle import happens inside the call, not at module scope

    The title is applied after the UI is up, so startup does not pay for the
    import; a missing package degrades to a no-op.
    """
    calls = []
    fake = types.ModuleType('setproctitle')
    fake.setproctitle = calls.append
    monkeypatch.setitem(sys.modules, 'setproctitle', fake)
    monkeypatch.setattr(sys, 'argv', ['/usr/local/bin/git-cola'])

    app.set_process_title()

    assert calls == ['/usr/local/bin/git-cola']


def test_set_process_title_falls_back_for_unhelpful_argv(monkeypatch):
    """Interpreter-style argv values fall back to the plain app name"""
    calls = []
    fake = types.ModuleType('setproctitle')
    fake.setproctitle = calls.append
    monkeypatch.setitem(sys.modules, 'setproctitle', fake)
    monkeypatch.setattr(sys, 'argv', ['-c'])

    app.set_process_title()

    assert calls == ['git-cola']


def test_set_process_title_survives_missing_package(monkeypatch):
    """A missing setproctitle package is a silent no-op"""
    monkeypatch.setitem(sys.modules, 'setproctitle', None)
    app.set_process_title()
