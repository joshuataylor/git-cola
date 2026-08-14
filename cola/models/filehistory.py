"""Data layer for the per-file history view"""
from __future__ import annotations
import collections

from ..git import STDOUT
from . import prefs

# The history walk stops after this many commits.
HISTORY_COMMIT_LIMIT = 1024

FileHistoryEntry = collections.namedtuple(
    'FileHistoryEntry',
    ['oid', 'summary', 'author', 'email', 'authdate', 'status', 'path', 'old_path'],
)


def load_history(
    context, path: str, ref: str | None = None, limit: int = HISTORY_COMMIT_LIMIT
) -> list[FileHistoryEntry]:
    """Return the commits that touched a path, newest first

    "git log --follow" accepts exactly one pathspec and reports each commit's
    own post-image path, so an entry's path is always valid for "git show
    <oid> -- <path>", grab-file and blame at that commit even across renames.
    """
    out = context.git.log(
        ref or 'HEAD',
        f'-{limit}',
        '--',
        path,
        follow=True,
        z=True,
        name_status=True,
        no_color=True,
        # tformat terminates each entry, so the metadata token is always
        # NUL-separated from the name-status tokens ("format" would not be).
        pretty=r'tformat:%x00%H%x1f%s%x1f%an%x1f%ae%x1f%ad',
        date=prefs.logdate(context),
        _readonly=True,
    )[STDOUT]
    return parse_follow_log(out, path)


def parse_follow_log(out: str, path: str) -> list[FileHistoryEntry]:
    """Parse "git log --follow -z --name-status" output framed by %x00 markers

    Each record is "<oid>\\x1f<summary>\\x1f<author>\\x1f<email>\\x1f<date>"
    preceded by a NUL marker, then a "\\n"-prefixed status token followed by
    the entry's path -- two paths (old, new) for a rename or copy. Merge
    commits emit no status or path; they inherit the effective older path
    from the neighbouring newer entry.
    """
    entries = []
    tokens = out.split('\0')
    total = len(tokens)
    index = 0
    while index < total:
        token = tokens[index]
        if not token or '\x1f' not in token:
            index += 1
            continue
        oid, summary, author, email, authdate = token.split('\x1f', 4)
        status = ''
        entry_path = ''
        old_path = ''
        index += 1
        if (
            index < total
            and tokens[index].startswith('\n')
            and '\x1f' not in tokens[index]
        ):
            status = tokens[index][1:]
            index += 1
            if status[:1] in ('R', 'C') and index + 1 < total:
                old_path = tokens[index]
                entry_path = tokens[index + 1]
                index += 2
            elif index < total and '\x1f' not in tokens[index]:
                entry_path = tokens[index]
                index += 1
        entries.append(
            FileHistoryEntry(
                oid, summary, author, email, authdate, status, entry_path, old_path
            )
        )
    # Fill in pathless entries: at an older commit the file lives at the
    # newer entry's pre-image path (its old_path for a rename, else its
    # path); the newest entry falls back to the requested path.
    for index, entry in enumerate(entries):
        if entry.path:
            continue
        if index > 0:
            newer = entries[index - 1]
            inherited = newer.old_path or newer.path
        else:
            inherited = path
        entries[index] = entry._replace(path=inherited)
    return entries


def diff_pathspec(entry: FileHistoryEntry):
    """Return the pathspec for diffing an entry against its parent

    Renames and copies include both sides so that git pairs them and the
    diff renders as "rename from/to" instead of a bare file addition.
    """
    if entry.old_path:
        return (entry.path, entry.old_path)
    return entry.path
