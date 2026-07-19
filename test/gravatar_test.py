import os
import sys
from unittest.mock import MagicMock

import pytest

from cola import avatarcache
from cola import gravatar
from cola.compat import ustr
from cola.gravatar import Gravatar
from cola.gravatar import GravatarLabel
from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets


def test_url_for_email_():
    email = 'email@example.com'
    # Gravatar prefers the SHA256 digest of the trimmed, lower-cased email.
    expect = (
        'https://gravatar.com/avatar/'
        '2a539d6520266b56c3b0c525b9e6128858baeccb5ee9b694a2906e123c8d6dd3?s=64'
        + r'&d=https%3A%2F%2Fgit-cola.github.io%2Fimages%2Fgit-64x64.jpg'
    )
    actual = gravatar.Gravatar.url_for_email(email, 64)
    assert expect == actual
    assert isinstance(actual, ustr)


def test_url_for_email_normalizes_case_and_whitespace():
    """Trimming and lower-casing yield the same URL as the canonical form."""
    canonical = gravatar.Gravatar.url_for_email('email@example.com', 64)
    assert gravatar.Gravatar.url_for_email('  Email@Example.COM  ', 64) == canonical


@pytest.fixture(autouse=True)
def isolated_avatar_cache(tmp_path, monkeypatch):
    """Keep avatar cache writes out of the developer's real cache directory

    GravatarLabel persists avatars and misses to disk, so without this the
    suite would write into $XDG_CACHE_HOME and a cached miss from one test
    would stop a later test from issuing its network request.
    """
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))


@pytest.fixture(scope='module')
def qapp():
    """Provide a QApplication for widget tests."""
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication(
            sys.argv[:1] if sys.argv else ['git-cola-test']
        )
    yield instance


def _url_mock(url):
    mock = MagicMock()
    mock.toString.return_value = url
    return mock


class FakeReply:
    """Minimal stand-in for QNetworkReply used to drive network_finished.

    Qt follows redirects itself, so a reply exposes two URLs: request().url()
    is what we asked for and url() is the final hop. They differ only when
    Gravatar had no avatar and redirected to the "d=" default image. The
    redirect is never visible as a Location header -- the reply we are handed
    is the plain 200 from the end of the chain.
    """

    def __init__(self, url, *, error=0, final_url=None, data=b'avatar-bytes'):
        self._url = url
        self._final_url = final_url if final_url is not None else url
        self._error = error
        self._data = data
        self.deleted = False

    def url(self):
        return _url_mock(self._final_url)

    def request(self):
        mock = MagicMock()
        mock.url.return_value = _url_mock(self._url)
        return mock

    def error(self):
        return self._error

    def readAll(self):
        return self._data

    def deleteLater(self):
        self.deleted = True


def _make_label(enable_gravatar=True):
    context = MagicMock()
    context.cfg.get.return_value = enable_gravatar
    label = GravatarLabel(context)
    # Avoid real network traffic; capture requested URLs instead.
    label.network = MagicMock()
    return label


def _png_bytes(size=8):
    """Return real PNG bytes, which the on-disk cache round-trips and decodes"""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtGui.QColor('red'))
    byte_array = QtCore.QByteArray()
    buf = QtCore.QBuffer(byte_array)
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, 'PNG')
    buf.close()
    return bytes(byte_array)


def _real_avatar_reply(label, email):
    """A reply that returns an actual avatar (no redirect, so the URLs match)."""
    url = Gravatar.url_for_email(email, label.imgsize)
    return FakeReply(url, error=0, data=_png_bytes())


def _missing_avatar_reply(label, email):
    """A reply redirected to the default image (no avatar for this email).

    Gravatar redirects to the "d=" default, which lives on another host, so the
    final URL differs from the one that was requested.
    """
    url = Gravatar.url_for_email(email, label.imgsize)
    return FakeReply(
        url,
        error=0,
        final_url='https://i2.wp.com/git-cola.github.io/images/git-64x64.jpg',
    )


def _expire_miss(label, email):
    """Expire a miss in memory and on disk so the email is retried

    Both windows have to lapse: the in-memory dict is only a fast path, and the
    on-disk marker outlives it so misses survive a restart.
    """
    if email in label.failed:
        label.failed[email] -= label.RETRY_INTERVAL_SECONDS + 1
    path = avatarcache.entry_path(
        gravatar.sha256_hexdigest(email), label.imgsize, avatarcache.MISS_SUFFIX
    )
    if os.path.exists(path):
        stamp = os.path.getmtime(path) - avatarcache.MISS_MAX_AGE_SECONDS - 60
        os.utime(path, (stamp, stamp))


