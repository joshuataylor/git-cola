import os

import pytest

from cola import avatarcache
from cola import resources


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Point the avatar cache at a temporary directory"""
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    return tmp_path / 'git-cola' / 'avatars'


EMAIL_HASH = 'a' * 64
IMGSIZE = 48


def test_xdg_cache_home_prefers_the_environment(monkeypatch):
    """$XDG_CACHE_HOME wins on every platform when it is set"""
    monkeypatch.setenv('XDG_CACHE_HOME', '/cache')
    assert resources.xdg_cache_home() == '/cache'
    assert resources.cache_home() == os.path.join('/cache', 'git-cola')


def test_xdg_cache_home_falls_back_per_platform(monkeypatch):
    """Without $XDG_CACHE_HOME, macOS uses Library/Caches and others ~/.cache"""
    monkeypatch.delenv('XDG_CACHE_HOME', raising=False)
    monkeypatch.setattr('cola.core.expanduser', lambda _path: '/home/user')

    monkeypatch.setattr('cola.utils.is_darwin', lambda: True)
    assert resources.xdg_cache_home() == os.path.join('/home/user', 'Library', 'Caches')

    monkeypatch.setattr('cola.utils.is_darwin', lambda: False)
    assert resources.xdg_cache_home() == os.path.join('/home/user', '.cache')


def test_avatar_round_trip(cache_dir):
    """Stored image bytes are returned verbatim"""
    assert avatarcache.load(EMAIL_HASH, IMGSIZE) is None

    assert avatarcache.store_avatar(EMAIL_HASH, IMGSIZE, b'\x89PNG-bytes')
    assert avatarcache.load(EMAIL_HASH, IMGSIZE) == (
        avatarcache.AVATAR,
        b'\x89PNG-bytes',
    )
    assert cache_dir.is_dir()


def test_miss_round_trip(cache_dir):
    """A recorded miss is reported without any image data"""
    assert avatarcache.store_miss(EMAIL_HASH, IMGSIZE)
    assert avatarcache.load(EMAIL_HASH, IMGSIZE) == (avatarcache.MISS, None)


def test_entries_are_size_specific(cache_dir):
    """An avatar cached at one size does not satisfy another size"""
    avatarcache.store_avatar(EMAIL_HASH, 16, b'small')
    assert avatarcache.load(EMAIL_HASH, 16) == (avatarcache.AVATAR, b'small')
    assert avatarcache.load(EMAIL_HASH, 64) is None


def _age(path, seconds):
    """Backdate a cache entry by the given number of seconds"""
    stamp = os.path.getmtime(path) - seconds
    os.utime(path, (stamp, stamp))


def test_expired_avatar_is_discarded(cache_dir):
    """A stale avatar is removed so profile picture changes are picked up"""
    avatarcache.store_avatar(EMAIL_HASH, IMGSIZE, b'old-avatar')
    path = avatarcache.entry_path(EMAIL_HASH, IMGSIZE, avatarcache.AVATAR_SUFFIX)
    _age(path, avatarcache.AVATAR_MAX_AGE_SECONDS + 60)

    assert avatarcache.load(EMAIL_HASH, IMGSIZE) is None
    assert not os.path.exists(path)


def test_expired_miss_is_discarded(cache_dir):
    """A stale miss is removed so a newly created avatar is picked up"""
    avatarcache.store_miss(EMAIL_HASH, IMGSIZE)
    path = avatarcache.entry_path(EMAIL_HASH, IMGSIZE, avatarcache.MISS_SUFFIX)
    _age(path, avatarcache.MISS_MAX_AGE_SECONDS + 60)

    assert avatarcache.load(EMAIL_HASH, IMGSIZE) is None
    assert not os.path.exists(path)


def test_fresh_miss_survives_the_avatar_window(cache_dir):
    """Misses use their own, shorter window rather than the avatar window"""
    avatarcache.store_miss(EMAIL_HASH, IMGSIZE)
    path = avatarcache.entry_path(EMAIL_HASH, IMGSIZE, avatarcache.MISS_SUFFIX)
    _age(path, avatarcache.MISS_MAX_AGE_SECONDS - 60)

    assert avatarcache.load(EMAIL_HASH, IMGSIZE) == (avatarcache.MISS, None)


def test_empty_avatar_file_is_treated_as_absent(cache_dir):
    """A truncated entry is ignored rather than cached as a broken image"""
    avatarcache.store_avatar(EMAIL_HASH, IMGSIZE, b'data')
    path = avatarcache.entry_path(EMAIL_HASH, IMGSIZE, avatarcache.AVATAR_SUFFIX)
    with open(path, 'wb'):
        pass  # truncate

    assert avatarcache.load(EMAIL_HASH, IMGSIZE) is None


def test_empty_data_is_not_stored(cache_dir):
    """Storing empty bytes is refused instead of writing a useless entry"""
    assert not avatarcache.store_avatar(EMAIL_HASH, IMGSIZE, b'')
    assert avatarcache.load(EMAIL_HASH, IMGSIZE) is None


def test_no_temporary_files_are_left_behind(cache_dir):
    """The atomic write leaves only the final entry in place"""
    avatarcache.store_avatar(EMAIL_HASH, IMGSIZE, b'avatar')
    names = os.listdir(cache_dir)
    assert names == [f'{EMAIL_HASH}-{IMGSIZE}{avatarcache.AVATAR_SUFFIX}']


def test_clear_removes_every_entry(cache_dir):
    """clear() deletes cached avatars and misses alike"""
    avatarcache.store_avatar(EMAIL_HASH, IMGSIZE, b'avatar')
    avatarcache.store_miss('b' * 64, IMGSIZE)

    assert avatarcache.clear() == 2
    assert avatarcache.load(EMAIL_HASH, IMGSIZE) is None
    assert os.listdir(cache_dir) == []


def test_clear_without_a_cache_directory(cache_dir):
    """clear() is a no-op when nothing has been cached yet"""
    assert avatarcache.clear() == 0


def test_unwritable_cache_is_not_fatal(cache_dir, monkeypatch):
    """I/O failures degrade to a cache miss rather than raising"""

    def boom(*_args, **_kwargs):
        raise OSError('read-only file system')

    monkeypatch.setattr('cola.avatarcache.core.xopen', boom)

    assert not avatarcache.store_avatar(EMAIL_HASH, IMGSIZE, b'avatar')
    assert avatarcache.load(EMAIL_HASH, IMGSIZE) is None
