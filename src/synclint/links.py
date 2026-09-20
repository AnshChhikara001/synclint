"""Linking: deciding which sections describe which chunks."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from synclint.chunks import Chunk
from synclint.sections import Section

# Dotted so that `repo.Repo.fetch` in prose is one token rather than three.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


@dataclass(frozen=True)
class Link:
    """A recorded relationship asserting that a section describes a chunk."""

    section: str
    chunk: str
    mechanism: str

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "chunk": self.chunk, "mechanism": self.mechanism}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Link:
        return cls(
            section=data["section"],
            chunk=data["chunk"],
            mechanism=data["mechanism"],
        )


def propose_name_links(sections: Iterable[Section], chunks: Iterable[Chunk]) -> list[Link]:
    """Link every section to the chunks it names.

    A section names a chunk when the chunk's qualified name, or the last
    component of it, appears in the section's heading or prose as a whole
    identifier. Bare method names match, which is deliberately permissive —
    precision is measured against the fixture corpus before it is tuned.
    """
    chunks = list(chunks)
    links: list[Link] = []
    for section in sections:
        identifiers = _identifiers(section)
        links.extend(
            Link(section=section.id, chunk=chunk.id, mechanism="name")
            for chunk in chunks
            if _names(identifiers, chunk)
        )
    return links


def _names(identifiers: set[str], chunk: Chunk) -> bool:
    return chunk.qualname in identifiers or chunk.qualname.rsplit(".", 1)[-1] in identifiers


def _identifiers(section: Section) -> set[str]:
    text = "\n".join((*section.heading_path, section.text))
    found: set[str] = set()
    for match in _IDENTIFIER.finditer(text):
        found.add(match.group())
        found.update(part for part in match.group().split(".") if part)
    return found
