"""The fixture corpus: planted drift, decoys, and the ground truth over both."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from synclint.analyse import disappearances, suspects
from synclint.changes import chunk_diff, is_test_file
from synclint.chunks import extract_chunks
from synclint.git import file_at, run
from synclint.index import Index, build_index

BASE_REF = "base"

KINDS = (
    "renamed-parameter",
    "changed-default",
    "removed-capability",
    "contradicted-claim",
    "deleted-chunk",
)


@dataclass(frozen=True)
class DecoyKind:
    """What a decoy of one kind promises about its own commit.

    A kind is a claim about the change, and the audit holds the decoy to it. A
    `formatting` decoy that really changes behaviour, or an `internal-refactor`
    that really changes nothing, is mislabelled — and a false positive rate
    measured over either would mean nothing.
    """

    changes_chunks: bool
    test_files_only: bool = False
    moves_chunks: bool = False


DECOY_KINDS = {
    "internal-refactor": DecoyKind(changes_chunks=True),
    "added-parameter": DecoyKind(changes_chunks=True),
    "comment-edit": DecoyKind(changes_chunks=False),
    "test-only": DecoyKind(changes_chunks=False, test_files_only=True),
    "formatting": DecoyKind(changes_chunks=False),
    "moved-chunk": DecoyKind(changes_chunks=False, moves_chunks=True),
}


class CorpusError(Exception):
    """The corpus on disk is not something that can be built."""


@dataclass(frozen=True)
class Case:
    """One planted drift case: a code change, and the section it should invalidate.

    Ground truth, written by hand and independent of what synclint can currently
    detect. A case nothing reaches is a gap in the tool, not an error in the case.

    `repair_says` and `repair_drops` are the ground truth for a repair of the
    section: text it must contain, and text it must no longer contain. Matched
    exactly, case and all, so each is written in the section's own voice — a
    page that writes "fourteen days" is not correctly repaired to "7 days".
    """

    id: str
    kind: str
    section: str
    chunk: str
    description: str
    repair_says: tuple[str, ...]
    repair_drops: tuple[str, ...]

    def accepts(self, repaired: str) -> bool:
        """Whether a repaired section says what the change made true, and no longer what it made false."""
        return all(text in repaired for text in self.repair_says) and not any(
            text in repaired for text in self.repair_drops
        )


@dataclass(frozen=True)
class Decoy:
    """One change to the fixture that must produce no finding.

    A decoy carries no section and no chunk because there is nothing to expect:
    what it asserts is an absence. Its kind says what sort of change it is, and
    the audit holds it to that.
    """

    id: str
    kind: str
    description: str


@dataclass(frozen=True)
class Manifest:
    """The ground truth as written down: what should be found, and what should not."""

    cases: tuple[Case, ...]
    decoys: tuple[Decoy, ...]


@dataclass(frozen=True)
class Corpus:
    """A built corpus: where it was written down, where it was built, and what is in it."""

    source: Path
    root: Path
    cases: tuple[Case, ...]
    decoys: tuple[Decoy, ...]


@dataclass(frozen=True)
class Audit:
    """What auditing a built corpus against its manifest found.

    `faults` are wrong things about the corpus itself and must be empty.
    `reachable` and `unreachable` measure synclint rather than the corpus: a
    case is reachable when its expected section and chunk meet as a suspect,
    or as a disappearance where the case deletes the chunk, which is the most
    a run can get right before the model is asked anything.
    A case with a fault appears in neither, so unreachable means one thing only
    — drift correctly recorded that synclint cannot yet get to.

    `suspected` names the decoys that reach the model, or raise a disappearance,
    where a false positive is still possible. A decoy missing from it raised no
    suspect at all and so cannot produce a finding whatever a model would have
    said about it.

    The cases and decoys are carried along so that a report can be rendered from
    this alone, the way `analyse.Report` carries what `render` needs.
    """

    cases: tuple[Case, ...]
    decoys: tuple[Decoy, ...]
    faults: tuple[str, ...]
    reachable: tuple[str, ...]
    unreachable: tuple[str, ...]
    suspected: tuple[str, ...]


def case_ref(case_id: str) -> str:
    """The branch holding one case's commit."""
    return f"case/{case_id}"


