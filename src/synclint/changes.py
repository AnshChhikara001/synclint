"""Which chunks a code change actually touched, and how."""

from __future__ import annotations

import ast
import copy
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from synclint.chunks import Definition, chunk_id, walk_definitions
from synclint.git import changed_python_files, file_at


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


@dataclass(frozen=True)
class VanishedChunk:
    """A chunk that existed before a change and, moves followed, exists nowhere after it."""

    path: str
    qualname: str
    before: str

    @property
    def chunk(self) -> str:
        return chunk_id(self.path, self.qualname)


@dataclass(frozen=True)
class ChunkDiff:
    """What a change did to the chunks it touched.

    `changed` holds the chunks that read differently afterwards, each named by
    its path before the change, which is the id the index links it under.
    `vanished` holds the chunks that are gone. `moved` pairs each chunk that was
    followed to another file with where it went, whether or not it changed.
    """

    changed: tuple[ChunkChange, ...]
    vanished: tuple[VanishedChunk, ...]
    moved: tuple[tuple[str, str], ...]


def chunk_diff(root: Path, base: str, head: str) -> ChunkDiff:
    """What the change from `base` to `head` did to the chunks in `root`.

    Test files are dropped whole: nothing in them can reach documentation.
    """
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    renamed: dict[str, str] = {}
    for file in changed_python_files(root, base, head):
        if file.before is not None and not is_test_file(file.before):
            before[file.before] = file_at(root, base, file.before)
        if file.after is not None and not is_test_file(file.after):
            after[file.after] = file_at(root, head, file.after)
        if file.before is not None and file.after not in (None, file.before):
            renamed[file.before] = file.after
    return compare(before, after, renamed)


def touched_chunks(root: Path, base: str, head: str) -> list[ChunkChange]:
    """Every chunk in `root` that the change from `base` to `head` changed, moves followed."""
    return list(chunk_diff(root, base, head).changed)


def compare(
    before: Mapping[str, str],
    after: Mapping[str, str],
    renamed: Mapping[str, str] | None = None,
) -> ChunkDiff:
    """Compare the chunks of the files a change touched, by path, at two revisions.

    `before` and `after` map a path to its source; a path on one side only was
    added or deleted. `renamed` pairs a file's path before with its path after,
    as git's rename detection judged it.

    A class is compared on its own source only — its bases, decorators and
    class-level statements — never on the methods inside it. Those are chunks
    in their own right and are compared separately. Carrying them would report
    a one-line method change twice, once as the method and once as a class
    whose before and after held every untouched sibling's body as well.

    A chunk missing from its own file afterwards is looked for in the files the
    change added to, and followed there when exactly one chunk of its qualified
    name appeared and no other chunk of that name went missing. Anything less
    certain than that is reported vanished: a false disappearance is a flag a
    human dismisses, and a false move would compare a chunk with a stranger.
    A move into or out of a class, or a rename, changes the qualified name, so
    neither is followed.
    """
    renamed = renamed or {}
    partner = {path: renamed.get(path, path) for path in before}
    was: dict[tuple[str, str], str] = {}
    now: dict[tuple[str, str], str] = {}
    for path, source in before.items():
        destination = partner[path]
        try:
            old = _definitions(source)
            new = _definitions(after[destination]) if destination in after else {}
        except SyntaxError:
            # `build_index` skips the file this interpreter cannot parse rather
            # than lose the whole index. A run has the same stake in one broken
            # file, and a larger one: it is checking a change, not a snapshot.
            # Both sides go, or every chunk in it would read as vanished.
            continue
        was.update(((path, qualname), text) for qualname, text in old.items())
        now.update(((destination, qualname), text) for qualname, text in new.items())
    paired = set(partner.values())
    for path, source in after.items():
        if path in paired:
            continue
        try:
            new = _definitions(source)
        except SyntaxError:
            continue
        now.update(((path, qualname), text) for qualname, text in new.items())

    missing = [key for key in was if (partner[key[0]], key[1]) not in now]
    claimed = {(partner[path], qualname) for path, qualname in was}
    appeared = [key for key in now if key not in claimed]
    missing_names = Counter(qualname for _, qualname in missing)
    appeared_by_name: dict[str, list[tuple[str, str]]] = {}
    for key in appeared:
        appeared_by_name.setdefault(key[1], []).append(key)

    changed: list[ChunkChange] = []
    vanished: list[VanishedChunk] = []
    moved: list[tuple[str, str]] = []
    for (path, qualname), source in was.items():
        target = (partner[path], qualname)
        if target not in now:
            candidates = appeared_by_name.get(qualname, [])
            if missing_names[qualname] != 1 or len(candidates) != 1:
                vanished.append(VanishedChunk(path, qualname, source))
                continue
            target = candidates[0]
        if target[0] != path:
            moved.append((chunk_id(path, qualname), chunk_id(*target)))
        if now[target] != source:
            changed.append(ChunkChange(path, qualname, source, now[target]))
    return ChunkDiff(tuple(changed), tuple(vanished), tuple(moved))


def _definitions(source: str) -> dict[str, str]:
    # Unparsed from the tree rather than sliced from the file, so that the
    # comparison sees through both reformatting and comment edits: neither
    # survives a parse, so neither can produce a change.
    tree = ast.parse(source)
    _strip_docstrings(tree)
    return {
        qualname: _own_source(node)
        for qualname, node in walk_definitions(tree.body)
    }


def _own_source(node: Definition) -> str:
    """The source of one definition, without the definitions nested inside it.

    Only classes have any stripped. A function-local definition is part of what
    the function does and nothing else indexes it, so it stays.
    """
    if not isinstance(node, ast.ClassDef):
        return ast.unparse(node)
    own = copy.copy(node)
    # `ast.unparse` cannot render a class with an empty body.
    own.body = [
        statement for statement in node.body if not isinstance(statement, Definition)
    ] or [ast.Pass()]
    return ast.unparse(own)


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
