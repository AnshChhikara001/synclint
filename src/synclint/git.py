"""Reading a repository at a revision, which is the only thing `analyse` needs git for."""

from __future__ import annotations

import subprocess
from pathlib import Path


def modified_python_files(root: Path, base: str, head: str) -> list[str]:
    """The Python files that exist at both revisions and differ between them.

    Files added or deleted by the change are left out. A section describing a
    chunk that no longer exists is a finding of its own and needs git's rename
    detection to tell a deletion from a move; that is #10.
    """
    listing = _git(
        root, "diff", "--name-only", "--diff-filter=M", base, head, "--", "*.py"
    )
    return [line for line in listing.splitlines() if line]


def file_at(root: Path, revision: str, path: str) -> str:
    """Read one file as it stood at a revision."""
    return _git(root, "show", f"{revision}:{path}")


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
    )
    # Source is decoded permissively for the same reason documentation is: one
    # file that is not UTF-8 must not cost the whole run.
    return result.stdout.decode("utf-8", errors="replace")
