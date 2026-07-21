"""GitHub commit signature verification

This reports the same "Verified" state that the GitHub web interface shows.
GitHub validates signatures against the keys its users have registered, whereas
git validates against your local keyring, so the two can legitimately disagree.
Both verdicts are surfaced rather than one overriding the other.

Everything here fails soft. Network errors, rate limits and unexpected payloads
result in no verification data rather than an error, because this is
supplementary information layered on top of git's own verification.
"""

from __future__ import annotations
import json
import os
import re
import urllib.error
import urllib.request

from . import core
from . import gitcmds
from . import utils
from .i18n import N_
from .interaction import Interaction
from .models import prefs

TIMEOUT = 5.0
"""Seconds to wait for a response from the GitHub API

The application waits for its background tasks to finish before exiting, and a
socket read cannot be interrupted, so this doubles as the longest a request in
flight can hold up quitting. Verification is supplementary information, so a
short ceiling is preferable to a patient one.
"""

GRAPHQL_BATCH_SIZE = 100
"""Number of commits queried per authenticated GraphQL request"""

REST_BATCH_SIZE = 20
"""Number of commits queried per unauthenticated pass

Unauthenticated clients get 60 REST requests per hour and each commit costs one
request, so we deliberately fetch very few without a token.
"""

_OID_REGEX = re.compile(r'^[0-9a-f]{7,64}$')
_REPO_REGEX = re.compile(r'^[A-Za-z0-9._-]+$')


class GitHubError(Exception):
    """A GitHub API request failed

    ``fatal`` indicates that retrying is pointless for the rest of the session,
    e.g. a bad token or an exhausted rate limit.
    """

    def __init__(self, message: str, fatal: bool = False, status: int = 0) -> None:
        super().__init__(message)
        self.fatal = fatal
        self.status = status
        """The HTTP status code, or zero when the request never completed"""


def is_oid(value: str) -> bool:
    """Return True when the value is a plausible object ID

    Values are interpolated into GraphQL queries so anything else is rejected.

    >>> is_oid('bdb0f6788fa5e3cacc4315e9ff318a27b2676ff4')
    True

    >>> is_oid('main')
    False

    """
    return bool(value) and _OID_REGEX.match(value) is not None


def api_url(host: str) -> str:
    """Return the REST API base URL for a GitHub host

    >>> api_url('github.com')
    'https://api.github.com'

    >>> api_url('github.example.org')
    'https://github.example.org/api/v3'

    """
    if host == 'github.com':
        return 'https://api.github.com'
    return f'https://{host}/api/v3'


def graphql_url(host: str) -> str:
    """Return the GraphQL endpoint for a GitHub host

    >>> graphql_url('github.com')
    'https://api.github.com/graphql'

    >>> graphql_url('github.example.org')
    'https://github.example.org/api/graphql'

    """
    if host == 'github.com':
        return 'https://api.github.com/graphql'
    return f'https://{host}/api/graphql'


def repository(context) -> tuple[str, str] | None:
    """Return the (owner, repository) for this repo's GitHub remote

    "origin" is preferred when it points at GitHub, otherwise the first
    matching remote is used. Returns None when no remote is hosted on GitHub.
    """
    host = prefs.github_host(context)
    remotes = context.model.remotes
    # Sort "origin" first while leaving the remaining order untouched.
    ordered = sorted(remotes, key=lambda remote: remote != 'origin')
    for remote in ordered:
        url = gitcmds.remote_url(context, remote)
        if not url or utils.get_hostname_from_url(url) != host:
            continue
        path = utils.get_path_from_url(url)
        if not path or path.count('/') != 1:
            continue
        owner, name = path.split('/')
        if _REPO_REGEX.match(owner) and _REPO_REGEX.match(name):
            return (owner, name)
    return None


_TOKEN_CACHE: dict[str, str | None] = {}


def reset_cache() -> None:
    """Forget the tokens resolved so far

    Resolving a token can be expensive -- a "gh" subprocess, or a password
    manager that prompts -- so the result is cached for the session. Call this
    to pick up a login that happened after git-cola started.
    """
    _TOKEN_CACHE.clear()


