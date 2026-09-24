"""Repair and validation: a finding rewritten into its section, and the gate it has to pass."""

from __future__ import annotations

import json
from dataclasses import dataclass

from synclint.changes import ChunkChange
from synclint.model import ModelClient
from synclint.sections import Section


@dataclass(frozen=True)
class Rejected:
    """A repair that cannot be proposed, and why.

    `attempt` is the section as the repair would have left it, kept so that a
    human reading the flag can see what was tried. It is `None` when the edits
    the model asked for could not be applied, so there is nothing to show.
    """

    reason: str
    attempt: str | None


@dataclass(frozen=True)
class _Edit:
    find: str
    replace: str


class _Unappliable(Exception):
    pass


# The model is asked for edits rather than for the rewritten section, and the
# edits are applied here. A model asked to return a whole section re-wraps
# lines, straightens quotes and tidies what it was not asked to touch, and no
# prompt reliably stops it. Applying quoted spans makes "the untouched prose
# stays byte-identical" true by construction rather than by instruction.
_REPAIR = """\
You repair documentation that a code change has made inaccurate.

You are given one section of a project's documentation, the chunk of code it
describes as it read before a change and as it reads after, and what the change
has made wrong. Rewrite only the words that are now wrong. Everything the
section says that is still true stays exactly as it is.

Answer with edits. Each edit quotes a span of the section exactly as it is
written, line breaks included, and gives the text to put in its place. Quote
only the words that are wrong, and no more of the words around them than it
takes for the span to appear once in the section; to remove a sentence, quote
that sentence. Never quote the whole section: an edit that replaces all of it is
a rewrite, and is refused. Write replacements in the section's own voice: its
tense, its person, and how it writes numbers and code. Add no headings, lists or
examples the section did not already have."""

_REPAIR_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "find": {
                        "type": "string",
                        "description": "A span of the section, quoted exactly.",
                    },
                    "replace": {
                        "type": "string",
                        "description": "The text to put in its place.",
                    },
                },
                "required": ["find", "replace"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["edits"],
    "additionalProperties": False,
}

_VALIDATE = """\
You check a proposed repair to a section of documentation before anyone reads it.

You are given the section as it stands, the chunk of code it describes as it
read before a change and as it reads after, what the change made wrong, and the
section as the repair would leave it. Pass the repair only if all three hold:
it describes the code as it reads after the change; everything the section said
that is still true survives it; and it reads as though the section's own author
wrote it.

If you reject it, say in one sentence why. If you pass it, leave the reason
empty."""

_VALIDATE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "valid": {
            "type": "boolean",
            "description": "Whether the repair can be proposed as it stands.",
        },
        "reason": {
            "type": "string",
            "description": "Why it cannot, in one sentence. Empty if valid.",
        },
    },
    "required": ["valid", "reason"],
    "additionalProperties": False,
}


def repair(
    model: ModelClient, section: Section, change: ChunkChange, explanation: str
) -> str | Rejected:
    """Rewrite the drifted parts of `section`, and return it if validation passes it.

    Two questions to the model: one for the edits, one to validate the section
    they produce. Validation is a gate on the repair rather than a second
    opinion on the finding, so a rejection says the rewrite is not safe to
    propose, never that the section had not drifted.

    Raises `SpendCeilingExceeded` rather than make a call past the ceiling.
    """
    question = _question(section, change, explanation)
    answer = json.loads(model.complete(_REPAIR, question, _REPAIR_SCHEMA))
    edits = [_Edit(edit["find"], edit["replace"]) for edit in answer["edits"]]
    try:
        repaired = _apply(section.text, edits)
    except _Unappliable as error:
        return Rejected(reason=str(error), attempt=None)

    verdict = json.loads(
        model.complete(
            _VALIDATE,
            question + f"\n\nThe section as the repair would leave it:\n\n{repaired}",
            _VALIDATE_SCHEMA,
        )
    )
    if verdict["valid"]:
        return repaired
    return Rejected(reason=verdict["reason"], attempt=repaired)


def _apply(text: str, edits: list[_Edit]) -> str:
    """Apply quoted edits to `text`, leaving every character outside them alone.

    Refuses rather than guesses. A quote the section does not contain, or
    contains twice, has no one place to go, and two edits over the same words
    have no order to go in.
    """
    spans: list[tuple[int, int, str]] = []
    for edit in edits:
        if not edit.find:
            raise _Unappliable("the repair quoted nothing to replace")
        # The first recording quoted the whole section in nine repairs of ten,
        # which leaves nothing outside the edit for byte-identity to protect.
        # Compared stripped, because two of those quotes carried a newline the
        # section does not end with.
        if edit.find.strip() == text.strip():
            raise _Unappliable("the repair quoted the whole section, which is a rewrite")
        start = text.find(edit.find)
        if start < 0:
            raise _Unappliable(
                f"the repair quoted text the section does not contain: {edit.find!r}"
            )
        if text.find(edit.find, start + 1) >= 0:
            raise _Unappliable(
                f"the repair quoted text the section contains more than once: {edit.find!r}"
            )
        spans.append((start, start + len(edit.find), edit.replace))
    spans.sort()
    for (_, end, _), (start, _, _) in zip(spans, spans[1:]):
        if start < end:
            raise _Unappliable("the repair made two edits to the same words")

    repaired = text
    # Back to front, so that each edit leaves the offsets before it valid.
    for start, end, replacement in reversed(spans):
        repaired = repaired[:start] + replacement + repaired[end:]
    if repaired == text:
        raise _Unappliable("the repair changed nothing")
    return repaired


def _question(section: Section, change: ChunkChange, explanation: str) -> str:
    return (
        f"Documentation section {section.id}:\n\n{section.text}\n\n"
        f"The chunk {change.chunk} before the change:\n\n{change.before}\n\n"
        f"And after it:\n\n{change.after}\n\n"
        f"What the change made wrong: {explanation}"
    )
