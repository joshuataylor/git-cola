"""Date formatting for commit dates displayed in the Git DAG

Git formats commit dates itself via "git log --date=<format>", which is what the
DAG shows by default. These helpers take the raw author timestamp instead so that
dates can be rendered using the system locale, a user-supplied format, or relative
labels such as "Today" and "10 minutes ago".

Timestamps are converted to naive local-time datetimes throughout so that "today"
means today in the viewer's timezone, which is what git's own --date=local does.
"""

from __future__ import annotations
import datetime

from qtpy import QtCore

from .i18n import N_

MINUTE = 60
HOUR = MINUTE * 60


class DateMode:
    """How commit dates are rendered in the Git DAG commit list"""

    GIT = 'git'
    """Use the string produced by "git log --date=<cola.logdate>" """

    SYSTEM = 'system'
    """Use the system locale's short date and time format"""

    CUSTOM = 'custom'
    """Use a user-supplied Qt date format string"""


def date_modes() -> list[str]:
    """Return valid values for git config cola.dagdatemode"""
    return [DateMode.GIT, DateMode.SYSTEM, DateMode.CUSTOM]


def format_timestamp(timestamp, mode, custom_format, pretty, git_date='', now=None):
    """Render a Unix timestamp for display in the commit list

    ``git_date`` is the pre-formatted string from "git log --date=" and is used
    as-is in ``DateMode.GIT``. A timestamp of zero means the date is unknown, in
    which case ``git_date`` is always returned unchanged.
    """
    if not timestamp:
        return git_date
    # The default configuration (git mode, no pretty labels) returns git's own
    # string untouched; this runs per commit at load, so skip the datetime.
    if not pretty and mode == DateMode.GIT:
        return git_date
    when = datetime.datetime.fromtimestamp(timestamp)
    if pretty:
        relative = relative_date(when, now=now)
        if relative:
            return relative
    if mode == DateMode.SYSTEM:
        return system_date(when)
    if mode == DateMode.CUSTOM and custom_format:
        return custom_date(when, custom_format)
    return git_date


def relative_date(when, now=None):
    """Return a "Today"-style label for recent dates, or an empty string

    An empty string means the date is old enough that the caller should fall back
    to its regular formatting.

    >>> import datetime
    >>> now = datetime.datetime(2026, 7, 19, 12, 0)
    >>> relative_date(datetime.datetime(2026, 7, 19, 11, 50), now=now)
    '10 minutes ago'
    >>> relative_date(datetime.datetime(2026, 7, 1, 9, 0), now=now)
    ''
    """
    if now is None:
        now = datetime.datetime.now()
    seconds = (now - when).total_seconds()
    # Dates in the future have no sensible relative label.
    if seconds < 0:
        return ''
    if seconds < MINUTE:
        return N_('just now')
    if seconds < HOUR:
        minutes = int(seconds // MINUTE)
        if minutes == 1:
            return N_('1 minute ago')
        return N_('%d minutes ago') % minutes
    days = (now.date() - when.date()).days
    if days == 0:
        return N_('Today %s') % short_time(when)
    if days == 1:
        return N_('Yesterday %s') % short_time(when)
    return ''


_locale = None


def _system_locale() -> QtCore.QLocale:
    """Return a cached system locale

    These formatters run once per commit in the DAG, and constructing a
    QLocale per call is measurable on large histories. Built lazily so that
    importing this module does not touch Qt.
    """
    global _locale
    if _locale is None:
        _locale = QtCore.QLocale()
    return _locale


def system_date(when) -> str:
    """Format a datetime using the system locale's short date and time format"""
    return _system_locale().toString(to_qdatetime(when), QtCore.QLocale.ShortFormat)


def short_time(when) -> str:
    """Format the time-of-day portion of a datetime using the system locale"""
    locale = _system_locale()
    time_format = locale.timeFormat(QtCore.QLocale.ShortFormat)
    return locale.toString(to_qdatetime(when).time(), time_format)


def custom_date(when, custom_format: str) -> str:
    """Format a datetime using a Qt date format string, e.g. "dd MMM yyyy hh:mm" """
    return to_qdatetime(when).toString(custom_format)


def to_qdatetime(when) -> QtCore.QDateTime:
    """Convert a datetime into a QDateTime"""
    return QtCore.QDateTime.fromSecsSinceEpoch(int(when.timestamp()))
