"""Tests the GitHub commit signature verification helpers"""
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from cola import github


@pytest.fixture(autouse=True)
def reset_token_cache():
    """Tokens are cached for the session, which must not leak between tests"""
    github.reset_cache()
    yield
    github.reset_cache()


def make_context(remotes, urls, host='github.com'):
    """Build a context whose config answers remote URL and host lookups"""
    config = {'cola.githubhost': host}
    config.update(urls)
    context = Mock()
    context.model.remotes = remotes
    context.cfg.get.side_effect = lambda key, default=None: config.get(key, default)
    return context


def test_is_oid():
    assert github.is_oid('bdb0f6788fa5e3cacc4315e9ff318a27b2676ff4')
    assert github.is_oid('a' * 64)
    assert not github.is_oid('main')
    assert not github.is_oid('')
    # Anything that could escape a GraphQL string literal is rejected.
    assert not github.is_oid('abc") { x } #')


def test_api_urls_for_github_enterprise():
    assert github.api_url('github.com') == 'https://api.github.com'
    assert github.api_url('git.example.org') == 'https://git.example.org/api/v3'
    assert github.graphql_url('github.com') == 'https://api.github.com/graphql'
    assert (
        github.graphql_url('git.example.org') == 'https://git.example.org/api/graphql'
    )


@pytest.mark.parametrize(
    'url',
    [
        'https://github.com/git-cola/git-cola.git',
        'git@github.com:git-cola/git-cola.git',
        'ssh://git@github.com/git-cola/git-cola',
        'https://github.com/git-cola/git-cola',
    ],
)
def test_repository_from_remote_url(url):
    """Every URL form used by GitHub resolves to the same owner and repository"""
    context = make_context(['origin'], {'remote.origin.url': url})
    assert github.repository(context) == ('git-cola', 'git-cola')


def test_repository_prefers_origin():
    """ "origin" wins when several remotes point at GitHub"""
    context = make_context(
        ['upstream', 'origin'],
        {
            'remote.origin.url': 'git@github.com:mine/project.git',
            'remote.upstream.url': 'git@github.com:theirs/project.git',
        },
    )
    assert github.repository(context) == ('mine', 'project')


def test_repository_falls_back_to_another_remote():
    """A GitHub remote is used even when "origin" points elsewhere"""
    context = make_context(
        ['origin', 'gh'],
        {
            'remote.origin.url': 'git@gitlab.com:mine/project.git',
            'remote.gh.url': 'git@github.com:theirs/project.git',
        },
    )
    assert github.repository(context) == ('theirs', 'project')


def test_repository_without_a_github_remote():
    context = make_context(
        ['origin'], {'remote.origin.url': 'git@gitlab.com:mine/project.git'}
    )
    assert github.repository(context) is None


def test_repository_honours_a_github_enterprise_host():
    context = make_context(
        ['origin'],
        {'remote.origin.url': 'git@git.example.org:team/project.git'},
        host='git.example.org',
    )
    assert github.repository(context) == ('team', 'project')


def test_verify_commits_ignores_invalid_oids():
    """Refs are never interpolated into a query"""
    context = make_context(['origin'], {})
    with patch('cola.github._request') as request:
        assert github.verify_commits(context, 'o', 'n', ['main', '']) == {}
    request.assert_not_called()


@patch('cola.github.token', return_value='s3cret')
@patch('cola.github._request')
def test_verify_commits_batches_with_graphql(request, _token):
    """A token batches the whole request into a single GraphQL query"""
    oids = ['a' * 40, 'b' * 40, 'c' * 40]
    request.return_value = {
        'data': {
            'repository': {
                'c0': {'signature': {'isValid': True, 'state': 'VALID'}},
                'c1': {'signature': {'isValid': False, 'state': 'UNKNOWN_KEY'}},
                'c2': None,
            }
        }
    }
    context = make_context(['origin'], {})

    results = github.verify_commits(context, 'git-cola', 'git-cola', oids)

    assert request.call_count == 1
    assert results[oids[0]] == {'verified': True, 'reason': 'valid'}
    assert results[oids[1]] == {'verified': False, 'reason': 'unknown_key'}
    # A commit GitHub does not know about is absent rather than unverified.
    assert oids[2] not in results


@patch('cola.github.token', return_value='s3cret')
@patch('cola.github._request')
def test_verify_commits_reports_unsigned_commits(request, _token):
    """A commit GitHub knows about but that carries no signature is unverified"""
    oid = 'a' * 40
    request.return_value = {'data': {'repository': {'c0': {'signature': None}}}}
    context = make_context(['origin'], {})

    results = github.verify_commits(context, 'git-cola', 'git-cola', [oid])

    assert results[oid] == {'verified': False, 'reason': 'unsigned'}