def test_successful_avatar_is_cached(qapp):
    """A fetched avatar is cached and reused without re-requesting."""
    label = _make_label()
    email = 'alice@example.com'

    label.set_email(email)
    assert label.network.get.call_count == 1  # initial request

    label.network_finished(_real_avatar_reply(label, email))
    assert email in label.pixmaps

    # Revisiting the same author hits the cache; no new request.
    label.set_email(email)
    assert label.network.get.call_count == 1


def test_missing_avatar_is_not_re_requested(qapp):
    """An email with no avatar is remembered and not requested again."""
    label = _make_label()
    email = 'noavatar@example.com'

    label.set_email(email)
    assert label.network.get.call_count == 1

    # Reply redirects to the default image -> recorded as a miss.
    label.network_finished(_missing_avatar_reply(label, email))
    assert email in label.failed
    assert email not in label.pixmaps

    # Revisiting must not fire another request within the retry window.
    label.set_email(email)
    assert label.network.get.call_count == 1


def test_missing_avatar_retried_after_window(qapp):
    """A failed lookup is retried once the retry window elapses."""
    label = _make_label()
    email = 'noavatar@example.com'

    label.set_email(email)
    label.network_finished(_missing_avatar_reply(label, email))
    assert label.network.get.call_count == 1

    # Age the failure beyond the retry window.
    _expire_miss(label, email)
    label.set_email(email)
    assert label.network.get.call_count == 2


def test_late_reply_for_previous_email_does_not_repaint(qapp):
    """A reply for an old author must not overwrite the current avatar."""
    label = _make_label()
    alice = 'alice@example.com'
    bob = 'bob@example.com'

    # Request alice, then immediately switch to bob before alice resolves.
    label.set_email(alice)
    label.set_email(bob)
    assert label.email == bob

    captured = []
    label.setPixmap = lambda pixmap: captured.append(pixmap)

    # Alice's (stale) reply arrives now.
    label.network_finished(_real_avatar_reply(label, alice))
    # Alice is still cached for later, but the visible label is not repainted.
    assert alice in label.pixmaps
    assert captured == []

    # Bob's reply arrives and does repaint, since bob is current.
    label.network_finished(_real_avatar_reply(label, bob))
    assert bob in label.pixmaps
    assert len(captured) == 1


def test_inflight_request_is_not_duplicated(qapp):
    """Revisiting an email whose request is still pending issues no duplicate."""
    label = _make_label()
    email = 'alice@example.com'

    label.set_email(email)
    assert label.network.get.call_count == 1

    # Switch away and back while the first request is still in flight.
    label.set_email('bob@example.com')
    label.set_email(email)
    # alice's request is still pending, so no second alice request is sent;
    # bob's is the only additional request.
    assert label.network.get.call_count == 2


def test_disabled_gravatar_uses_default_without_network(qapp):
    """With gravatar disabled, the default icon is cached and no request fires."""
    label = _make_label(enable_gravatar=False)
    email = 'alice@example.com'

    label.set_email(email)
    label.network.get.assert_not_called()
    assert email in label.pixmaps
    assert isinstance(label.pixmaps[email], QtGui.QPixmap)


def test_switching_to_uncached_email_shows_default_not_stale_avatar(qapp):
    """Switching authors shows the default while loading, never the old face.

    Regression: holding the previous author's avatar during the fetch made a
    commit whose author has no gravatar display the wrong person's picture.
    """
    label = _make_label()
    alice = 'alice@example.com'
    bob = 'bob@example.com'

    # Resolve alice so the label is showing alice's real avatar.
    label.set_email(alice)
    label.network_finished(_real_avatar_reply(label, alice))
    alice_pixmap = label.pixmaps[alice]

    painted = []
    label.setPixmap = lambda pixmap: painted.append(pixmap)

    # Switching to an uncached author must repaint the default immediately, so
    # alice's face is not shown for bob's commit while bob's avatar loads.
    label.set_email(bob)
    assert len(painted) == 1
    assert painted[0] is not alice_pixmap
    assert painted[0] is label.default_pixmap()
    assert label.network.get.call_count == 2  # bob is requested

    # bob turns out to have no avatar -> the default stays (no stale alice).
    label.network_finished(_missing_avatar_reply(label, bob))
    assert painted[-1] is label.default_pixmap()


