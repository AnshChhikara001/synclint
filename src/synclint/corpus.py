"""The fixture corpus: planted drift, and the ground truth saying what should be found."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from synclint.analyse import suspects
from synclint.chunks import extract_chunks
from synclint.git import file_at
from synclint.index import Index, build_index

BASE_REF = "base"

KINDS = (
    "renamed-parameter",
    "changed-default",
    "removed-capability",
    "undocumented-feature",
)


class CorpusError(Exception):
    """The corpus on disk is not something that can be built."""


@dataclass(frozen=True)
class Case:
    """One planted drift case: a code change, and the section it should invalidate.

    Ground truth, written by hand and independent of what synclint can currently
    detect. A case nothing reaches is a gap in the tool, not an error in the case.
    """

    id: str
    kind: str
    section: str
    chunk: str
    description: str


@dataclass(frozen=True)
class Corpus:
    """A built corpus: where it was written down, where it was built, and its cases."""

    source: Path
    root: Path
    cases: tuple[Case, ...]


@dataclass(frozen=True)
class Validation:
    """What checking a built corpus against its manifest found.

    `problems` are faults in the corpus itself and must be empty. `reachable`
    and `unreachable` are a measurement of synclint rather than of the corpus:
    a case is reachable when its expected section and chunk meet as a suspect,
    which is the most a run can get right before the model is asked anything.
    """

    problems: tuple[str, ...]
    reachable: tuple[str, ...]
    unreachable: tuple[str, ...]


def case_ref(case_id: str) -> str:
    """The branch holding one case's commit."""
    return f"case/{case_id}"


def build_corpus(source: Path, destination: Path) -> Corpus:
    """Materialise the corpus described by `source` as a git repository at `destination`.

    `source` holds `manifest.toml`, a `base/` tree, and a `cases/<id>/` overlay
    per case naming the files that case rewrites. The build commits `base`, then
    commits each overlay on top of it as its own branch, so every case is a diff
    against the same base and one index serves them all.

    The working tree is left at `base`. `destination` must not already exist.
    """
    cases = read_manifest(source / "manifest.toml")
    if destination.exists():
        raise CorpusError(f"{destination} already exists")

    shutil.copytree(source / "base", destination)
    _git(destination, "init", "-q", "-b", BASE_REF)
    _commit(destination, "the project as it stands")

    for case in cases:
        overlay = source / "cases" / case.id
        if not overlay.is_dir():
            raise CorpusError(f"case {case.id} has no overlay at {overlay}")
        _git(destination, "checkout", "-q", "-B", case_ref(case.id), BASE_REF)
        shutil.copytree(overlay, destination, dirs_exist_ok=True)
        _git(destination, "add", "-A")
        if _git(destination, "diff", "--cached", "--name-only").strip() == "":
            # A case that changes nothing would still commit cleanly on a
            # `--allow-empty`, and then silently measure nothing for ever.
            raise CorpusError(f"case {case.id} changes nothing against the base")
        _commit(destination, case.description)

    _git(destination, "checkout", "-q", BASE_REF)
    return Corpus(source=source, root=destination, cases=cases)


def validate_corpus(corpus: Corpus) -> Validation:
    """Check a built corpus against its manifest, and measure what synclint reaches."""
    index = build_index(corpus.root)
    problems: list[str] = []
    reachable: list[str] = []
    unreachable: list[str] = []
    for case in corpus.cases:
        problems.extend(_problems(corpus, case, index))
        pairs = {
            (suspect.section.id, suspect.change.chunk)
            for suspect in suspects(corpus.root, index, BASE_REF, case_ref(case.id))
        }
        found = reachable if (case.section, case.chunk) in pairs else unreachable
        found.append(case.id)
    problems.extend(_unclaimed(corpus))
    return Validation(
        problems=tuple(problems),
        reachable=tuple(reachable),
        unreachable=tuple(unreachable),
    )


def _problems(corpus: Corpus, case: Case, index: Index) -> list[str]:
    # Any markdown, rather than only what the documentation glob matched: a
    # case that adds a doc file is making the same mistake as one that edits
    # an indexed one, and the glob would not have caught it.
    documentation = [
        path for path in _changed_paths(corpus.root, case) if path.endswith(".md")
    ]
    problems = []
    if case.kind not in KINDS:
        problems.append(
            f"{case.id}: kind {case.kind} is not one of {', '.join(KINDS)}"
        )
    if documentation:
        problems.append(
            f"{case.id}: edits documentation ({', '.join(documentation)}); "
            "a planted case changes code and leaves the prose stale"
        )
    # At the case's own revision rather than at the base: a case that adds a
    # capability names a chunk that only exists once the case is applied.
    if not _defines(corpus.root, case_ref(case.id), case.chunk):
        problems.append(
            f"{case.id}: no chunk {case.chunk} at {case_ref(case.id)}"
        )
    if case.section not in {section.id for section in index.sections}:
        problems.append(f"{case.id}: the base index has no section {case.section}")
    return problems


def _unclaimed(corpus: Corpus) -> list[str]:
    claimed = {case.id for case in corpus.cases}
    return [
        f"{overlay.name}: an overlay with no case in the manifest"
        for overlay in sorted((corpus.source / "cases").iterdir())
        if overlay.is_dir() and overlay.name not in claimed
    ]


def _defines(root: Path, revision: str, chunk: str) -> bool:
    path, _, _ = chunk.partition("::")
    try:
        chunks = extract_chunks(file_at(root, revision, path), path)
    except (subprocess.CalledProcessError, SyntaxError):
        # No such file at that revision, or one that does not parse. Either way
        # the manifest is pointing at something a run could never read.
        return False
    return any(defined.id == chunk for defined in chunks)


def _changed_paths(root: Path, case: Case) -> list[str]:
    listing = _git(root, "diff", "--name-only", BASE_REF, case_ref(case.id))
    return [line for line in listing.splitlines() if line]


def read_manifest(path: Path) -> tuple[Case, ...]:
    """Read the ground truth, in the order the cases are written down."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    return tuple(_case(entry) for entry in document.get("case", []))


def _case(entry: dict[str, object]) -> Case:
    missing = sorted({"id", "kind", "section", "chunk", "description"} - set(entry))
    if missing:
        raise CorpusError(f"case {entry.get('id', '?')} is missing {', '.join(missing)}")
    return Case(
        id=str(entry["id"]),
        kind=str(entry["kind"]),
        section=str(entry["section"]),
        chunk=str(entry["chunk"]),
        description=str(entry["description"]),
    )


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


# Identity and dates are fixed rather than taken from the developer's git
# config, so that the same corpus builds to the same commits anywhere.
_IDENTITY = {
    "GIT_AUTHOR_NAME": "synclint corpus",
    "GIT_AUTHOR_EMAIL": "corpus@synclint.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "synclint corpus",
    "GIT_COMMITTER_EMAIL": "corpus@synclint.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        # Signing is off because a developer who signs every commit cannot
        # otherwise build a corpus in a temporary directory.
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", *arguments],
        check=True,
        capture_output=True,
        env={**os.environ, **_IDENTITY},
    )
    return result.stdout.decode("utf-8", errors="replace")
