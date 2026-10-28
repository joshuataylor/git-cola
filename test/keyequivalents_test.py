"""Tests for cola.keyequivalents"""
from cola import keyequivalents as ke

CMD = ke.COMMAND
SHIFT = ke.SHIFT


def _items(*specs):
    """(key, mask, enabled, name) tuples in menu-bar order"""
    return [(key, mask, enabled, name) for key, mask, enabled, name in specs]


def test_shift_twin_before_the_exact_item_performs_the_exact_item():
    items = _items(
        ('m', CMD | SHIFT, True, 'Merge'),
        ('m', CMD, True, 'Minimize'),
    )
    assert ke.resolve(items, 'm', CMD) == (ke.PERFORM, 'Minimize')


def test_exact_item_first_is_left_to_appkit():
    items = _items(
        ('p', CMD, True, 'Push'),
        ('p', CMD | SHIFT, True, 'Pull'),
    )
    assert ke.resolve(items, 'p', CMD) is None


def test_shift_twin_without_an_exact_item_forwards_to_the_window():
    items = _items(('e', CMD | SHIFT, True, 'Recently Modified'))
    assert ke.resolve(items, 'e', CMD) == (ke.FORWARD, None)


def test_shifted_events_and_unrelated_keys_are_left_alone():
    items = _items(
        ('m', CMD | SHIFT, True, 'Merge'),
        ('m', CMD, True, 'Minimize'),
    )
    assert ke.resolve(items, 'm', CMD | SHIFT) is None
    assert ke.resolve(items, 'x', CMD) is None
    assert ke.resolve(items, 'm', ke.OPTION) is None


def test_disabled_items_are_skipped():
    items = _items(
        ('m', CMD | SHIFT, False, 'Merge'),
        ('m', CMD, True, 'Minimize'),
    )
    assert ke.resolve(items, 'm', CMD) is None
    items = _items(
        ('m', CMD | SHIFT, True, 'Merge'),
        ('m', CMD, False, 'Minimize'),
    )
    assert ke.resolve(items, 'm', CMD) == (ke.FORWARD, None)


def test_extra_modifiers_must_match():
    option = ke.OPTION
    items = _items(
        ('c', CMD | option | SHIFT, True, 'Other'),
        ('c', CMD | option, True, 'Copy Commit'),
    )
    assert ke.resolve(items, 'c', CMD | option) == (ke.PERFORM, 'Copy Commit')
    assert ke.resolve(items, 'c', CMD) is None


def test_device_dependent_bits_are_ignored():
    items = _items(
        ('m', CMD | SHIFT, True, 'Merge'),
        ('m', CMD, True, 'Minimize'),
    )
    left_command_key = 0x8
    assert ke.resolve(items, 'm', CMD | left_command_key) == (ke.PERFORM, 'Minimize')
