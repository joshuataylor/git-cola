"""On-disk cache for Gravatar avatars

Avatars are cached under the XDG cache directory so they survive restarts
instead of being re-fetched from the network on every launch.

Both outcomes are recorded. A hit stores the image bytes; a miss stores an
empty marker file. Persisting misses matters as much as persisting hits: most
author emails have no Gravatar, and without a marker every one of them would
re-hit the network on each launch.

Entries carry no metadata -- the file's modification time is the timestamp and
its extension is the outcome, which keeps the format trivial and self-healing.
Cache failures are never fatal: any I/O error degrades to a cache miss and the
avatar is fetched as usual.
"""

from __future__ import annotations
import os
import time
from typing import Any

from . import core
from . import resources

# Avatars are re-fetched after this long so profile picture changes show up.
AVATAR_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
# Misses are re-checked less eagerly than hits; an email that has no Gravatar
# today usually still has none tomorrow.
MISS_MAX_AGE_SECONDS = 24 * 60 * 60

AVATAR_SUFFIX = '.png'
MISS_SUFFIX = '.miss'

# load() outcomes.
AVATAR = 'avatar'
MISS = 'miss'


def cache_directory() -> str:
    """Return the directory holding cached avatars"""
    return resources.cache_home('avatars')


def entry_path(email_hash: str, imgsize: int, suffix: str) -> str:
    """Return the cache path for an email hash at a specific icon size

    The size is part of the name because avatars are requested and stored at
    the size the label displays; a cached 16px icon must not satisfy a 64px
    lookup.
    """
    return os.path.join(cache_directory(), f'{email_hash}-{imgsize}{suffix}')


def _age_in_seconds(path: str) -> float | None:
    """Return how long ago `path` was written, or None when it is unreadable"""
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def load(email_hash: str, imgsize: int) -> tuple[str, bytes | None] | None:
    """Return the cached outcome for an email, or None when not cached

    Returns ``(AVATAR, data)`` for a cached image, ``(MISS, None)`` for an
    email known to have no avatar, and ``None`` when the cache cannot answer
    and the network should be consulted. Expired entries are removed so the
    cache does not grow without bound as authors come and go.
    """
    avatar_path = entry_path(email_hash, imgsize, AVATAR_SUFFIX)
    age = _age_in_seconds(avatar_path)
    if age is not None:
        if age < AVATAR_MAX_AGE_SECONDS:
            try:
                with core.xopen(avatar_path, 'rb') as handle:
                    data = handle.read()
            except OSError:
                return None
            # A truncated or empty file is treated as absent rather than
            # cached as a broken image.
            return (AVATAR, data) if data else None
        remove(avatar_path)

    miss_path = entry_path(email_hash, imgsize, MISS_SUFFIX)
    age = _age_in_seconds(miss_path)
    if age is not None:
        if age < MISS_MAX_AGE_SECONDS:
            return (MISS, None)
        remove(miss_path)

    return None


def store_avatar(email_hash: str, imgsize: int, data: Any) -> bool:
    """Cache the image bytes for an email; returns True when written"""
    if not data:
        return False
    return _write(entry_path(email_hash, imgsize, AVATAR_SUFFIX), bytes(data))


def store_miss(email_hash: str, imgsize: int) -> bool:
    """Record that an email has no avatar; returns True when written"""
    return _write(entry_path(email_hash, imgsize, MISS_SUFFIX), b'')


def _write(path: str, data: bytes) -> bool:
    """Write `data` to `path` atomically, ignoring cache failures

    The write goes to a temporary file in the same directory and is renamed
    into place, so a crash or a second git-cola writing the same entry cannot
    leave a half-written avatar behind.
    """
    if not _ensure_directory():
        return False
    # The pid keeps concurrent git-cola processes off each other's temp files.
    temp_path = f'{path}.{os.getpid()}.tmp'
    try:
        with core.xopen(temp_path, 'wb') as handle:
            handle.write(data)
        os.replace(core.mkpath(temp_path), core.mkpath(path))
    except OSError:
        remove(temp_path)
        return False
    return True


def _ensure_directory() -> bool:
    """Create the cache directory; returns False when it cannot be used"""
    try:
        os.makedirs(core.mkpath(cache_directory()), exist_ok=True)
    except OSError:
        return False
    return True


def remove(path: str) -> None:
    """Delete a cache entry, ignoring files that are already gone"""
    try:
        os.remove(core.mkpath(path))
    except OSError:
        # Nothing to do: the entry is gone, or the cache is unwritable and the
        # avatar will simply be fetched again.
        pass


def clear() -> int:
    """Delete every cached entry and return how many were removed"""
    directory = cache_directory()
    try:
        names = os.listdir(core.mkpath(directory))
    except OSError:
        return 0
    count = 0
    for name in names:
        # core.mkpath() encodes the path on Unix, so listdir() hands back bytes.
        filename = core.decode(name)
        if not filename.endswith((AVATAR_SUFFIX, MISS_SUFFIX)):
            continue
        remove(os.path.join(directory, filename))
        count += 1
    return count
