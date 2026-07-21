from __future__ import annotations
import datetime
import json
import re
from collections.abc import Iterator

from .. import core
from .. import utils
from ..i18n import N_
from ..models import prefs

# put summary at the end b/c it can contain
# any number of funky characters, including the separator
LOGFMT = r'format:%H%x01%P%x01%d%x01%an%x01%ad%x01%at%x01%ae' r'%x01%cn%x01%cd%x01%ct%x01%ce%x01%s'
LOGSEP = chr(0x01)
STAGE = 'STAGE'
WORKTREE = 'WORKTREE'

# Signature verification is deliberately kept out of LOGFMT. The %G?
# placeholders make "git log" verify every signed commit before it emits any
# output, which would delay the whole DAG. Signatures are read separately by
# read_signatures() once the commits are on screen.
SIGNATURE_LOGFMT = r'format:%H%x01%G?%x01%GS%x01%GK'

OID_REGEX = re.compile(r'^[0-9a-f]{7,64}$')
"""Matches an object ID, which STAGE and WORKTREE deliberately are not"""


class SignatureStatus:
    """Severity levels derived from git's "%G?" signature status character"""

    NONE = 'none'
    """The commit is not signed"""
    GOOD = 'good'
    """The signature is valid and trusted"""
    UNKNOWN = 'unknown'
    """The signature is valid but its validity or key could not be established"""
    BAD = 'bad'
    """The signature is invalid or was made by a revoked key"""


_SIGNATURE_SEVERITIES = {
    'G': SignatureStatus.GOOD,
    'U': SignatureStatus.UNKNOWN,
    'X': SignatureStatus.UNKNOWN,
    'Y': SignatureStatus.UNKNOWN,
    'E': SignatureStatus.UNKNOWN,
    'R': SignatureStatus.BAD,
    'B': SignatureStatus.BAD,
}


def signature_severity(commit) -> str:
    """Return the SignatureStatus severity for a commit

    >>> signature_severity(None)
    'none'

    """
    signature = getattr(commit, 'signature', '')
    return _SIGNATURE_SEVERITIES.get(signature, SignatureStatus.NONE)


def signature_label(commit) -> str:
    """Return a short label describing a commit's signature status"""
    labels = {
        'G': N_('Verified'),
        'U': N_('Unknown validity'),
        'X': N_('Expired signature'),
        'Y': N_('Expired key'),
        'R': N_('Revoked key'),
        'B': N_('Bad signature'),
        'E': N_('Unchecked'),
    }
    return labels.get(getattr(commit, 'signature', ''), '')


def signature_tooltip(commit) -> str:
    """Return a multi-line description of a commit's signature, or an empty string

    Both the local "%G?" verification and GitHub's verdict are reported when
    they are available. They can legitimately disagree -- GitHub validates
    against the keys its users registered, whereas git validates against your
    local keyring -- so neither result is hidden.
    """
    descriptions = {
        'G': N_('Good signature'),
        'U': N_('Good signature with unknown validity'),
        'X': N_('Good signature that has expired'),
        'Y': N_('Good signature made by an expired key'),
        'R': N_('Good signature made by a revoked key'),
        'B': N_('Bad signature'),
        'E': N_('Signature could not be checked'),
    }
    lines = []
    description = descriptions.get(getattr(commit, 'signature', ''), '')
    if description:
        signer = commit.signer
        if signer:
            description = f'{description} - {signer}'
        lines.append(description)
        if commit.signing_key:
            lines.append(N_('Key: %s') % commit.signing_key)

    verification = getattr(commit, 'github_verification', None)
    if verification:
        if verification.get('verified'):
            lines.append(N_('Verified by GitHub'))
        else:
            reason = verification.get('reason') or N_('unverified')
            lines.append(N_('Not verified by GitHub: %s') % reason)

    return '\n'.join(lines)


