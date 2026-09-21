"""Running git, and the two questions `analyse` runs it to answer."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path


def modified_python_files(root: Path, base: str, head: str) -> list[str]:
    """The Python files that exist at both revisions and differ between them.

    Whole files added or deleted by the change are left out, for the reason
    `changes.changed_chunks` gives about the chunks inside a file that survived.
    """
    listing = run(
        root, "diff", "--name-only", "--diff-filter=M", base, head, "--", "*.py"
    )
    return [line for line in listing.splitlines() if line]


def file_at(root: Path, revision: str, path: str) -> str:
    """Read one file as it stood at a revision."""
    return run(root, "show", f"{revision}:{path}")


def run(root: Path, *arguments: str, env: Mapping[str, str] | None = None) -> str:
    """Run git in `root` and return what it printed. Raises if git does."""
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=dict(env) if env is not None else None,
    )
    # Source is decoded permissively for the same reason documentation is: one
    # file that is not UTF-8 must not cost the whole run.
    return result.stdout.decode("utf-8", errors="replace")
