"""GitRepo: a small, read-only Git discovery adapter.

This module answers exactly one question -- "what does the Git
repository at this path actually say?" -- via the `git` CLI, and
nothing else. It never runs a mutating Git command (no checkout, no
reset, no fetch, no clean), never decides what any of the facts it
returns *mean* for QCH's domain model, and never imports anything from
`qch.services`/`qch.hub`/`qch.repositories`. That interpretation layer
belongs entirely to callers such as
`qch.importers.ecdsafail_history.ECDSAFailHistoryImporter` -- see its
own module docstring for why this separation matters (DB-2 Phase A2,
section 6: "Separate Git discovery from QCH ingestion/classification").

Every method here shells out to a real `git` binary (no GitPython or
other dependency) and parses its plumbing-stable `--format` output.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Field/record separators chosen to never plausibly appear in a commit
# subject line -- \x1f/\x1e are the ASCII "unit/record separator"
# control characters, not printable text a commit message would contain.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


@dataclass
class GitCommitInfo:
    """One commit's plumbing-level facts -- no interpretation."""

    sha: str
    parent_shas: list[str]
    author_name: str
    author_email: str
    author_date: str  # ISO 8601, e.g. "2026-05-29T12:00:00+00:00"
    subject: str  # first line of the commit message only


@dataclass
class GitBranchInfo:
    """One ref matching a caller-given glob -- name is the ref's own
    short name (e.g. "submissions/<uuid>"), not the full refname."""

    name: str
    target_sha: str


class GitCommandError(RuntimeError):
    """A `git` invocation exited non-zero. Wraps stderr for diagnosis."""


