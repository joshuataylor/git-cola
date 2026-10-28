"""Resolve macOS menu key equivalents that AppKit would match to the wrong item

Qt's Cocoa plugin writes Cmd+Shift+<letter> menu shortcuts as a lower-case key
equivalent plus the Shift modifier (qcocoamenuitem.mm). When Qt's menu
delegate declines a key (the focus widget did not accept ShortcutOverride),
AppKit scans the menu bar itself and also matches the *unshifted* Cmd+<letter>
against those items, taking whichever comes first in menu-bar order. Cmd+M
ran "Merge..." (Actions menu) instead of "Minimize Window" (View menu), and
Cmd+E ran "Recently Modified Files..." (File) instead of "Launch Editor"
(Edit).

A local NSEvent monitor sees each key-down before the menu bar does. Only
when a Shift twin precedes the exact item does it take over: it repeats Qt's
ShortcutOverride check, then performs the exact item, or hands the key to the
window when there is no exact item. Every other key is left to AppKit.
"""
from __future__ import annotations

from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets
from qtpy.QtCore import Qt

try:
    import AppKit
except ImportError:
    AppKit = None

# NSEventModifierFlags, spelled out so the resolver can be tested without
# PyObjC.
SHIFT = 1 << 17
CONTROL = 1 << 18
OPTION = 1 << 19
COMMAND = 1 << 20
MODIFIERS = SHIFT | CONTROL | OPTION | COMMAND

PERFORM = 'perform'
FORWARD = 'forward'

# Keep the monitor alive for the life of the process.
_monitor = None


def resolve(items, key, modifiers):
    """Decide whether AppKit would pick the wrong item for an unshifted key

    items: (key_equivalent, modifier_mask, enabled, item) tuples in
    menu-bar order. key: the lower-case character typed. modifiers: the
    event's NSEventModifierFlags.

    Returns None to leave the key to AppKit, (PERFORM, item) to run the exact
    item, or (FORWARD, None) to deliver the key to the window instead.
    """
    modifiers &= MODIFIERS
    if not modifiers & COMMAND or modifiers & SHIFT:
        return None
    first = None
    exact = None
    for key_equivalent, mask, enabled, item in items:
        if not enabled or key_equivalent != key:
            continue
        mask &= MODIFIERS
        if mask == modifiers:
            if exact is None:
                exact = item
            if first is None:
                first = (False, item)
        elif mask == modifiers | SHIFT and first is None:
            first = (True, item)
    if first is None or not first[0]:
        return None
    if exact is not None:
        return (PERFORM, exact)
    return (FORWARD, None)


def install():
    """Install the key-equivalent monitor on macOS"""
    global _monitor
    if AppKit is None or _monitor is not None:
        return
    if QtGui.QGuiApplication.platformName() != 'cocoa':
        return
    _monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
        AppKit.NSEventMaskKeyDown, _handle_key_down
    )


def _menu_items(menu):
    """Yield (key_equivalent, mask, enabled, item) in menu-bar order"""
    for item in menu.itemArray():
        if item.isSeparatorItem() or item.isHidden():
            continue
        submenu = item.submenu()
        if submenu is not None:
            yield from _menu_items(submenu)
            continue
        yield (
            str(item.keyEquivalent()),
            int(item.keyEquivalentModifierMask()),
            bool(item.isEnabled()),
            item,
        )


def _focus_wants_key(key, modifiers):
    """Repeat Qt's check: does the focus widget claim this key itself?"""
    focus = QtWidgets.QApplication.focusObject()
    if focus is None or len(key) != 1 or not key.isprintable():
        return False
    qt_modifiers = Qt.ControlModifier
    if modifiers & OPTION:
        qt_modifiers |= Qt.AltModifier
    if modifiers & CONTROL:
        qt_modifiers |= Qt.MetaModifier
    event = QtGui.QKeyEvent(
        QtCore.QEvent.ShortcutOverride, ord(key.upper()), qt_modifiers
    )
    event.ignore()
    QtWidgets.QApplication.sendEvent(focus, event)
    return event.isAccepted()


def _handle_key_down(event):
    """NSEvent local monitor: return the event to continue, None to consume it"""
    try:
        modifiers = int(event.modifierFlags())
        if not modifiers & COMMAND or modifiers & SHIFT:
            return event
        characters = event.charactersIgnoringModifiers()
        if not characters or len(characters) != 1:
            return event
        key = str(characters).lower()
        menu = AppKit.NSApp.mainMenu()
        if menu is None:
            return event
        decision = resolve(_menu_items(menu), key, modifiers)
        if decision is None:
            return event
        action, item = decision
        window = AppKit.NSApp.keyWindow()
        if action == PERFORM and not _focus_wants_key(key, modifiers):
            parent = item.menu()
            parent.performActionForItemAtIndex_(parent.indexOfItem_(item))
            return None
        if window is None:
            return event
        window.sendEvent_(event)
        return None
    except Exception:  # never break keyboard input
        return event
