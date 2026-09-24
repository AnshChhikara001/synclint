"""Confidence: which repairs may be proposed without a human reading the original first.

Per ADR-0003 the model does not decide this on its own. Deterministic rules
decide which shapes of change are eligible at all, and the model's score is
consulted only inside that gate. A change of any other shape is flagged however
certain the model is. Widening the gate means adding a shape to `SHAPES`, never
lowering the threshold.
"""

from __future__ import annotations

import ast
import copy
import json
from collections.abc import Callable
from dataclasses import dataclass

from synclint.changes import ChunkChange
from synclint.model import ModelClient
from synclint.repair import question
from synclint.sections import Section

# Set before any score was recorded, so the corpus could not have picked it.
# A repair inside the gate is still the one action synclint takes that is hard
# to undo, so the model has to claim nine chances in ten of it being exactly
# right. The calibration table in the score is what says whether that holds.
DEFAULT_THRESHOLD = 0.9

_Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class Shape:
    """One narrow, well-understood kind of change that is eligible for automatic repair."""

    name: str
    description: str
    matches: Callable[[_Function, _Function], bool]


def _renamed_parameter(before: _Function, after: _Function) -> bool:
    was, now = _parameters(before), _parameters(after)
    if len(was) != len(now):
        return False
    renamed = [(old, new) for old, new in zip(was, now) if old != new]
    if len(renamed) != 1:
        return False
    ((old, new),) = renamed
    # A body that already used the new name would have two variables folded
    # into one by the rename, and the comparison below could not tell.
    if new in _names(before):
        return False
    return ast.dump(_rename(before, old, new)) == ast.dump(after)


def _changed_default(before: _Function, after: _Function) -> bool:
    was, now = _defaults(before), _defaults(after)
    # Adding a default to a parameter that had none, or dropping one, makes a
    # call that used to fail succeed or the reverse. That is not this shape.
    if [default is None for default in was] != [default is None for default in now]:
        return False
    changed = sum(
        1
        for old, new in zip(was, now)
        if old is not None and new is not None and ast.dump(old) != ast.dump(new)
    )
    if changed != 1:
        return False
    aligned = copy.deepcopy(before)
    aligned.args.defaults = after.args.defaults
    aligned.args.kw_defaults = after.args.kw_defaults
    return ast.dump(aligned) == ast.dump(after)


SHAPES: tuple[Shape, ...] = (
    Shape(
        "renamed-parameter",
        "renamed parameter",
        _renamed_parameter,
    ),
    Shape(
        "changed-default",
        "changed default",
        _changed_default,
    ),
)


def shape_of(change: ChunkChange) -> Shape | None:
    """The eligible shape `change` has, or `None` if it has none of them."""
    before, after = _function(change.before), _function(change.after)
    if before is None or after is None:
        return None
    return next((shape for shape in SHAPES if shape.matches(before, after)), None)


def outside(change: ChunkChange) -> str:
    """Why `change` is outside the gate, in words a reviewer can check against the diff."""
    eligible = " or ".join(f"a {shape.description}" for shape in SHAPES)
    before, after = _function(change.before), _function(change.after)
    if before is None or after is None:
        touched = f"{change.chunk} is a class"
    else:
        touched = f"it touches the {_touched(before, after)}"
    return (
        f"only {eligible} is repaired without a human reading the original "
        f"first, and this change is neither: {touched}"
    )


_RATE = """\
You judge whether a repair to documentation is safe to propose without a human
reading the original section first.

You are given the section as it stands, the chunk of code it describes as it
read before a change and as it reads after, what the change made wrong, and the
section as the repair would leave it. Say how likely it is, from 0 to 1, that
the repaired section is exactly right: it describes the code as it now reads,
it keeps everything the section said that is still true, and it needs no
further edit before it is merged."""

_RATE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "confidence": {
            "type": "number",
            "description": "How likely the repair is exactly right, from 0 to 1.",
        },
    },
    "required": ["confidence"],
    "additionalProperties": False,
}


def rate(
    model: ModelClient,
    section: Section,
    change: ChunkChange,
    explanation: str,
    repaired: str,
) -> float:
    """The model's score for a repair, which only a repair inside the gate is ever given.

    Raises `SpendCeilingExceeded` rather than make a call past the ceiling.
    """
    asked = question(section, change, explanation)
    answer = json.loads(
        model.complete(
            _RATE,
            asked + f"\n\nThe section as the repair would leave it:\n\n{repaired}",
            _RATE_SCHEMA,
        )
    )
    return float(answer["confidence"])


def _touched(before: _Function, after: _Function) -> str:
    parts = [
        part
        for part, was, now in (
            ("decorators", before.decorator_list, after.decorator_list),
            ("parameters", before.args, after.args),
            ("return annotation", before.returns, after.returns),
            ("body", before.body, after.body),
        )
        if _dump(was) != _dump(now)
    ]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _dump(node: ast.AST | list[ast.stmt] | list[ast.expr] | None) -> str:
    if node is None:
        return ""
    if isinstance(node, list):
        return "\n".join(ast.dump(each) for each in node)
    return ast.dump(node)


def _function(source: str) -> _Function | None:
    (node,) = ast.parse(source).body
    return node if isinstance(node, _Function) else None


def _parameters(function: _Function) -> list[str]:
    arguments = function.args
    every = [
        *arguments.posonlyargs,
        *arguments.args,
        *([arguments.vararg] if arguments.vararg else []),
        *arguments.kwonlyargs,
        *([arguments.kwarg] if arguments.kwarg else []),
    ]
    return [argument.arg for argument in every]


def _defaults(function: _Function) -> list[ast.expr | None]:
    """Every parameter's default in order, `None` where it has none."""
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    padding: list[ast.expr | None] = [None] * (len(positional) - len(arguments.defaults))
    return padding + list(arguments.defaults) + list(arguments.kw_defaults)


def _names(function: _Function) -> set[str]:
    return {
        node.id if isinstance(node, ast.Name) else node.arg
        for node in ast.walk(function)
        if isinstance(node, (ast.Name, ast.arg))
    }


def _rename(function: _Function, old: str, new: str) -> _Function:
    renamed = copy.deepcopy(function)
    for node in ast.walk(renamed):
        if isinstance(node, ast.Name) and node.id == old:
            node.id = new
        elif isinstance(node, ast.arg) and node.arg == old:
            node.arg = new
    return renamed
