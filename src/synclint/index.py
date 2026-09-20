"""The index: every chunk, section and link in a repository at a point in time."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synclint.chunks import Chunk, extract_chunks
from synclint.links import Link, propose_name_links
from synclint.sections import Section, split_sections

DEFAULT_DOC_GLOBS = ("README.md", "docs/**/*.md")


@dataclass(frozen=True)
class Index:
    """What synclint believes about a repository."""

    chunks: tuple[Chunk, ...]
    sections: tuple[Section, ...]
    links: tuple[Link, ...]


def build_index(root: Path, doc_globs: Sequence[str] = DEFAULT_DOC_GLOBS) -> Index:
    """Index the repository rooted at `root`."""
    chunks: list[Chunk] = []
    for path in sorted(root.rglob("*.py")):
        chunks.extend(extract_chunks(path.read_text(), _relative(path, root)))

    sections: list[Section] = []
    for path in _documentation(root, doc_globs):
        sections.extend(split_sections(path.read_text(), _relative(path, root)))

    return Index(
        chunks=tuple(chunks),
        sections=tuple(sections),
        links=tuple(propose_name_links(sections, chunks)),
    )


def _documentation(root: Path, doc_globs: Sequence[str]) -> list[Path]:
    matched = {path for glob in doc_globs for path in root.glob(glob) if path.is_file()}
    return sorted(matched)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
