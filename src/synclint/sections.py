"""Section splitting: prose documentation divided at its markdown headings."""

from __future__ import annotations

import re
from dataclasses import dataclass

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
        """The section's identity: its file path plus its full heading path."""
        return f"{self.path}#{' > '.join(self.heading_path)}"


def split_sections(markdown: str, path: str) -> list[Section]:
    """Split one markdown file into sections, in document order.

    A section runs from its heading to the next heading of any level, so
    subsections are separate sections rather than nested inside their parent.
    """
    sections: list[Section] = []
    # TODO: two headings with the same path in one file produce two sections
    # with the same id. Links to either become ambiguous; needs an occurrence
    # suffix once `publish` has to point at one of them.
    headings: list[tuple[int, str]] = []
    body: list[str] = []

    def flush() -> None:
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
        flush()
        body = []
        level = len(heading.group(1))
        while headings and headings[-1][0] >= level:
            headings.pop()
        headings.append((level, heading.group(2)))
    flush()

    return sections
