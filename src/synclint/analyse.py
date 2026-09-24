"""The second seam: an index and two revisions in, a report of what drifted out."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from synclint.changes import ChunkChange, touched_chunks
from synclint.confidence import DEFAULT_THRESHOLD, outside_reason, rate, shape_of
from synclint.index import Index
from synclint.model import ModelClient, Spend, SpendCeilingExceeded
from synclint.repair import Rejected, repair
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
class Repair:
    """A finding resolved to a rewritten section safe to propose.

    It passed validation, its change has one of the shapes the gate admits,
    and the model's `confidence` in it reached the threshold.
    """

    finding: Finding
    original: str
    repaired: str
    shape: str
    confidence: float

    @property
    def diff(self) -> str:
        """The repair as a unified diff against the section it rewrites."""
        return section_diff(self.finding.section, self.original, self.repaired)


def section_diff(section: str, original: str, rewritten: str) -> str:
    """A rewrite of `section` as a unified diff against its original text."""
    # A section is stripped of its trailing newline, and without one the last
    # line of each side runs into the next line of the diff.
    return "".join(
        difflib.unified_diff(
            (original + "\n").splitlines(keepends=True),
            (rewritten + "\n").splitlines(keepends=True),
            fromfile=section,
            tofile=section,
        )
    )


# Why a finding was flagged rather than repaired. The repair's edits could not
# be applied; validation refused the rewrite; the change is outside the gate;
# the model's confidence inside it fell short of the threshold; or the run
# reached its spend ceiling first.
Cause = Literal["unappliable", "refused", "outside", "doubted", "ceiling"]


@dataclass(frozen=True)
class Flag:
    """A finding surfaced for a human rather than repaired, and why.

    `attempt` is the rewrite that was refused, where there was one to refuse,
    and `original` the section it would have replaced. `shape` is the gate's
    name for the change, `None` outside it, and `confidence` the model's,
    `None` unless the repair got far enough to be given one.
    """

    finding: Finding
    reason: str
    original: str
    attempt: str | None
    cause: Cause
    shape: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class Report:
    """What one run of `analyse` concluded.

    `verified` names the sections put to the model; `unchecked` counts the
    suspects never reached, which is zero unless the run stopped at its spend
    ceiling. A section can be a suspect twice over, against two changed chunks,
    so a section can appear in `verified` while a suspect naming it went
    unchecked. Without that count a truncated report would read like a clean one.

    Every finding resolves to exactly one repair or one flag. `unrepaired`
    counts the flags that are there only because the run reached its ceiling
    first, so that a caller can tell them from the flags validation earned.
    """

    findings: tuple[Finding, ...]
    verified: tuple[str, ...]
    unchecked: int
    unrepaired: int
    repairs: tuple[Repair, ...]
    flags: tuple[Flag, ...]
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
    root: Path,
    index: Index,
    base: str,
    head: str,
    model: ModelClient,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> Report:
    """Report the sections of `root` that the change from `base` to `head` invalidated.

    Only sections the index links to a chunk the change touched are looked at,
    and each one is put to the model before it is reported. Sections verified
    and found accurate are reported too, so that silence about a section means
    it was never a suspect rather than that it passed.

    Each finding is then repaired, and the repair validated. It is proposed
    only if validation passes it, its change has a shape the gate admits, and
    the model's confidence in it reaches `threshold`; otherwise it becomes a
    flag that says which of those it failed.

    A run that reaches its spend ceiling returns what it has rather than
    raising: the findings are already paid for, and the report says how many
    suspects it never got to.
    """
    pending = suspects(root, index, base, head)

    drifted: list[tuple[Suspect, Finding]] = []
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
            drifted.append((suspect, finding))

    repairs, flags = _resolve(model, drifted, threshold)
    return Report(
        findings=tuple(finding for _, finding in drifted),
        verified=tuple(dict.fromkeys(verified)),
        unchecked=unchecked,
        unrepaired=sum(1 for flag in flags if flag.cause == "ceiling"),
        repairs=repairs,
        flags=flags,
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
    # A pair both mechanisms proposed is linked twice, once under each, and is
    # still one question: the mechanism says how the pair was found, not what
    # the model is asked about it.
    linked = dict.fromkeys((link.section, link.chunk) for link in index.links)
    return [
        Suspect(section=sections[section], change=change)
        for change in touched_chunks(root, base, head)
        for section, chunk in linked
        if chunk == change.chunk and section in sections
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


_UNREPAIRED = "the run reached its spend ceiling before this finding could be repaired"


def _resolve(
    model: ModelClient, drifted: list[tuple[Suspect, Finding]], threshold: float
) -> tuple[tuple[Repair, ...], tuple[Flag, ...]]:
    """Resolve every finding to a repair or a flag.

    Only after every suspect is verified. Under a ceiling, a finding reported
    as a flag is worth more than a repair to one finding and silence about the
    rest, so verification is paid for first.

    A finding outside the gate is still repaired and validated, so that the
    flag can show a reviewer a rewrite to start from. The model is never asked
    its confidence in it, because no answer could make it a repair. A ceiling
    reached at validation or at the confidence pass throws away edits already paid for. Keeping them would mean
    proposing, or showing, a rewrite nothing has checked.
    """
    repairs: list[Repair] = []
    flags: list[Flag] = []
    for position, (suspect, finding) in enumerate(drifted):
        try:
            resolved = _settle(model, suspect, finding, threshold)
        except SpendCeilingExceeded:
            flags += [
                Flag(finding, _UNREPAIRED, suspect.section.text, None, cause="ceiling")
                for suspect, finding in drifted[position:]
            ]
            break
        if isinstance(resolved, Repair):
            repairs.append(resolved)
        else:
            flags.append(resolved)
    return tuple(repairs), tuple(flags)


def _settle(
    model: ModelClient, suspect: Suspect, finding: Finding, threshold: float
) -> Repair | Flag:
    """One finding through repair, validation, the gate and the threshold, in that order."""
    section, change = suspect.section, suspect.change
    shape = shape_of(change)
    outcome = repair(model, section, change, finding.explanation)
    if isinstance(outcome, Rejected):
        return Flag(
            finding=finding,
            reason=outcome.reason,
            original=section.text,
            attempt=outcome.attempt,
            cause="unappliable" if outcome.attempt is None else "refused",
            shape=shape.name if shape else None,
        )
    if shape is None:
        return Flag(
            finding=finding,
            reason=outside_reason(change),
            original=section.text,
            attempt=outcome,
            cause="outside",
        )
    confidence = rate(model, section, change, finding.explanation, outcome)
    if confidence < threshold:
        return Flag(
            finding=finding,
            reason=(
                f"the model is {confidence:.0%} confident in this {shape.description} "
                f"repair, short of the {threshold:.0%} it takes to propose one "
                "without a human"
            ),
            original=section.text,
            attempt=outcome,
            cause="doubted",
            shape=shape.name,
            confidence=confidence,
        )
    return Repair(finding, section.text, outcome, shape.name, confidence)


def _question(suspect: Suspect) -> str:
    section, change = suspect.section, suspect.change
    return (
        f"Documentation section {section.id}:\n\n{section.text}\n\n"
        f"The chunk {change.chunk} before the change:\n\n{change.before}\n\n"
        f"And after it:\n\n{change.after}"
    )