def token(context) -> str | None:
    """Return a GitHub API token, or None when no token is available

    The result is cached per host for the session; see reset_cache().

    Sources are consulted in order and the first token found wins:

    1. The ``GITHUB_TOKEN`` and ``GH_TOKEN`` environment variables.
    2. ``cola.githubauthcommand``, when one is configured.
    3. The "gh" command-line tool, unless ``cola.githubusegh`` is disabled.

    Tokens are deliberately never read from or written to the git config so
    that one cannot end up committed inside a repository.
    """
    host = prefs.github_host(context)
    try:
        return _TOKEN_CACHE[host]
    except KeyError:
        pass
    value = _resolve_token(context)
    _TOKEN_CACHE[host] = value
    return value


def _resolve_token(context) -> str | None:
    """Consult every token source in priority order"""
    for name in ('GITHUB_TOKEN', 'GH_TOKEN'):
        value = core.getenv(name)
        if value and value.strip():
            return value.strip()

    auth_token = _token_from_auth_command(context)
    if auth_token:
        return auth_token

    if not prefs.github_use_gh(context):
        return None
    return _token_from_gh_config(context) or _token_from_gh_command(context)


def _first_line(value: str) -> str:
    """Return the first non-empty line, stripped of quotes and whitespace

    A token never spans lines, so anything a command prints after the first
    line is chatter rather than part of the credential.

    >>> _first_line('\\ngho_token\\nSaved to keyring\\n')
    'gho_token'

    >>> _first_line('  ')
    ''

    """
    for line in value.splitlines():
        line = line.strip().strip('"\'')
        if line:
            return line
    return ''


def _shell_argv(command: str) -> list[str]:
    """Return the argument list that runs ``command`` through a shell

    >>> _shell_argv('gh auth token')[-1]
    'gh auth token'

    """
    if utils.is_win32():
        return ['cmd', '/c', command]
    return ['/bin/sh', '-c', command]


def _token_from_auth_command(context) -> str | None:
    """Run the configured auth command and read a token from its output

    The command runs through a shell so that quoting and pipes behave the way
    they do in a terminal, e.g. "op read 'op://Private/GitHub/token'".
    The hostname is exported as COLA_CREDENTIAL_HOST.
    """
    command = prefs.github_auth_command(context)
    if not command:
        return None
    host = prefs.github_host(context)
    status, out, err = core.run_command(
        _shell_argv(command),
        add_env={
            'COLA_CREDENTIAL_HOST': host,
            'COLA_CREDENTIAL_PROVIDER': 'github',
        },
    )
    if status != 0:
        # Fall through to the remaining sources rather than giving up, but say
        # so, because a silently broken auth command is miserable to diagnose.
        Interaction.log(
            N_('The GitHub auth command failed: %s') % command
            + '\n'
            + (err.strip() or N_('exit status %s') % status)
        )
        return None
    return _first_line(out) or None


def _gh_hosts_path() -> str:
    """Return the path to the "gh" CLI's hosts.yml"""
    config_dir = core.getenv('GH_CONFIG_DIR')
    if config_dir:
        return os.path.join(config_dir, 'hosts.yml')
    xdg_config_home = core.getenv('XDG_CONFIG_HOME') or os.path.join(
        core.expanduser('~'), '.config'
    )
    return os.path.join(xdg_config_home, 'gh', 'hosts.yml')


def _token_from_gh_command(context) -> str | None:
    """Ask the "gh" CLI for its token

    Recent versions of "gh" keep the token in the system keyring rather than in
    hosts.yml, and this is the only way to get at it.
    """
    host = prefs.github_host(context)
    status, out, _ = core.run_command(['gh', 'auth', 'token', '--hostname', host])
    if status == 0:
        return _first_line(out) or None
    return None


