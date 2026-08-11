from unittest.mock import patch

from cola import core
from cola import icons


def test_from_filename_unicode():
    filename = chr(0x400) + '.py'
    expect = 'file-code.svg'
    actual = icons.basename_from_filename(filename)
    assert expect == actual

    actual = icons.basename_from_filename(core.encode(filename))
    assert expect == actual


def test_basename_from_filename_is_memoized():
    """Repeated lookups of the same filename guess the mimetype only once."""
    filename = 'memoize_probe_unique_name.py'
    icons.basename_from_filename.cache.pop((filename,), None)

    with patch('cola.icons.core.guess_mimetype', return_value='text/x-python') as guess:
        first = icons.basename_from_filename(filename)
        second = icons.basename_from_filename(filename)

    assert first == second == 'file-code.svg'
    assert guess.call_count == 1
