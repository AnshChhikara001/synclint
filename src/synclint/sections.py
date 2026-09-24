"""Section splitting: prose documentation divided at its markdown headings."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# ATX headings only. TODO: setext headings (`Title` underlined with `===`) are
# not recognised, so an older README written that way indexes as one section.
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")


@dataclass(frozen=True)
class Section:
    """A unit of prose documentation, delimited by a markdown heading."""

    path: str
    heading_path: tuple[str, ...]
    text: str

    @property
    def id(self) -> str:
        if not self.heading_path:
            return self.path
        return f"{self.path}#{' > '.join(self.heading_path)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "heading_path": list(self.heading_path),
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Section:
        return cls(
            path=data["path"],
            heading_path=tuple(data["heading_path"]),
            text=data["text"],
        )


def split_sections(markdown: str, path: str) -> list[Section]:
    """Split one markdown file into sections, in document order.

    A section runs from its heading to the next heading of any level, so
    subsections are separate sections rather than nested inside their parent.
    Prose above the first heading is a section too, with an empty heading path:
    it is real documentation and can drift like any other.
    """
    # TODO: two headings with the same path in one file produce two sections
    # with the same id. Links to either become ambiguous, and `publish` points
    # at whichever comes first; needs an occurrence suffix.
    sections: list[Section] = []
    headings: list[tuple[int, str]] = []
    body: list[str] = []

    def emit_section() -> None:
        text = "\n".join(body).strip()
        # A section with no prose cannot be inaccurate, so it is not indexed.
        if text:
            sections.append(
                Section(
                    path=path,
                    heading_path=tuple(title for _, title in headings),
                    text=text,
                )
            )

    fenced = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(line)
        if not heading:
            body.append(line)
            continue
        emit_section()
        body = []
        level = len(heading.group(1))
        while headings and headings[-1][0] >= level:
            headings.pop()
        headings.append((level, heading.group(2)))
    emit_section()

    return sections