def _token_from_gh_config(context) -> str | None:
    """Read the oauth token that the "gh" CLI stores for this host

    hosts.yml is simple enough that a full YAML parser is not worth a new
    dependency: top-level keys are hostnames and the token is the first
    "oauth_token" entry nested underneath the host we care about.
    """
    path = _gh_hosts_path()
    if not core.exists(path):
        return None
    try:
        content = core.read(path)
    except OSError:
        return None

    host = prefs.github_host(context)
    in_host = False
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[:1].isspace():
            in_host = line.split(':', 1)[0].strip().strip('"\'') == host
            continue
        if not in_host:
            continue
        key, _, value = line.strip().partition(':')
        if key == 'oauth_token' and value.strip():
            return value.strip().strip('"\'')
    return None


def _request(url: str, auth_token: str | None, data: dict | None = None) -> dict:
    """Perform a GitHub API request and return the decoded JSON response"""
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'git-cola',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    body = None
    if auth_token:
        headers['Authorization'] = 'Bearer ' + auth_token
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = response.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        # 401 is a bad token, 403 and 429 are rate limits. Retrying any of them
        # during this session will only produce the same answer.
        fatal = exc.code in (401, 403, 429)
        raise GitHubError(
            f'{url}: HTTP {exc.code}', fatal=fatal, status=exc.code
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GitHubError(f'{url}: {exc}') from exc

    try:
        return json.loads(payload)
    except ValueError as exc:
        raise GitHubError(f'{url}: invalid JSON response') from exc


def verify_commits(
    context, owner: str, name: str, oids: list[str], should_stop=None
) -> dict[str, dict]:
    """Return {oid: {'verified': bool, 'reason': str}} for the requested commits

    Commits that GitHub does not know about are simply absent from the result.
    Raises GitHubError when the request fails.

    ``should_stop`` is an optional callable polled between requests so that a
    closing window does not have to wait for the whole batch.
    """
    oids = [oid for oid in oids if is_oid(oid)]
    if not oids or (should_stop is not None and should_stop()):
        return {}
    host = prefs.github_host(context)
    auth_token = token(context)
    if auth_token:
        return _verify_graphql(host, owner, name, oids[:GRAPHQL_BATCH_SIZE], auth_token)
    return _verify_rest(host, owner, name, oids[:REST_BATCH_SIZE], should_stop)


def _verify_graphql(
    host: str, owner: str, name: str, oids: list[str], auth_token: str
) -> dict[str, dict]:
    """Verify a batch of commits using a single aliased GraphQL query"""
    selections = ' '.join(
        f'c{idx}: object(oid: "{oid}") '
        '{ ... on Commit { signature { isValid state } } }'
        for idx, oid in enumerate(oids)
    )
    query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {selections} }} }}'
    payload = _request(graphql_url(host), auth_token, data={'query': query})

    errors = payload.get('errors')
    if errors and not payload.get('data'):
        message = errors[0].get('message', 'GraphQL query failed')
        raise GitHubError(message)

    repo = (payload.get('data') or {}).get('repository') or {}
    results = {}
    for idx, oid in enumerate(oids):
        commit = repo.get(f'c{idx}')
        if not commit:
            continue
        signature = commit.get('signature')
        if not signature:
            # GitHub knows the commit but it carries no signature at all.
            results[oid] = {'verified': False, 'reason': 'unsigned'}
            continue
        results[oid] = {
            'verified': bool(signature.get('isValid')),
            'reason': (signature.get('state') or '').lower(),
        }
    return results


def _verify_rest(
    host: str, owner: str, name: str, oids: list[str], should_stop=None
) -> dict[str, dict]:
    """Verify commits one request at a time, for use without a token"""
    base_url = api_url(host)
    results = {}
    for oid in oids:
        if should_stop is not None and should_stop():
            break
        try:
            payload = _request(f'{base_url}/repos/{owner}/{name}/commits/{oid}', None)
        except GitHubError as error:
            # A commit that has not been pushed yet is not an error worth
            # abandoning the whole batch over; GitHub reports it as 404 or 422.
            if error.status in (404, 422):
                continue
            raise
        verification = (payload.get('commit') or {}).get('verification') or {}
        if not verification:
            continue
        results[oid] = {
            'verified': bool(verification.get('verified')),
            'reason': verification.get('reason') or '',
        }
    return results
