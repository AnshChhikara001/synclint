"""The second seam: an index and two revisions in, a report of what drifted out."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from synclint.changes import ChunkChange, changed_chunks, is_test_file
from synclint.git import file_at, modified_python_files
from synclint.index import Index
from synclint.model import ModelClient, Spend
from synclint.sections import Section


@dataclass(frozen=True)
class Finding:
    """A suspect confirmed to have drifted, with what is now wrong about it."""

    section: str
    chunk: str
    explanation: str


@dataclass(frozen=True)
class Report:
    """What one run of `analyse` concluded."""

    findings: tuple[Finding, ...]
    checked: tuple[str, ...]
    spend: Spend


_SYSTEM = """\
You check whether documentation still describes code accurately.

You are given one section of a project's documentation and one chunk of code it
describes, as that chunk read before a change and as it reads after. Decide
whether the change has made any part of the section wrong.

Judge only what the section actually claims. A section that never mentioned the
thing that changed has not drifted. Neither has one that describes behaviour in
looser terms than the code states it. Say a section has drifted only when a
reader following it would now be misled.

If it has drifted, explain in one sentence what is now wrong. If it has not,
leave the explanation empty."""

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "accurate": {
            "type": "boolean",
            "description": "Whether the section still describes the code correctly.",
        },
        "explanation": {
            "type": "string",
            "description": "What is now wrong, in one sentence. Empty if accurate.",
        },
    },
    "required": ["accurate", "explanation"],
    "additionalProperties": False,
}


def analyse(
    root: Path, index: Index, base: str, head: str, model: ModelClient
) -> Report:
    """Report the sections of `root` that the change from `base` to `head` invalidated.

    Only sections the index links to a chunk the change touched are looked at,
    and each one is put to the model before it is reported. Sections checked and
    found accurate are reported too, so that silence about a section means it
    was never a suspect rather than that it passed.
    """
    sections = {section.id: section for section in index.sections}
    findings: list[Finding] = []
    checked: list[str] = []

    for change in _changes(root, base, head):
        for section in _suspects(index, change, sections):
            verdict = _verify(model, section, change)
            checked.append(section.id)
            if verdict is not None:
                findings.append(verdict)

    return Report(
        findings=tuple(findings),
        checked=tuple(dict.fromkeys(checked)),
        spend=model.spend,
    )


def _changes(root: Path, base: str, head: str) -> list[ChunkChange]:
    changes: list[ChunkChange] = []
    for path in modified_python_files(root, base, head):
        if is_test_file(path):
            continue
        changes.extend(
            changed_chunks(file_at(root, base, path), file_at(root, head, path), path)
        )
    return changes


def _suspects(
    index: Index, change: ChunkChange, sections: dict[str, Section]
) -> list[Section]:
    return [
        sections[link.section]
        for link in index.links
        if link.chunk == change.chunk and link.section in sections
    ]


def _verify(model: ModelClient, section: Section, change: ChunkChange) -> Finding | None:
    answer = json.loads(model.complete(_SYSTEM, _question(section, change), _SCHEMA))
    if answer["accurate"]:
        return None
    return Finding(
        section=section.id, chunk=change.chunk, explanation=answer["explanation"]
    )


def _question(section: Section, change: ChunkChange) -> str:
    return (
        f"Documentation section {section.id}:\n\n{section.text}\n\n"
        f"The chunk {change.chunk} before the change:\n\n{change.before}\n\n"
        f"And after it:\n\n{change.after}"
    )
