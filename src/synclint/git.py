"""Running git, and the two questions `analyse` runs it to answer."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileChange:
    """One Python file a change touched, by its path at each revision.

    `before` is `None` for a file the change added and `after` for one it
    deleted. A file git judges renamed carries both paths, so the chunks inside
    it are compared across the move rather than reported gone.
    """

    before: str | None
    after: str | None


def changed_python_files(root: Path, base: str, head: str) -> list[FileChange]:
    """The Python files that differ between two revisions, with renames paired up.

    Renames are asked for explicitly rather than left to `diff.renames`, which
    a user's configuration can turn off. `-z` keeps a path with a tab or a
    non-ASCII character in it from being quoted.
    """
    fields = run(
        root, "diff", "-z", "--name-status", "--find-renames", base, head, "--", "*.py"
    ).split("\0")
    files: list[FileChange] = []
    position = 0
    while position < len(fields) - 1:
        status = fields[position]
        if status.startswith("R"):
            files.append(FileChange(fields[position + 1], fields[position + 2]))
            position += 3
            continue
        path = fields[position + 1]
        files.append(
            FileChange(
                before=None if status == "A" else path,
                after=None if status == "D" else path,
            )
        )
        position += 2
    return files


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