def decoy_ref(decoy_id: str) -> str:
    """The branch holding one decoy's commit."""
    return f"decoy/{decoy_id}"


def build_corpus(source: Path, destination: Path) -> Corpus:
    """Materialise the corpus described by `source` as a git repository at `destination`.

    `source` holds `manifest.toml`, a `base/` tree, and an overlay per entry —
    `cases/<id>/` or `decoys/<id>/` — naming the files it rewrites. The build
    commits `base`, then commits each overlay on top of it as its own branch, so
    every case and every decoy is a diff against the same base, one index serves
    them all, and a decoy carries nothing of the cases.

    The working tree is left at `base`. `destination` must not already exist.
    """
    manifest = read_manifest(source / "manifest.toml")
    if destination.exists():
        raise CorpusError(f"{destination} already exists")

    _copy(source / "base", destination)
    _git(destination, "init", "-q", "-b", BASE_REF)
    _commit(destination, "the project as it stands")

    for case in manifest.cases:
        _commit_overlay(
            source / "cases" / case.id,
            destination,
            case_ref(case.id),
            case.description,
            f"case {case.id}",
        )
    for decoy in manifest.decoys:
        _commit_overlay(
            source / "decoys" / decoy.id,
            destination,
            decoy_ref(decoy.id),
            decoy.description,
            f"decoy {decoy.id}",
        )

    _git(destination, "checkout", "-q", BASE_REF)
    return Corpus(
        source=source, root=destination, cases=manifest.cases, decoys=manifest.decoys
    )


def _commit_overlay(
    overlay: Path, destination: Path, ref: str, message: str, subject: str
) -> None:
    if not overlay.is_dir():
        raise CorpusError(f"{subject} has no overlay at {overlay}")
    _git(destination, "checkout", "-q", "-B", ref, BASE_REF)
    _copy(overlay, destination, dirs_exist_ok=True)
    _git(destination, "add", "-A")
    if _git(destination, "diff", "--cached", "--name-only").strip() == "":
        # An overlay that changes nothing would still commit cleanly on a
        # `--allow-empty`, and then silently measure nothing for ever.
        raise CorpusError(f"{subject} changes nothing against the base")
    _commit(destination, message)


def _copy(source: Path, destination: Path, dirs_exist_ok: bool = False) -> None:
    # Bytecode is left behind by anyone who imports the fixture and is not part
    # of it. Copying it would commit whatever happens to be on the developer's
    # disk, and a decoy is audited on the paths its commit touches.
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__"),
        dirs_exist_ok=dirs_exist_ok,
    )


def audit_corpus(corpus: Corpus) -> Audit:
    """Audit a built corpus against its manifest, and measure what synclint reaches."""
    index = build_index(corpus.root)
    faults: list[str] = []
    reachable: list[str] = []
    unreachable: list[str] = []
    for case in corpus.cases:
        found = _faults(corpus, case, index)
        if found:
            # A case known to be wrong is not measured. Reachability is a
            # statement about synclint, and a faulty case cannot make one.
            faults.extend(found)
            continue
        ref = case_ref(case.id)
        pairs = {
            (suspect.section.id, suspect.change.chunk)
            for suspect in suspects(corpus.root, index, BASE_REF, ref)
        } | {
            (flag.finding.section, flag.finding.chunk)
            for flag in disappearances(corpus.root, index, BASE_REF, ref)
        }
        reached = reachable if (case.section, case.chunk) in pairs else unreachable
        reached.append(case.id)

    suspected: list[str] = []
    for decoy in corpus.decoys:
        found = _decoy_faults(corpus, decoy)
        if found:
            faults.extend(found)
            continue
        # A disappearance never reaches the model, but it is a finding all the
        # same, and so a false positive a decoy could cause.
        ref = decoy_ref(decoy.id)
        if suspects(corpus.root, index, BASE_REF, ref) or disappearances(
            corpus.root, index, BASE_REF, ref
        ):
            suspected.append(decoy.id)

    faults.extend(_unclaimed(corpus))
    return Audit(
        cases=corpus.cases,
        decoys=corpus.decoys,
        faults=tuple(faults),
        reachable=tuple(reachable),
        unreachable=tuple(unreachable),
        suspected=tuple(suspected),
    )


