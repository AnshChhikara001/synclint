"""Which chunks a code change actually touched, and how."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath

from synclint.chunks import chunk_id, walk_definitions


@dataclass(frozen=True)
class ChunkChange:
    """One chunk, as it read before a change and as it reads after.

    Both sides carry the chunk's full source, bodies included — the one place
    synclint needs them, and only for the chunks a change actually touched.
    """

    path: str
    qualname: str
    before: str
    after: str

    @property
    def chunk(self) -> str:
        return chunk_id(self.path, self.qualname)


def changed_chunks(before: str, after: str, path: str) -> list[ChunkChange]:
    """Report the chunks of one Python file that differ between two revisions.

    A change to a method is also a change to the class holding it, so both are
    reported. A section describing the class may well describe the behaviour
    that moved, and missing that is worse than checking it twice.

    Chunks added or removed between the revisions are not reported. A section
    describing a chunk that no longer exists is a finding in its own right and
    needs git's rename detection to tell a deletion from a move; that is #10.
    """
    was = _definitions(before)
    now = _definitions(after)
    return [
        ChunkChange(path=path, qualname=qualname, before=was[qualname], after=source)
        for qualname, source in now.items()
        if qualname in was and was[qualname] != source
    ]


def _definitions(source: str) -> dict[str, str]:
    # Unparsed from the tree rather than sliced from the file, so that the
    # comparison sees through both reformatting and comment edits: neither
    # survives a parse, so neither can produce a change.
    tree = ast.parse(source)
    _strip_docstrings(tree)
    return {
        qualname: ast.unparse(node) for qualname, node in walk_definitions(tree.body)
    }


_DOCUMENTED = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _strip_docstrings(tree: ast.Module) -> None:
    """Remove every docstring in place, so that rewording one is not a code change.

    A docstring is the one part of a chunk that is documentation rather than
    behaviour, and per ADR-0004 synclint does not check it. Leaving it in would
    make a documentation-polish commit look like a change to every chunk it
    touched. A docstring reworded alongside a real change is still caught,
    because then the rest of the body differs too.
    """
    for node in ast.walk(tree):
        if isinstance(node, _DOCUMENTED) and ast.get_docstring(node) is not None:
            # `ast.unparse` cannot render a definition with an empty body.
            node.body = node.body[1:] or [ast.Pass()]


# `conftest.py` is not named for a test but only tests import it. The rest is
# pytest's own discovery convention, which is what these repositories follow.
# TODO: a repository using some other convention has no way to say so yet.
def is_test_file(path: str) -> bool:
    """Whether a path is test code, a change to which cannot reach documentation."""
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return (
        name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or any(part in ("test", "tests") for part in parts[:-1])
    )
