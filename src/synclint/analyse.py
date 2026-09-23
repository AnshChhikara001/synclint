"""The second seam: an index and two revisions in, a report of what drifted out."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from synclint.changes import ChunkChange, touched_chunks
from synclint.index import Index
from synclint.model import ModelClient, Spend, SpendCeilingExceeded
from synclint.sections import Section


@dataclass(frozen=True)
class Suspect:
    """A section linked to a changed chunk, before anything has confirmed it drifted."""

    section: Section
    change: ChunkChange


@dataclass(frozen=True)
class Finding:
    """A suspect confirmed to have drifted, with what is now wrong about it."""

    section: str
    chunk: str
    explanation: str


@dataclass(frozen=True)
class Report:
    """What one run of `analyse` concluded.

    `verified` names the sections put to the model; `unchecked` counts the
    suspects never reached, which is zero unless the run stopped at its spend
    ceiling. A section can be a suspect twice over, against two changed chunks,
    so a section can appear in `verified` while a suspect naming it went
    unchecked. Without that count a truncated report would read like a clean one.
    """

    findings: tuple[Finding, ...]
    verified: tuple[str, ...]
    unchecked: int
    spend: Spend


# The second paragraph draws synclint's boundary: silence is not inaccuracy. A
# page that never mentioned the parameter a function just gained is incomplete,
# and this tool reports what is wrong rather than what is missing. That line was
# written here before the corpus was planted, and the corpus contradicted it —
# five cases asserted an undocumented feature was drift. The corpus was wrong
# and now says so: three of the five are `added-parameter` decoys, and the two
# whose prose makes a claim the code falsifies stayed as cases.
# Changing a word of this invalidates every recorded answer, because the cache
# key is a hash of it. Re-record the corpus in the same commit or the published
# numbers stop being reproducible.
_SYSTEM = """\
You verify whether documentation still describes code accurately.

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
    and each one is put to the model before it is reported. Sections verified
    and found accurate are reported too, so that silence about a section means
    it was never a suspect rather than that it passed.

    A run that reaches its spend ceiling returns what it has rather than
    raising: the findings are already paid for, and the report says how many
    suspects it never got to.
    """
    pending = suspects(root, index, base, head)

    findings: list[Finding] = []
    verified: list[str] = []
    unchecked = 0
    for position, suspect in enumerate(pending):
        try:
            finding = _verify(model, suspect)
        except SpendCeilingExceeded:
            unchecked = len(pending) - position
            break
        verified.append(suspect.section.id)
        if finding is not None:
            findings.append(finding)

    return Report(
        findings=tuple(findings),
        verified=tuple(dict.fromkeys(verified)),
        unchecked=unchecked,
        spend=model.spend,
    )


def suspects(root: Path, index: Index, base: str, head: str) -> list[Suspect]:
    """Every section the change from `base` to `head` puts in question, with the change.

    This is the whole of what `analyse` decides before it spends anything: a
    section reaches the model only by appearing here. Separated out so that the
    fixture corpus can measure which planted cases the index and the diff
    actually reach, which costs nothing, apart from whether the model then
    judges them correctly, which does.
    """
    sections = {section.id: section for section in index.sections}
    return [
        Suspect(section=sections[link.section], change=change)
        for change in touched_chunks(root, base, head)
        for link in index.links
        if link.chunk == change.chunk and link.section in sections
    ]


def _verify(model: ModelClient, suspect: Suspect) -> Finding | None:
    answer = json.loads(model.complete(_SYSTEM, _question(suspect), _SCHEMA))
    if answer["accurate"]:
        return None
    return Finding(
        section=suspect.section.id,
        chunk=suspect.change.chunk,
        explanation=answer["explanation"],
    )


def _question(suspect: Suspect) -> str:
    section, change = suspect.section, suspect.change
    return (
        f"Documentation section {section.id}:\n\n{section.text}\n\n"
        f"The chunk {change.chunk} before the change:\n\n{change.before}\n\n"
        f"And after it:\n\n{change.after}"
    )