def _decoy_faults(corpus: Corpus, decoy: Decoy) -> list[str]:
    """The ways the decoy's commit is not the kind of change the manifest calls it.

    Only as far as a chunk goes, which is as far as synclint itself looks: a
    decoy that rewrote a module-level statement, or a refactor that quietly
    changed what the code does, would clear every check here. Those rest on the
    fixture's own tests and on reading the diff.
    """
    if decoy.kind not in DECOY_KINDS:
        return [
            f"{decoy.id}: kind {decoy.kind} is not one of {', '.join(DECOY_KINDS)}"
        ]
    promise = DECOY_KINDS[decoy.kind]
    ref = decoy_ref(decoy.id)
    paths = _changed_paths(corpus.root, ref)
    diff = chunk_diff(corpus.root, BASE_REF, ref)
    # A chunk deleted is as much a change to it as a chunk rewritten.
    touched = sorted(
        [change.chunk for change in diff.changed]
        + [chunk.chunk for chunk in diff.vanished]
    )
    faults = []
    if promise.changes_chunks and not touched:
        faults.append(
            f"{decoy.id}: kind {decoy.kind} promises a changed chunk, "
            "and this commit changes none"
        )
    if not promise.changes_chunks and touched:
        faults.append(
            f"{decoy.id}: kind {decoy.kind} promises no changed chunk, "
            f"and this commit changes {', '.join(touched)}"
        )
    if promise.moves_chunks and not diff.moved:
        faults.append(
            f"{decoy.id}: kind {decoy.kind} promises a chunk moved to another "
            "file, and this commit moves none"
        )
    # `chunk_diff` drops test files whole, so a test-only decoy would clear
    # the chunk check even if it rewrote half the library alongside. What makes
    # it test-only is where it lands, and that is worth saying separately.
    if promise.test_files_only:
        source = [path for path in paths if not is_test_file(path)]
        if source:
            faults.append(
                f"{decoy.id}: kind {decoy.kind} promises a change to test files "
                f"only, and this commit changes {', '.join(source)}"
            )
    # A decoy that updated the prose alongside the code would be the most
    # realistic one of all, and it cannot be measured here: the index is built
    # at the base, so the section put to the model would be the stale one and
    # the false positive would be the harness's rather than the model's.
    # TODO: #11 settles which revision the index is built at. A decoy that edits
    # documentation can become a measurement rather than a fault once it has.
    documentation = [path for path in paths if path.endswith(".md")]
    if documentation:
        faults.append(
            f"{decoy.id}: edits documentation ({', '.join(documentation)}); "
            "a decoy changes code and leaves the prose alone"
        )
    return faults


def _faults(corpus: Corpus, case: Case, index: Index) -> list[str]:
    # Any markdown, rather than only what the documentation glob matched: a
    # case that adds a doc file is making the same mistake as one that edits
    # an indexed one, and the glob would not have caught it.
    documentation = [
        path
        for path in _changed_paths(corpus.root, case_ref(case.id))
        if path.endswith(".md")
    ]
    faults = []
    if case.kind not in KINDS:
        faults.append(f"{case.id}: kind {case.kind} is not one of {', '.join(KINDS)}")
    if documentation:
        faults.append(
            f"{case.id}: edits documentation ({', '.join(documentation)}); "
            "a planted case changes code and leaves the prose stale"
        )
    faults.extend(_chunk_faults(corpus.root, case))
    sections = {section.id: section for section in index.sections}
    if case.section not in sections:
        faults.append(f"{case.id}: the base index has no section {case.section}")
    elif case.accepts(sections[case.section].text):
        faults.append(
            f"{case.id}: the section as it stands already meets its repair "
            "ground truth, so a repair that changed nothing would be judged correct"
        )
    return faults