def read_signatures(context, oids: list[str]) -> dict[str, tuple[str, str, str]]:
    """Verify the specified commits and return {oid: (status, signer, key)}

    Verification is expensive -- roughly a GPG or SSH invocation per signed
    commit -- so this is called in the background for a batch of commits at a
    time rather than being folded into the log that populates the DAG.

    "--no-walk" restricts the output to the commits that were asked for
    instead of walking their history. Anything that is not an object ID is
    dropped first: the STAGE and WORKTREE placeholders are not revisions, and
    a single unknown argument makes "git log" fail the entire batch.
    """
    oids = [oid for oid in oids if OID_REGEX.match(oid)]
    if not oids:
        return {}
    cmd = [
        'git',
        '-c',
        'log.abbrevCommit=false',
        '-c',
        'log.showSignature=false',
        'log',
        '--no-walk=unsorted',
        '--no-patch',
        '--pretty=' + SIGNATURE_LOGFMT,
        *oids,
    ]
    # Signature verification writes complaints to stderr, e.g. when
    # gpg.ssh.allowedSignersFile is unset. The status character already
    # conveys that, so stderr is discarded.
    status, out, _ = core.run_command(cmd)
    if status != 0:
        return {}

    signatures = {}
    for line in out.splitlines():
        if not line:
            continue
        fields = line.split(LOGSEP)
        if len(fields) != 4:
            continue
        oid, signature, signer, signing_key = fields
        signatures[oid] = (signature, signer, signing_key)
    return signatures


def _parse_timestamp(value: str) -> int:
    """Parse a Unix timestamp from git log, tolerating garbage"""
    try:
        return int(value)
    except ValueError:
        return 0


class CommitFactory:
    root_generation = 0
    commits = {}

    @classmethod
    def reset(cls) -> None:
        cls.commits.clear()
        cls.root_generation = 0

    @classmethod
    def new(
        cls, context, oid: str | None = None, log_entry: str | None = None
    ) -> Commit:
        if not oid and log_entry:
            oid = log_entry[: context.model.oid_len]
        try:
            commit = cls.commits[oid]
            if log_entry and not commit.parsed:
                commit.parse(log_entry)
            cls.root_generation = max(commit.generation, cls.root_generation)
        except KeyError:
            commit = Commit(context, oid=oid, log_entry=log_entry)
            if not log_entry:
                cls.root_generation += 1
                commit.generation = max(commit.generation, cls.root_generation)
            cls.commits[oid] = commit
        return commit


class DAG:
    def __init__(self, ref: str, count: int) -> None:
        self.ref = ref
        self.count = count
        self.display_status = True
        self.overrides = {}

    def set_ref(self, ref: str) -> bool:
        changed = ref != self.ref
        if changed:
            self.ref = ref
        return changed

    def set_count(self, count: int) -> bool:
        changed = count != self.count
        if changed:
            self.count = count
        return changed

    def set_arguments(self, args) -> None:
        if args is None:
            return
        if self.set_count(args.count):
            self.overrides['count'] = args.count

        if hasattr(args, 'args') and args.args:
            ref = core.list2cmdline(args.args)
            if self.set_ref(ref):
                self.overrides['ref'] = ref

    def set_display_status(self, enabled: bool) -> None:
        """Should we display the worktree status?"""
        self.display_status = enabled

    def overridden(self, opt: str) -> bool:
        return opt in self.overrides

    def paths(self) -> list[str]:
        all_refs = utils.shell_split(self.ref)
        if '--' in all_refs:
            all_refs = all_refs[all_refs.index('--') :]

        return [p for p in all_refs if p and core.exists(p)]