def test_revisiting_cached_miss_shows_default_not_stale_avatar(qapp):
    """An author with a known-missing avatar shows the default, not the prior face."""
    label = _make_label()
    alice = 'alice@example.com'
    bob = 'bob@example.com'

    # alice has an avatar; bob is a known miss.
    label.set_email(alice)
    label.network_finished(_real_avatar_reply(label, alice))
    label.set_email(bob)
    label.network_finished(_missing_avatar_reply(label, bob))

    # Show alice again (cached avatar), then bob again (cached miss).
    label.set_email(alice)
    painted = []
    label.setPixmap = lambda pixmap: painted.append(pixmap)
    label.set_email(bob)
    # bob's miss is cached, so no new request and the default is shown.
    assert label.network.get.call_count == 2
    assert painted[-1] is label.default_pixmap()


def test_redirected_reply_is_attributed_to_its_email(qapp):
    """A redirected miss is matched to its email via the original request URL.

    Regression: network_finished() keyed off reply.url(), which after Qt
    follows the redirect is the default image on another host. It never matched
    the pending entry, so the email was left unidentified: the miss was not
    recorded and the stranded entry in self.requested made request() treat the
    email as permanently in flight, so it was never fetched again.
    """
    label = _make_label()
    email = 'noavatar@example.com'

    label.set_email(email)
    assert label.network.get.call_count == 1
    assert label.requested  # the request is pending

    label.network_finished(_missing_avatar_reply(label, email))

    # The reply was attributed despite the redirect: the miss is recorded and
    # nothing is left pending.
    assert email in label.failed
    assert email not in label.pixmaps
    assert not label.requested

    # Once the retry window lapses the email is requested again, which the
    # stranded entry used to prevent forever.
    _expire_miss(label, email)
    label.set_email(email)
    assert label.network.get.call_count == 2


def test_redirected_reply_does_not_cache_default_as_avatar(qapp):
    """The default image served on a miss is never cached as the real avatar.

    Regression: relocation was detected via the Location header, which is
    always empty because Qt has already followed the redirect. Every miss
    therefore looked like a success and cached the git-cola default image as
    that author's avatar, permanently.
    """
    label = _make_label()
    email = 'noavatar@example.com'

    label.set_email(email)
    label.network_finished(_missing_avatar_reply(label, email))

    assert email not in label.pixmaps
    assert email in label.failed


def test_avatar_is_served_from_disk_without_a_request(qapp):
    """A label in a later session reuses the cached avatar, hitting no network"""
    label = _make_label()
    email = 'alice@example.com'
    label.set_email(email)
    label.network_finished(_real_avatar_reply(label, email))

    # A fresh label stands in for a subsequent run of git-cola.
    fresh = _make_label()
    fresh.set_email(email)

    assert email in fresh.pixmaps
    fresh.network.get.assert_not_called()


def test_cached_miss_is_served_from_disk_without_a_request(qapp):
    """A miss recorded on disk suppresses the request in a later session"""
    label = _make_label()
    email = 'noavatar@example.com'
    label.set_email(email)
    label.network_finished(_missing_avatar_reply(label, email))

    fresh = _make_label()
    fresh.set_email(email)

    assert email in fresh.failed
    assert email not in fresh.pixmaps
    fresh.network.get.assert_not_called()


def test_network_error_is_not_cached_as_a_miss(qapp):
    """Being offline must not persist a miss and hide avatars in later sessions

    Only a redirect proves an email has no avatar. A failed request means the
    network was unavailable, so the email must be retried next time rather than
    showing the default icon for a day.
    """
    label = _make_label()
    email = 'alice@example.com'
    label.set_email(email)
    # error=1 is any non-zero QNetworkReply error, e.g. host unreachable.
    label.network_finished(
        FakeReply(Gravatar.url_for_email(email, label.imgsize), error=1)
    )
    assert email in label.failed

    fresh = _make_label()
    fresh.set_email(email)
    # Nothing was written to disk, so the fresh label goes to the network.
    assert fresh.network.get.call_count == 1


def test_undecodable_cache_entry_is_refetched(qapp):
    """A corrupt cache file is discarded and the avatar requested again"""
    label = _make_label()
    email = 'alice@example.com'
    email_hash = gravatar.sha256_hexdigest(email)
    # Bytes that are not a decodable image, as a truncated write would leave.
    avatarcache.store_avatar(email_hash, label.imgsize, b'not-an-image')

    label.set_email(email)

    assert email not in label.pixmaps
    assert label.network.get.call_count == 1
    assert not os.path.exists(
        avatarcache.entry_path(email_hash, label.imgsize, avatarcache.AVATAR_SUFFIX)
    )


def test_default_pixmap_decoded_once(qapp):
    """The fallback icon is decoded a single time and reused."""
    label = _make_label()
    first = label.default_pixmap()
    second = label.default_pixmap()
    assert first is second
