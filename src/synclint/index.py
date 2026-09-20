"""The index: every chunk, section and link in a repository at a point in time."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synclint.chunks import Chunk, extract_chunks
from synclint.links import Link, propose_name_links
from synclint.sections import Section, split_sections

DEFAULT_DOCUMENTATION_GLOBS = ("README.md", "docs/**/*.md")


@dataclass(frozen=True)
class Index:
    """What synclint believes about a repository."""

    chunks: tuple[Chunk, ...]
    sections: tuple[Section, ...]
    links: tuple[Link, ...]

    def to_json(self) -> str:
        """Render the index as the JSON that gets committed to the repository.

        Chunk and section ids are derived from the fields beside them. They are
        written out anyway so that a link can be grepped back to what it points
        at, and ignored when reading.
        """
        document = {
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "sections": [section.to_dict() for section in self.sections],
            "links": [link.to_dict() for link in self.links],
        }
        return json.dumps(document, indent=2) + "\n"

    @classmethod
    def from_json(cls, document: str) -> Index:
        """Read back an index rendered by `to_json`."""
        data = json.loads(document)
        return cls(
            chunks=tuple(Chunk.from_dict(chunk) for chunk in data["chunks"]),
            sections=tuple(Section.from_dict(section) for section in data["sections"]),
            links=tuple(Link.from_dict(link) for link in data["links"]),
        )


def build_index(
    root: Path,
    documentation_globs: Sequence[str] = DEFAULT_DOCUMENTATION_GLOBS,
) -> Index:
    """Index the repository rooted at `root`.

    `documentation_globs` are patterns, relative to `root`, naming the markdown
    that counts as documentation. Python is found by walking the tree, so only
    the documentation side is configurable.
    """
    chunks: list[Chunk] = []
    for path in _source(root):
        try:
            chunks.extend(extract_chunks(path.read_bytes(), _relative(path, root)))
        except SyntaxError:
            # A repository can hold Python this interpreter cannot parse: a
            # python 2 file, a template, a fixture broken on purpose. One of
            # them must not cost the whole index.
            continue

    sections: list[Section] = []
    for path in _documentation(root, documentation_globs):
        markdown = path.read_text(encoding="utf-8", errors="replace")
        sections.extend(split_sections(markdown, _relative(path, root)))

    return Index(
        chunks=tuple(chunks),
        sections=tuple(sections),
        links=tuple(propose_name_links(sections, chunks)),
    )


def _source(root: Path) -> list[Path]:
    # TODO: replace with `git ls-files` once `analyse` has made git a hard
    # dependency. That would also exclude a non-hidden virtualenv and anything
    # else the repository has chosen to ignore.
    return sorted(
        path
        for path in root.rglob("*.py")
        if not any(
            part.startswith(".") or part == "__pycache__"
            for part in path.relative_to(root).parts
        )
    )


def _documentation(root: Path, documentation_globs: Sequence[str]) -> list[Path]:
    matched = {
        path
        for glob in documentation_globs
        for path in root.glob(glob)
        if path.is_file() and _within(root, path)
    }
    return sorted(matched)


def _within(root: Path, path: Path) -> bool:
    # A glob can climb out with `..`, and documentation outside the repository
    # is out of scope — synclint only ever reports on what the repository owns.
    return path.resolve().is_relative_to(root.resolve())


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