@patch('cola.github.token', return_value=None)
@patch('cola.github._request')
def test_verify_commits_uses_rest_without_a_token(request, _token):
    """Without a token each commit costs one REST request"""
    oids = ['a' * 40, 'b' * 40]
    request.side_effect = [
        {'commit': {'verification': {'verified': True, 'reason': 'valid'}}},
        {'commit': {'verification': {'verified': False, 'reason': 'unsigned'}}},
    ]
    context = make_context(['origin'], {})

    results = github.verify_commits(context, 'git-cola', 'git-cola', oids)

    assert request.call_count == 2
    assert results[oids[0]] == {'verified': True, 'reason': 'valid'}
    assert results[oids[1]] == {'verified': False, 'reason': 'unsigned'}


@patch('cola.github.token', return_value=None)
@patch('cola.github._request')
def test_verify_commits_skips_unpushed_commits(request, _token):
    """A commit GitHub has never seen does not abandon the rest of the batch"""
    oids = ['a' * 40, 'b' * 40]
    request.side_effect = [
        github.GitHubError('not found', status=422),
        {'commit': {'verification': {'verified': True, 'reason': 'valid'}}},
    ]
    context = make_context(['origin'], {})

    results = github.verify_commits(context, 'git-cola', 'git-cola', oids)

    assert oids[0] not in results
    assert results[oids[1]] == {'verified': True, 'reason': 'valid'}


@patch('cola.github.token', return_value=None)
@patch('cola.github._request')
def test_verify_commits_stops_early_when_asked(request, _token):
    """A closing window must not wait for the whole batch of REST requests"""
    oids = ['a' * 40, 'b' * 40, 'c' * 40]
    issued = []

    def record(url, auth_token, data=None):
        issued.append(url)
        return {'commit': {'verification': {'verified': True, 'reason': 'valid'}}}

    request.side_effect = record
    # Stop once the first request has been made.
    results = github.verify_commits(
        context=make_context(['origin'], {}),
        owner='git-cola',
        name='git-cola',
        oids=oids,
        should_stop=lambda: len(issued) >= 1,
    )

    assert len(issued) == 1
    assert len(results) == 1


def test_verify_commits_does_nothing_when_already_stopping():
    """No request is made at all when shutdown began before the task ran"""
    context = make_context(['origin'], {})
    with patch('cola.github._request') as request:
        results = github.verify_commits(
            context, 'git-cola', 'git-cola', ['a' * 40], should_stop=lambda: True
        )
    assert results == {}
    request.assert_not_called()


@patch('cola.github.token', return_value=None)
@patch('cola.github._request')
def test_verify_commits_propagates_real_errors(request, _token):
    """A server error is not silently swallowed the way a missing commit is"""
    request.side_effect = github.GitHubError('boom', fatal=True, status=403)
    context = make_context(['origin'], {})

    with pytest.raises(github.GitHubError):
        github.verify_commits(context, 'git-cola', 'git-cola', ['a' * 40])


@patch('cola.github.token', return_value='s3cret')
@patch('cola.github._request')
def test_verify_commits_raises_on_graphql_errors(request, _token):
    request.return_value = {
        'errors': [{'message': 'Could not resolve to a Repository'}]
    }
    context = make_context(['origin'], {})

    with pytest.raises(github.GitHubError):
        github.verify_commits(context, 'git-cola', 'nope', ['a' * 40])


def test_token_is_cached_for_the_session():
    """Resolving a token can prompt, so it must not run on every request"""
    context = make_context(['origin'], {'cola.githubauthcommand': 'slow-command'})
    with patch('cola.github.core.getenv', return_value=None):
        with patch(
            'cola.github.core.run_command', return_value=(0, 'tok\n', '')
        ) as run_command:
            assert github.token(context) == 'tok'
            assert github.token(context) == 'tok'
            assert run_command.call_count == 1

            github.reset_cache()
            assert github.token(context) == 'tok'
            assert run_command.call_count == 2


def test_missing_token_is_cached_too():
    """A negative result is cached so "gh" is not re-run on every request"""
    context = make_context(['origin'], {})
    with patch('cola.github.core.getenv', return_value=None):
        with patch(
            'cola.github.core.run_command', return_value=(1, '', '')
        ) as run_command:
            with patch('cola.github._gh_hosts_path', return_value='/nonexistent'):
                assert github.token(context) is None
                assert github.token(context) is None
    assert run_command.call_count == 1


def test_github_error_is_fatal_for_auth_and_rate_limits():
    """Errors worth retrying are distinguished from ones that are not"""
    assert github.GitHubError('boom').fatal is False
    assert github.GitHubError('boom', fatal=True).fatal is True


@patch('cola.github.core.getenv')
def test_token_prefers_the_environment(getenv):
    getenv.side_effect = lambda name, default=None: (
        'from-env' if name == 'GITHUB_TOKEN' else default
    )
    context = make_context(['origin'], {})
    assert github.token(context) == 'from-env'


