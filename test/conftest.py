"""Pytest configuration for Qt-based tests."""

import os
import sys

import pytest

# Use an offscreen Qt backend in CI and headless local sessions.
# This prevents QApplication from aborting when the display server is unavailable
# or the active platform plugin is unstable.
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QPA_PLATFORMTHEME'] = 'offscreen'

# Qt warnings that mean a painter outlived its paint event or two painters
# shared a device. On macOS the first one came before a crash in
# QBackingStore::flush().
PAINTER_WARNINGS = (
    'called with active painter',
    'can only be painted by one painter at a time',
)
_painter_warnings = []


@pytest.fixture(autouse=True, scope='session')
def _qt_message_log():
    """Record Qt's painter warnings, passing every message on as before"""
    from qtpy import QtCore

    previous = {}

    def handle(mode, context, message):
        if any(warning in message for warning in PAINTER_WARNINGS):
            _painter_warnings.append(message)
        if previous.get('handler') is not None:
            previous['handler'](mode, context, message)
        else:
            sys.stderr.write(message + '\n')

    previous['handler'] = QtCore.qInstallMessageHandler(handle)
    yield
    QtCore.qInstallMessageHandler(previous['handler'])


@pytest.fixture(autouse=True)
def _no_painter_warnings(_qt_message_log):
    """Fail a test that left a painter active or shared a paint device"""
    start = len(_painter_warnings)
    yield
    warnings = _painter_warnings[start:]
    if warnings:
        pytest.fail(f'Qt painter warnings: {warnings}', pytrace=False)