class Commit:
    root_generation = 0

    __slots__ = (
        'authdate',
        'author',
        'branches',
        'children',
        'column',
        'context',
        'email',
        'committer',
        'commitdate',
        'committer_timestamp',
        'committer_email',
        'signature',
        'signer',
        'signing_key',
        'github_verification',
        'generation',
        'oid',
        'parents',
        'parsed',
        'row',
        'summary',
        'tags',
        'timestamp',
    )

    def __init__(
        self, context, oid: str | None = None, log_entry: str | None = None
    ) -> None:
        self.context = context
        self.oid = oid
        self.summary: str | None = None
        self.parents: list[Commit] = []
        self.children = []
        self.tags: list[str] = []
        self.branches = []
        self.email: str | None = None
        self.author: str | None = None
        self.authdate: str | None = None
        self.timestamp: int = 0
        """The author date as a Unix timestamp, for date formatting and filtering"""
        self.committer: str | None = None
        self.committer_email: str | None = None
        self.commitdate: str | None = None
        self.committer_timestamp: int = 0
        self.signature: str = ''
        """The "%G?" signature status character, empty when not requested"""
        self.signer: str = ''
        self.signing_key: str = ''
        self.github_verification: dict | None = None
        """GitHub's verdict, filled in asynchronously when enabled"""
        self.parsed = False
        self.generation = CommitFactory.root_generation
        self.column = None
        self.row = None
        if log_entry:
            self.parse(log_entry)

    def parse(self, log_entry: str, sep: str = LOGSEP):
        oid_len = self.context.model.oid_len
        self.oid = log_entry[:oid_len]
        after_oid = log_entry[oid_len + 1 :]
        details = after_oid.split(sep, 10)
        (
            parents,
            tags,
            author,
            authdate,
            timestamp,
            email,
            committer,
            commitdate,
            committer_timestamp,
            committer_email,
            summary,
        ) = details

        self.summary = summary if summary else ''
        self.author = author if author else ''
        self.authdate = authdate if authdate else ''
        self.email = email if email else ''
        self.committer = committer if committer else ''
        self.commitdate = commitdate if commitdate else ''
        self.committer_email = committer_email if committer_email else ''
        self.timestamp = _parse_timestamp(timestamp)
        self.committer_timestamp = _parse_timestamp(committer_timestamp)

        if parents:
            generation = None
            for parent_oid in parents.split(' '):
                parent = CommitFactory.new(self.context, oid=parent_oid)
                parent.children.append(self)
                if generation is None:
                    generation = parent.generation + 1
                self.parents.append(parent)
                generation = max(parent.generation + 1, generation)
            self.generation = generation

        if tags:
            for tag in tags[2:-1].split(', '):
                self.add_label(tag)

        self.parsed = True
        return self

    def add_label(self, tag: str) -> None:
        """Add tag/branch labels from `git log --decorate ....`"""
        tag = tag.removeprefix('tag: ')  # strip off "tag: " leaving refs/tags/
        if tag.startswith('refs/heads/'):
            branch = tag[11:]
            self.branches.append(branch)
        tag = tag.removeprefix('refs/')
        if tag.endswith('/HEAD'):
            return

        # Git 2.4 Release Notes (draft)
        # =============================
        #
        # Backward compatibility warning(s)
        # ---------------------------------
        #
        # This release has a few changes in the user-visible output from
        # Porcelain commands. These are not meant to be parsed by scripts, but
        # the users still may want to be aware of the changes:
        #
        # * Output from "git log --decorate" (and "%d" format specifier used in
        #   the userformat "--format=<string>" parameter "git log" family of
        #   command takes) used to list "HEAD" just like other tips of branch
        #   names, separated with a comma in between.  E.g.
        #
        #      $ git log --decorate -1 main
        #      commit bdb0f6788fa5e3cacc4315e9ff318a27b2676ff4 (HEAD, main)
        #      ...
        #
        # This release updates the output slightly when HEAD refers to the tip
        # of a branch whose name is also shown in the output.  The above is
        # shown as:
        #
        #      $ git log --decorate -1 main
        #      commit bdb0f6788fa5e3cacc4315e9ff318a27b2676ff4 (HEAD -> main)
        #      ...
        #
        # C.f. http://thread.gmane.org/gmane.linux.kernel/1931234
        head_arrow = 'HEAD -> '
        if tag.startswith(head_arrow):
            self.tags.append('HEAD')
            self.add_label(tag[len(head_arrow) :])
        else:
            self.tags.append(tag)

    def __str__(self) -> str | None:
        return self.oid

    def data(self) -> dict[str, str | None | list[str | None] | list[str]]:
        data: dict[str, str | None | list[str | None] | list[str]] = {
            'oid': self.oid,
            'summary': self.summary,
            'author': self.author,
            'authdate': self.authdate,
            'parents': [p.oid for p in self.parents],
            'tags': self.tags,
        }
        if self.signature:
            data['signature'] = self.signature
            data['signer'] = self.signer
            data['signing_key'] = self.signing_key
        return data

    def __repr__(self) -> str:
        return json.dumps(self.data(), sort_keys=True, indent=4, default=list)

    def is_fork(self) -> bool:
        """Returns True if the node is a fork"""
        return len(self.children) > 1

    def is_merge(self) -> bool:
        """Returns True if the node is a fork"""
        return len(self.parents) > 1