def _chunk_faults(root: Path, case: Case) -> list[str]:
    """Whether the case's commit really does what the manifest says it does.

    Without this a manifest naming the wrong chunk audits clean and reports as
    unreachable, which is the one thing unreachable must never mean: the whole
    point of the split is that unreachable is a gap in synclint and a fault is
    a mistake in the corpus.
    """
    ref = case_ref(case.id)
    # At either revision, because a case that adds a capability names a chunk
    # that exists only once it is applied, and one that deletes a chunk names
    # one that exists only before.
    if not _defines(root, BASE_REF, case.chunk):
        if not _defines(root, ref, case.chunk):
            return [f"{case.id}: no chunk {case.chunk} at {BASE_REF} or {ref}"]
        # Added by the case. `chunk_diff` compares only chunks that were there
        # before, so there is nothing to compare and nothing to check.
        return []
    diff = chunk_diff(root, BASE_REF, ref)
    touched = {change.chunk for change in diff.changed}
    touched |= {chunk.chunk for chunk in diff.vanished}
    if case.chunk not in touched:
        return [f"{case.id}: the commit at {ref} does not change {case.chunk}"]
    return []


def _unclaimed(corpus: Corpus) -> list[str]:
    return _unclaimed_in(
        corpus.source / "cases", {case.id for case in corpus.cases}, "case"
    ) + _unclaimed_in(
        corpus.source / "decoys", {decoy.id for decoy in corpus.decoys}, "decoy"
    )


def _unclaimed_in(directory: Path, claimed: set[str], noun: str) -> list[str]:
    if not directory.is_dir():
        return []
    return [
        f"{overlay.name}: an overlay with no {noun} in the manifest"
        for overlay in sorted(directory.iterdir())
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


def _changed_paths(root: Path, ref: str) -> list[str]:
    listing = _git(root, "diff", "--name-only", BASE_REF, ref)
    return [line for line in listing.splitlines() if line]


def read_manifest(path: Path) -> Manifest:
    """Read the ground truth, in the order the cases and decoys are written down."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    return Manifest(
        cases=tuple(_case(entry) for entry in document.get("case", [])),
        decoys=tuple(_decoy(entry) for entry in document.get("decoy", [])),
    )


def _case(entry: dict[str, object]) -> Case:
    _require(
        entry,
        {"id", "kind", "section", "chunk", "description", "repair_says", "repair_drops"},
        "case",
    )
    return Case(
        id=str(entry["id"]),
        kind=str(entry["kind"]),
        section=str(entry["section"]),
        chunk=str(entry["chunk"]),
        description=str(entry["description"]),
        repair_says=_strings(entry, "repair_says"),
        repair_drops=_strings(entry, "repair_drops"),
    )


def _strings(entry: dict[str, object], field: str) -> tuple[str, ...]:
    value = entry[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CorpusError(f"case {entry['id']}: {field} is not a list of strings")
    return tuple(value)


def _decoy(entry: dict[str, object]) -> Decoy:
    _require(entry, {"id", "kind", "description"}, "decoy")
    return Decoy(
        id=str(entry["id"]),
        kind=str(entry["kind"]),
        description=str(entry["description"]),
    )


def _require(entry: dict[str, object], fields: set[str], noun: str) -> None:
    missing = sorted(fields - set(entry))
    if missing:
        raise CorpusError(
            f"{noun} {entry.get('id', '?')} is missing {', '.join(missing)}"
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
    # Signing is off because a developer who signs every commit cannot otherwise
    # build a corpus in a temporary directory.
    return run(
        root, "-c", "commit.gpgsign=false", *arguments, env={**os.environ, **_IDENTITY}
    )
