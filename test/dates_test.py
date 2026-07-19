"""Tests for the cola.dates commit date formatting helpers"""

import datetime

from cola import dates

NOW = datetime.datetime(2026, 7, 19, 12, 0, 0)


def timestamp_for(*args):
    """Return a Unix timestamp for the given datetime arguments"""
    return int(datetime.datetime(*args).timestamp())


def test_relative_date_just_now():
    assert dates.relative_date(datetime.datetime(2026, 7, 19, 11, 59, 30), now=NOW) == (
        'just now'
    )


def test_relative_date_minutes():
    when = datetime.datetime(2026, 7, 19, 11, 59, 0)
    assert dates.relative_date(when, now=NOW) == '1 minute ago'

    when = datetime.datetime(2026, 7, 19, 11, 50, 0)
    assert dates.relative_date(when, now=NOW) == '10 minutes ago'


def test_relative_date_today_and_yesterday():
    when = datetime.datetime(2026, 7, 19, 0, 45, 0)
    assert dates.relative_date(when, now=NOW).startswith('Today ')

    when = datetime.datetime(2026, 7, 18, 23, 12, 0)
    assert dates.relative_date(when, now=NOW).startswith('Yesterday ')


def test_relative_date_falls_through_when_old():
    """Dates older than yesterday have no relative label"""
    when = datetime.datetime(2026, 7, 17, 12, 0, 0)
    assert dates.relative_date(when, now=NOW) == ''


def test_relative_date_ignores_the_future():
    """Clock skew can produce commit dates in the future"""
    when = datetime.datetime(2026, 7, 20, 12, 0, 0)
    assert dates.relative_date(when, now=NOW) == ''


def test_format_timestamp_git_mode_passes_through():
    timestamp = timestamp_for(2026, 7, 14, 23, 12)
    actual = dates.format_timestamp(
        timestamp,
        dates.DateMode.GIT,
        '',
        False,
        git_date='Tue Jul 14 23:12:00 2026 +1000',
        now=NOW,
    )
    assert actual == 'Tue Jul 14 23:12:00 2026 +1000'


def test_format_timestamp_without_a_timestamp():
    """Commits with no timestamp keep whatever git produced"""
    actual = dates.format_timestamp(
        0, dates.DateMode.SYSTEM, '', True, git_date='unknown', now=NOW
    )
    assert actual == 'unknown'


def test_format_timestamp_custom_format():
    timestamp = timestamp_for(2026, 7, 14, 23, 12)
    actual = dates.format_timestamp(
        timestamp, dates.DateMode.CUSTOM, 'yyyy-MM-dd HH:mm', False, now=NOW
    )
    assert actual == '2026-07-14 23:12'


def test_format_timestamp_custom_format_falls_back_when_empty():
    """An empty custom format leaves git's date alone"""
    timestamp = timestamp_for(2026, 7, 14, 23, 12)
    actual = dates.format_timestamp(
        timestamp, dates.DateMode.CUSTOM, '', False, git_date='fallback', now=NOW
    )
    assert actual == 'fallback'


def test_format_timestamp_pretty_beats_the_mode():
    """Pretty labels take precedence over the configured format when recent"""
    timestamp = timestamp_for(2026, 7, 19, 11, 50)
    actual = dates.format_timestamp(
        timestamp, dates.DateMode.CUSTOM, 'yyyy-MM-dd HH:mm', True, now=NOW
    )
    assert actual == '10 minutes ago'


def test_format_timestamp_pretty_falls_through_when_old():
    timestamp = timestamp_for(2026, 7, 14, 23, 12)
    actual = dates.format_timestamp(
        timestamp, dates.DateMode.CUSTOM, 'yyyy-MM-dd HH:mm', True, now=NOW
    )
    assert actual == '2026-07-14 23:12'


def test_date_modes():
    assert dates.date_modes() == ['git', 'system', 'custom']