class RepoReader:
    def __init__(self, context, params: DAG, allow_git_init: bool = True) -> None:
        self.context = context
        self.params = params
        self.git = context.git
        self.returncode = 0
        self._allow_git_init = allow_git_init
        self._objects: dict[str, Commit] = {}
        self._cmd = [
            'git',
            '-c',
            'log.abbrevCommit=false',
            '-c',
            'log.showSignature=false',
            'log',
            '--topo-order',
            '--decorate=full',
            '--pretty=' + LOGFMT,
        ]
        self._cached = False
        """Indicates that all data has been read"""
        self._topo_list = []
        """List of commits objects in topological order"""

    cached = property(lambda self: self._cached)
    """Return True when no commits remain to be read"""

    def __len__(self) -> int:
        return len(self._topo_list)

    def reset(self) -> None:
        CommitFactory.reset()
        self._cached = False
        self._topo_list = []

    def get(self) -> Iterator[Commit]:
        """Generator function returns Commit objects found by the params"""
        if self._cached:
            for commit in self._topo_list:
                yield commit
            return

        self.reset()
        ref_args = utils.shell_split(self.params.ref)
        cmd = (
            self._cmd
            + ['-%d' % self.params.count]
            + [f'--date={prefs.logdate(self.context)}']
            + ['--no-patch']
            + ref_args
        )
        commit = None

        # When _allow_git_init is True then we detect the "git init" state
        # by checking whether any local branches currently exist.
        if not self._allow_git_init or self.context.model.local_branches:
            status, out, _ = core.run_command(cmd)
            oid_len = self.context.model.oid_len
            for log_entry in reversed(out.splitlines()):
                if not log_entry:
                    break
                oid = log_entry[:oid_len]
                try:
                    commit = self._objects[oid]
                except KeyError:
                    try:
                        commit = CommitFactory.new(self.context, log_entry=log_entry)
                    except (KeyError, ValueError):
                        continue
                    self._objects[commit.oid] = commit
                    self._topo_list.append(commit)
                yield commit
        else:
            # git init
            status = 0
        self._top_commit = commit
        self._cached = True
        self.returncode = status

    def get_worktree_commits(self) -> tuple[Commit | None, Commit | None]:
        """A Commit object that represents unstaged modified changes in a worktree"""
        if self.returncode != 0 or not self.params.display_status:
            return None, None
        context = self.context
        model = context.model
        if not model.modified and not model.staged and not model.unmerged:
            return None, None
        parents = []
        parent_commit = self._top_commit
        status, head, _ = context.git.rev_parse('HEAD', _readonly=True)
        if status != 0:
            # "git init" should include worktree and stage entries.
            # We do not early out with None and leave the parents list empty.
            pass
        elif parent_commit:
            # Is the top-most commit also our current HEAD?
            # If so we'll include worktree and stage placeholder commits
            # otherwise we should early out and omit these entries.
            if head != parent_commit.oid:
                return None, None
            parents = [parent_commit]

        author, email = context.cfg.get_author()
        if model.commitmsg:
            summary = model.commitmsg.split('\n', 1)[0]
        else:
            summary = ''

        if summary:
            stage_summary = f'STAGE: {summary}'
            worktree_summary = f'WORKTREE: {summary}'
        else:
            stage_summary = N_('STAGE: changes ready to commit')
            worktree_summary = N_('WORKTREE: unstaged changes')
        authdate = get_date_for_current_time(context)
        timestamp = int(datetime.datetime.now().timestamp())

        stage_commit = None
        worktree_commit = None

        if model.staged:
            stage_commit = Commit(context, oid=STAGE)
            stage_commit.add_label(STAGE)
            stage_commit.parents = parents
            stage_commit.summary = stage_summary
            stage_commit.author = author
            stage_commit.email = email
            stage_commit.authdate = authdate
            stage_commit.timestamp = timestamp
            stage_commit.committer = author
            stage_commit.committer_email = email
            stage_commit.commitdate = authdate
            stage_commit.committer_timestamp = timestamp
            stage_commit.parsed = True
            if parent_commit:
                parent_commit.children.append(stage_commit)
                stage_commit.generation = parent_commit.generation + 1
            # Update state for the subsequent WORKTREE pseudo-commit.
            parents = [stage_commit]
            parent_commit = stage_commit

        if model.modified or model.unmerged:
            worktree_commit = Commit(context, oid=WORKTREE)
            worktree_commit.add_label(WORKTREE)
            worktree_commit.parents = parents
            worktree_commit.summary = worktree_summary
            worktree_commit.author = author
            worktree_commit.email = email
            worktree_commit.authdate = authdate
            worktree_commit.timestamp = timestamp
            worktree_commit.committer = author
            worktree_commit.committer_email = email
            worktree_commit.commitdate = authdate
            worktree_commit.committer_timestamp = timestamp
            worktree_commit.parsed = True
            if parent_commit:
                parent_commit.children.append(worktree_commit)
                worktree_commit.generation = parent_commit.generation + 1

        return stage_commit, worktree_commit

    def __getitem__(self, oid: str) -> Commit:
        return self._objects[oid]

    def items(self) -> list[tuple[str, Commit]]:
        return list(self._objects.items())