def test_token_runs_the_auth_command():
    """A configured auth command takes priority over the "gh" CLI"""
    context = make_context(['origin'], {'cola.githubauthcommand': 'print-my-token'})
    with patch('cola.github.core.getenv', return_value=None):
        with patch(
            'cola.github.core.run_command', return_value=(0, 'gho_fromcommand\n', '')
        ) as run_command:
            assert github.token(context) == 'gho_fromcommand'

    argv, kwargs = run_command.call_args[0][0], run_command.call_args[1]
    # The command runs through a shell so that quoting and pipes behave.
    assert argv[:2] in (['/bin/sh', '-c'], ['cmd', '/c'])
    assert argv[-1] == 'print-my-token'
    assert kwargs['add_env']['COLA_CREDENTIAL_HOST'] == 'github.com'


def test_token_auth_command_receives_the_enterprise_host():
    """The hostname is exported so one command can serve several hosts"""
    context = make_context(
        ['origin'], {'cola.githubauthcommand': 'print-my-token'}, host='git.example.org'
    )
    with patch('cola.github.core.getenv', return_value=None):
        with patch(
            'cola.github.core.run_command', return_value=(0, 'tok\n', '')
        ) as run_command:
            assert github.token(context) == 'tok'

    add_env = run_command.call_args[1]['add_env']
    assert add_env['COLA_CREDENTIAL_HOST'] == 'git.example.org'
    assert add_env['COLA_CREDENTIAL_PROVIDER'] == 'github'


def test_token_falls_back_when_the_auth_command_fails(tmp_path):
    """A broken auth command does not prevent the remaining sources running"""
    hosts = tmp_path / 'hosts.yml'
    hosts.write_text('github.com:\n    oauth_token: gho_fromfile\n')
    context = make_context(['origin'], {'cola.githubauthcommand': 'false'})
    with patch('cola.github.core.getenv', return_value=None):
        with patch('cola.github._gh_hosts_path', return_value=str(hosts)):
            with patch('cola.github.core.run_command', return_value=(1, '', 'nope')):
                assert github.token(context) == 'gho_fromfile'


def test_token_auth_command_ignores_trailing_chatter():
    """Only the first line of output is treated as the token"""
    context = make_context(['origin'], {'cola.githubauthcommand': 'noisy'})
    with patch('cola.github.core.getenv', return_value=None):
        with patch(
            'cola.github.core.run_command',
            return_value=(0, '\ngho_thetoken\nSaved to keyring\n', ''),
        ):
            assert github.token(context) == 'gho_thetoken'


def test_token_skips_the_gh_cli_when_disabled(tmp_path):
    """cola.githubusegh opts out of the "gh" integration entirely"""
    hosts = tmp_path / 'hosts.yml'
    hosts.write_text('github.com:\n    oauth_token: gho_fromfile\n')
    context = make_context(['origin'], {'cola.githubusegh': False})
    with patch('cola.github.core.getenv', return_value=None):
        with patch('cola.github._gh_hosts_path', return_value=str(hosts)):
            with patch('cola.github.core.run_command') as run_command:
                assert github.token(context) is None
    run_command.assert_not_called()


def test_token_reads_the_gh_cli_login(tmp_path):
    """The "gh" CLI's hosts.yml is used when the environment has no token"""
    hosts = tmp_path / 'hosts.yml'
    hosts.write_text(
        'git.example.org:\n'
        '    oauth_token: wrong-host\n'
        'github.com:\n'
        '    users:\n'
        '        joshuataylor:\n'
        '            oauth_token: gho_fromghcli\n'
        '    user: joshuataylor\n'
    )
    context = make_context(['origin'], {})
    with patch('cola.github.core.getenv', return_value=None):
        with patch('cola.github._gh_hosts_path', return_value=str(hosts)):
            assert github.token(context) == 'gho_fromghcli'


def test_token_falls_back_to_the_gh_command(tmp_path):
    """Recent "gh" versions keep the token in the keyring, not in hosts.yml"""
    context = make_context(['origin'], {})
    missing = str(tmp_path / 'missing.yml')
    with patch('cola.github.core.getenv', return_value=None):
        with patch('cola.github._gh_hosts_path', return_value=missing):
            with patch(
                'cola.github.core.run_command',
                return_value=(0, 'gho_fromkeyring\n', ''),
            ):
                assert github.token(context) == 'gho_fromkeyring'


def test_token_without_any_source(tmp_path):
    """No token is reported when "gh" is absent or logged out"""
    context = make_context(['origin'], {})
    missing = str(tmp_path / 'missing.yml')
    with patch('cola.github.core.getenv', return_value=None):
        with patch('cola.github._gh_hosts_path', return_value=missing):
            with patch('cola.github.core.run_command', return_value=(1, '', 'error')):
                assert github.token(context) is None
