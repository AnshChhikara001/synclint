"""The index: every chunk, section and link in a repository at a point in time."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from synclint.chunks import Chunk, extract_chunks


@dataclass(frozen=True)
class Index:
    """What synclint believes about a repository."""

    chunks: tuple[Chunk, ...]


def build_index(root: Path) -> Index:
    """Index the repository rooted at `root`."""
    chunks: list[Chunk] = []
    for path in sorted(root.rglob("*.py")):
        chunks.extend(extract_chunks(path.read_text(), path.relative_to(root).as_posix()))
    return Index(chunks=tuple(chunks))