def get_date_for_current_time(context) -> str:
    """Return the current time formatted according to the cola.logdate configuration"""
    DateFormat = prefs.DateFormat
    logdate = prefs.logdate(context)
    now = datetime.datetime.now().astimezone()
    if logdate == DateFormat.DEFAULT:
        authdate = now.strftime('%c %z')
    elif logdate == DateFormat.HUMAN:
        authdate = now.strftime('%a %b %d %H:%M')
    elif logdate == DateFormat.LOCAL:
        authdate = now.strftime('%c')
    elif logdate == DateFormat.ISO:
        authdate = now.strftime('%Y-%m-%d %H:%M:%S %z')
    elif logdate == DateFormat.ISO_STRICT:
        authdate = now.strftime('%Y-%m-%dT%H:%M:%S%z')
    elif logdate == DateFormat.RAW:
        authdate = now.strftime('%s %z')
    elif logdate == DateFormat.RELATIVE:
        authdate = '0 seconds ago'
    elif logdate == DateFormat.RFC:
        authdate = now.strftime('%a, %e %b %Y %H:%M:%S %z')
    elif logdate == DateFormat.SHORT:
        authdate = now.strftime('%Y-%m-%d')
    elif logdate == DateFormat.UNIX:
        authdate = now.strftime('%s')
    elif DateFormat.is_custom(logdate):
        fmt = DateFormat.get_custom_format(logdate)
        authdate = now.strftime(fmt)
    else:
        authdate = now.strftime('%c %z')
    return authdate
