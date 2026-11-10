"""Tests the cola.core module's unicode handling"""
import os

from cola import core
from cola import operations_local

from . import helper


def test_core_decode():
    """Test the core.decode function"""
    filename = helper.fixture('unicode.txt')
    expect = core.decode(core.encode('unicøde'))
    actual = core.read(filename).strip()
    assert expect == actual


def test_core_encode():
    """Test the core.encode function"""
    filename = helper.fixture('unicode.txt')
    expect = core.encode('unicøde')
    actual = core.encode(core.read(filename).strip())
    assert expect == actual


def test_decode_None():
    """Ensure that decode(None) returns None"""
    expect = None
    actual = core.decode(None)
    assert expect == actual


def test_decode_utf8():
    filename = helper.fixture('cyrillic-utf-8.txt')
    actual = core.read(filename)
    assert actual.encoding == 'utf-8'


def test_decode_non_utf8():
    filename = helper.fixture('cyrillic-cp1251.txt')
    actual = core.read(filename)
    assert actual.encoding == 'iso-8859-15'


def test_decode_non_utf8_string():
    filename = helper.fixture('cyrillic-cp1251.txt')
    with open(filename, 'rb') as f:
        content = f.read()
    actual = core.decode(content)
    assert actual.encoding == 'iso-8859-15'


def test_guess_mimetype():
    value = '字龍.txt'
    expect = 'text/plain'
    actual = core.guess_mimetype(value)
    assert expect == actual
    # This function is robust to bytes vs. unicode
    actual = core.guess_mimetype(core.encode(value))
    assert expect == actual


def test_setenv():
    """Test the core.decode function"""
    key = 'COLA_UNICODE_TEST'
    value = '字龍'
    ops = operations_local.LocalOperations()
    core.setenv(ops, key, value)
    assert key in os.environ
    assert os.getenv(key)

    core.unsetenv(ops, key)
    assert key not in os.environ
    assert not os.getenv(key)


def test_app_bundle_contents(monkeypatch):
    monkeypatch.setattr(core, 'IS_DARWIN', True)
    python = '/Applications/git-cola.app/Contents/Resources/bin/python'
    assert core.app_bundle_contents(python) == '/Applications/git-cola.app/Contents'
    assert core.app_bundle_contents('/usr/local/bin/python3') is None
    monkeypatch.setattr(core, 'IS_DARWIN', False)
    assert core.app_bundle_contents(python) is None


def test_cola_argv_starts_new_windows_through_the_bundle(tmp_path, monkeypatch):
    """From git-cola.app another window goes through the bundle's launcher"""
    contents = tmp_path / 'git-cola.app' / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    launcher = contents / 'MacOS' / 'git-cola'
    launcher.write_text('#!/bin/sh\n')
    launcher.chmod(0o755)
    monkeypatch.setattr(core, 'IS_DARWIN', True)
    monkeypatch.setattr(
        core.sys, 'executable', str(contents / 'Resources' / 'bin' / 'python')
    )
    assert core.cola_argv('--repo', '/src') == [str(launcher), '--repo', '/src']

    monkeypatch.setattr(core.sys, 'executable', '/usr/bin/python3')
    monkeypatch.setattr(core.sys, 'argv', ['/usr/local/bin/git-cola'])
    assert core.cola_argv('--repo', '/src') == [
        '/usr/bin/python3',
        '/usr/local/bin/git-cola',
        '--repo',
        '/src',
    ]
