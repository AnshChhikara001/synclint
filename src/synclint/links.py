"""Linking: deciding which sections describe which chunks."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from synclint.chunks import Chunk
from synclint.embeddings import EmbeddingClient
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
    candidates = [(chunk, _names_of(chunk)) for chunk in chunks]
    links: list[Link] = []
    for section in sections:
        identifiers = _identifiers_in(section)
        links.extend(
            Link(section=section.id, chunk=chunk.id, mechanism="name")
            for chunk, names in candidates
            if names & identifiers
        )
    return links


def propose_embedding_links(
    sections: Sequence[Section],
    chunks: Sequence[Chunk],
    embeddings: EmbeddingClient,
    threshold: float,
) -> list[Link]:
    """Link every section to the chunks whose embedding is at least `threshold` alike.

    Similarity is cosine, computed as one matrix product over every section
    and every chunk rather than looked up in a vector store (ADR-0002). A
    chunk is embedded without its body, as the index records it, so what is
    compared is the prose against the signature and the docstring.
    """
    if not sections or not chunks:
        return []
    section_vectors = _normalised(embeddings.embed([_section_text(s) for s in sections]))
    chunk_vectors = _normalised(embeddings.embed([_chunk_text(c) for c in chunks]))
    similarity = section_vectors @ chunk_vectors.T
    return [
        Link(section=sections[row].id, chunk=chunks[column].id, mechanism="embedding")
        for row, column in zip(*np.nonzero(similarity >= threshold))
    ]


def _normalised(vectors: np.ndarray) -> np.ndarray:
    # OpenAI's vectors already have unit length, but the threshold means cosine
    # similarity, and that should not rest on one provider's convention.
    normalised: np.ndarray = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    return normalised


def _section_text(section: Section) -> str:
    return "\n".join((*section.heading_path, section.text))


def _chunk_text(chunk: Chunk) -> str:
    return "\n".join(
        (chunk.qualname, *chunk.decorators, chunk.signature, chunk.docstring or "")
    ).strip()


def _names_of(chunk: Chunk) -> set[str]:
    return {chunk.qualname, chunk.qualname.rsplit(".", 1)[-1]}


def _identifiers_in(section: Section) -> set[str]:
    text = "\n".join((*section.heading_path, section.text))
    found: set[str] = set()
    for match in _IDENTIFIER.finditer(text):
        found.add(match.group())
        found.update(part for part in match.group().split(".") if part)
    return found