@dataclass
class GitRepo:
    """A read-only handle on one Git working copy. Every method is a
    plain `git` subprocess call; nothing here mutates the repository."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def _run(self, args: list[str]) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise GitCommandError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def remote_url(self, remote: str = "origin") -> str | None:
        try:
            return self._run(["remote", "get-url", remote]).strip() or None
        except GitCommandError:
            return None

    def rev_parse(self, ref: str) -> str:
        """The full 40-character SHA a ref currently resolves to."""
        return self._run(["rev-parse", ref]).strip()

    def commit_count(self, ref: str) -> int:
        return int(self._run(["rev-list", "--count", ref]).strip())

    def list_branches(self, name_glob: str = "*") -> list[GitBranchInfo]:
        """Every branch ref (local and `remotes/<remote>/...`) whose
        short name matches `name_glob` (a `git for-each-ref` glob, e.g.
        "submissions/*") -- deduplicated by target SHA + short name so
        a branch that exists both locally and on `origin` is not
        double-counted."""
        output = self._run(
            [
                "for-each-ref",
                f"--format=%(refname){_FIELD_SEP}%(objectname)",
                f"refs/heads/{name_glob}",
                f"refs/remotes/*/{name_glob}",
            ]
        )
        seen: set[tuple[str, str]] = set()
        branches: list[GitBranchInfo] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            full_ref, sha = line.split(_FIELD_SEP)
            # "refs/heads/submissions/<uuid>" -> "submissions/<uuid>";
            # "refs/remotes/origin/submissions/<uuid>" -> the same --
            # so a branch known only via "origin/" and one that also
            # exists locally are never double-counted.
            if full_ref.startswith("refs/heads/"):
                name = full_ref[len("refs/heads/") :]
            elif full_ref.startswith("refs/remotes/"):
                name = full_ref.split("/", 3)[3]  # drop "refs/remotes/<remote>/"
            else:
                name = full_ref
            key = (name, sha)
            if key in seen:
                continue
            seen.add(key)
            branches.append(GitBranchInfo(name=name, target_sha=sha))
        return branches

    def log(self, ref: str) -> list[GitCommitInfo]:
        """Every commit reachable from `ref`, oldest-parent info
        included, in one batched `git log` call (never one subprocess
        per commit -- see DB-2 Phase A2 section 23)."""
        output = self._run(
            [
                "log",
                ref,
                f"--format=%H{_FIELD_SEP}%P{_FIELD_SEP}%an{_FIELD_SEP}%ae{_FIELD_SEP}%aI{_FIELD_SEP}%s{_RECORD_SEP}",
            ]
        )
        commits: list[GitCommitInfo] = []
        for record in output.split(_RECORD_SEP):
            record = record.strip("\n")
            if not record.strip():
                continue
            sha, parents, author_name, author_email, author_date, subject = record.split(_FIELD_SEP)
            commits.append(
                GitCommitInfo(
                    sha=sha,
                    parent_shas=parents.split() if parents else [],
                    author_name=author_name,
                    author_email=author_email,
                    author_date=author_date,
                    subject=subject,
                )
            )
        return commits

    def trailer_values(self, key: str, *, all_refs: bool = True, ref: str = "HEAD") -> list[tuple[str, str]]:
        """(commit SHA, verbatim trailer value) for every `key:` trailer
        (git matches the key case-insensitively) of every commit reachable
        from all refs (default) or from `ref` -- one batched `git log`
        call. Values are returned as-is; interpreting them is the
        caller's job (QCH Phase 2D.6)."""
        output = self._run(
            [
                "log",
                "--all" if all_refs else ref,
                f"--format=%H{_FIELD_SEP}%(trailers:key={key},valueonly,separator=%x1d){_RECORD_SEP}",
            ]
        )
        values: list[tuple[str, str]] = []
        for record in output.split(_RECORD_SEP):
            record = record.strip("\n")
            if _FIELD_SEP not in record:
                continue
            sha, trailers = record.split(_FIELD_SEP, 1)
            for value in trailers.split("\x1d"):
                value = value.strip()
                if value:
                    values.append((sha.strip(), value))
        return values

    def commits_info(self, shas: list[str]) -> dict[str, GitCommitInfo]:
        """Info for exactly the given SHAs, in as few `git` calls as
        possible (`--no-walk` so nothing is traversed, just looked up)
        -- used for submission-branch tips that are not reachable from
        `main` and so never appear in a `log(main_ref)` call. Chunked
        to stay well under typical OS command-line length limits."""
        if not shas:
            return {}
        results: dict[str, GitCommitInfo] = {}
        chunk_size = 200
        for start in range(0, len(shas), chunk_size):
            chunk = shas[start : start + chunk_size]
            output = self._run(
                [
                    "log",
                    "--no-walk",
                    f"--format=%H{_FIELD_SEP}%P{_FIELD_SEP}%an{_FIELD_SEP}%ae{_FIELD_SEP}%aI{_FIELD_SEP}%s{_RECORD_SEP}",
                    *chunk,
                ]
            )
            for record in output.split(_RECORD_SEP):
                record = record.strip("\n")
                if not record.strip():
                    continue
                sha, parents, author_name, author_email, author_date, subject = record.split(_FIELD_SEP)
                results[sha] = GitCommitInfo(
                    sha=sha,
                    parent_shas=parents.split() if parents else [],
                    author_name=author_name,
                    author_email=author_email,
                    author_date=author_date,
                    subject=subject,
                )
        return results

    def merge_base(self, a: str, b: str) -> str | None:
        """The best common ancestor of `a` and `b` (`git merge-base`),
        or None if they share no history. Used by DB-5 Phase A11's
        version-graph reconstruction to find where a submission branch
        that never reached `main` actually forked from it -- a pure
        read, like every other method here."""
        try:
            return self._run(["merge-base", a, b]).strip() or None
        except GitCommandError:
            return None

    def commit_info(self, sha: str) -> GitCommitInfo:
        """A single commit's info -- prefer `log()` for many commits;
        this exists for the rare one-off lookup (e.g. a branch tip not
        already covered by `log(main_ref)`)."""
        output = self._run(
            ["show", "-s", f"--format=%H{_FIELD_SEP}%P{_FIELD_SEP}%an{_FIELD_SEP}%ae{_FIELD_SEP}%aI{_FIELD_SEP}%s", sha]
        )
        commit_sha, parents, author_name, author_email, author_date, subject = output.strip("\n").split(_FIELD_SEP)
        return GitCommitInfo(
            sha=commit_sha,
            parent_shas=parents.split() if parents else [],
            author_name=author_name,
            author_email=author_email,
            author_date=author_date,
            subject=subject,
        )
