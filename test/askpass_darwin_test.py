"""Tests for the macOS SSH_ASKPASS helper (run against a stub osascript)"""
import os
import shutil
import subprocess
import sys

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'cola', 'bin', 'ssh-askpass-darwin'
)

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='POSIX shell')


def _run(tmp_path, prompt, reply='secret', status=0):
    """Run the helper with a stub osascript that records how it was called"""
    record = tmp_path / 'args'
    stub = tmp_path / 'osascript'
    stub.write_text(
        '#!/bin/sh\n'
        f'printf "%s\\n" "$@" > {record}\n'
        f'cat > {tmp_path / "script"}\n'
        f'printf "%s\\n" "{reply}"\n'
        f'exit {status}\n'
    )
    stub.chmod(0o755)
    env = {'PATH': f'{tmp_path}:/usr/bin:/bin', 'MACOS_ASKPASS_TITLE': 'Git Cola'}
    result = subprocess.run(
        ['/bin/sh', SCRIPT, prompt], env=env, capture_output=True, text=True
    )
    args = record.read_text().splitlines() if record.exists() else []
    return result, args, (tmp_path / 'script').read_text()


def test_passphrase_prompt_is_hidden_and_passed_as_an_argument(tmp_path):
    prompt = 'Enter passphrase for key "/Users/me/.ssh/id \\ed25519":'
    result, args, script = _run(tmp_path, prompt)
    assert result.returncode == 0
    assert result.stdout == 'secret\n'
    assert args == ['-', prompt, 'Git Cola', 'true']
    # The prompt never appears in the AppleScript source.
    assert 'id \\ed25519' not in script
    assert 'Finder' not in script


def test_host_key_question_shows_the_answer(tmp_path):
    prompt = 'Are you sure you want to continue connecting (yes/no/[fingerprint])?'
    _result, args, _script = _run(tmp_path, prompt, reply='yes')
    assert args[-1] == 'false'


def test_cancel_fails(tmp_path):
    result, _args, _script = _run(tmp_path, 'Password:', reply='', status=1)
    assert result.returncode != 0


@pytest.mark.skipif(shutil.which('osacompile') is None, reason='needs macOS')
def test_applescript_compiles(tmp_path):
    with open(SCRIPT, encoding='utf-8') as handle:
        text = handle.read()
    source = text[text.index('on run argv') : text.index('end run') + len('end run')]
    (tmp_path / 'askpass.applescript').write_text(source)
    subprocess.run(
        [
            'osacompile',
            '-o',
            str(tmp_path / 'askpass.scpt'),
            str(tmp_path / 'askpass.applescript'),
        ],
        check=True,
    )
