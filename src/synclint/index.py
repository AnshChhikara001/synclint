"""The index: every chunk, section and link in a repository at a point in time."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from synclint.chunks import Chunk, extract_chunks
from synclint.embeddings import EmbeddingClient
from synclint.git import extract, run
from synclint.links import Link, propose_embedding_links, propose_name_links
from synclint.sections import Section, split_sections

DEFAULT_DOCUMENTATION_GLOBS = ("README.md", "docs/**/*.md")

# Cosine similarity, measured on the fixture corpus with text-embedding-3-small:
# one section-chunk pair in twenty scores 0.556 or more, and this is that line
# rounded down. It was read off the distribution of every pair rather than off
# the planted cases, so the ground truth did not choose it — but it is still one
# corpus, and `score --threshold` is how a different value gets argued for.
DEFAULT_SIMILARITY_THRESHOLD = 0.55

# Where a repository commits its index, relative to its root. A dotted
# directory, like `.github`, because it is tooling state and not the project.
DEFAULT_INDEX_PATH = Path(".synclint/index.json")


@dataclass(frozen=True)
class Index:
    """What synclint believes about a repository.

    `revision` is the commit the index describes, `None` when nothing says
    which, and `documentation_globs` what it counted as documentation. Both
    exist so that an index committed to a repository can be checked against
    the revision a run needs before it is trusted.
    """

    chunks: tuple[Chunk, ...]
    sections: tuple[Section, ...]
    links: tuple[Link, ...]
    revision: str | None = None
    documentation_globs: tuple[str, ...] | None = None

    def to_json(self) -> str:
        """Render the index as the JSON that gets committed to the repository.

        Chunk and section ids are derived from the fields beside them. They are
        written out anyway so that a link can be grepped back to what it points
        at, and ignored when reading.
        """
        document = {
            "revision": self.revision,
            "documentation_globs": (
                None if self.documentation_globs is None else list(self.documentation_globs)
            ),
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "sections": [section.to_dict() for section in self.sections],
            "links": [link.to_dict() for link in self.links],
        }
        return json.dumps(document, indent=2) + "\n"

    @classmethod
    def from_json(cls, document: str) -> Index:
        """Read back an index rendered by `to_json`."""
        data = json.loads(document)
        # Read with `get`: an index written before #11 records neither, and
        # reads back as one that cannot be checked rather than as an error.
        globs = data.get("documentation_globs")
        return cls(
            chunks=tuple(Chunk.from_dict(chunk) for chunk in data["chunks"]),
            sections=tuple(Section.from_dict(section) for section in data["sections"]),
            links=tuple(Link.from_dict(link) for link in data["links"]),
            revision=data.get("revision"),
            documentation_globs=None if globs is None else tuple(globs),
        )


def build_index(
    root: Path,
    documentation_globs: Sequence[str] = DEFAULT_DOCUMENTATION_GLOBS,
    embeddings: EmbeddingClient | None = None,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Index:
    """Index the repository rooted at `root`.

    `documentation_globs` are patterns, relative to `root`, naming the markdown
    that counts as documentation. Python is found by walking the tree, so only
    the documentation side is configurable.

    Links are proposed by name always, and by embedding similarity as well when
    `embeddings` is given, at `threshold` cosine similarity or above. A pair
    both propose is linked once under each mechanism.
    """
    chunks: list[Chunk] = []
    for path in _source(root):
        try:
            chunks.extend(extract_chunks(path.read_bytes(), _relative(path, root)))
        except SyntaxError:
            # A repository can hold Python this interpreter cannot parse: a
            # python 2 file, a template, a fixture broken on purpose. One of
            # them must not cost the whole index.
            continue

    sections: list[Section] = []
    for path in _documentation(root, documentation_globs):
        markdown = path.read_text(encoding="utf-8", errors="replace")
        sections.extend(split_sections(markdown, _relative(path, root)))

    links = propose_name_links(sections, chunks)
    if embeddings is not None:
        links += propose_embedding_links(sections, chunks, embeddings, threshold)
    return Index(
        chunks=tuple(chunks),
        sections=tuple(sections),
        links=tuple(links),
        documentation_globs=tuple(documentation_globs),
    )


def build_index_at(root: Path, revision: str, documentation_globs: Sequence[str]) -> Index:
    """Index the repository at `root` as it stood at `revision`, not as its working tree is.

    Name links only: this is the index a run builds for itself, and a run that
    has to build one should not also have to pay for embeddings to do it.
    """
    commit = run(root, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()
    with tempfile.TemporaryDirectory() as directory:
        extract(root, commit, Path(directory))
        index = build_index(Path(directory), documentation_globs)
    return replace(index, revision=commit)


@dataclass(frozen=True)
class IndexUsed:
    """Which index a run analysed against, and why that one.

    `path` is the committed index when it was the one used. It is `None` when
    the run built its own at `revision`, and `passed_over` then says what was
    wrong with the committed one.
    """

    revision: str
    path: str | None
    passed_over: str | None = None

    def describe(self) -> str:
        """One sentence for the report, naming the index and the commit it describes."""
        if self.path is not None:
            return f"Index: {self.path}, built at {self.revision[:7]}."
        return (
            f"Index: built for this run at the base, {self.revision[:7]}, "
            f"because {self.passed_over}."
        )


def index_for(
    root: Path, base: str, path: Path, documentation_globs: Sequence[str]
) -> tuple[Index, IndexUsed]:
    """The index to analyse a change from `base` against, and which one it was.

    The index committed at `path` is read as it stood at `base`, never from
    the working tree: the Action checks out the pull request's head, and an
    index the pull request wrote could claim the base and link nothing. A
    `path` outside `root` is read from disk, since git has no version of it.

    It is used if it still describes `base`: it records the commit it was
    built at, it was built from the same documentation globs, and no Python
    or documentation file differs between that commit and `base`. Compared by
    content rather than by commit, because the commit that commits a rebuilt
    index is never the one it was built at.

    Otherwise an index is built in memory at `base`, so that an out-of-date
    or missing index makes a run slower rather than wrong.
    """
    commit = run(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    # Named as the repository sees it where it can be: the report is read on
    # a pull request, where the runner's checkout directory means nothing.
    shown = path.relative_to(root) if path.is_relative_to(root) else path
    index = _committed(root, commit, path, shown)
    reason = (
        index
        if isinstance(index, str)
        else _out_of_date(root, index, commit, documentation_globs, shown)
    )
    # The first two are implied by the third, and spelled out for mypy.
    if isinstance(index, Index) and index.revision is not None and reason is None:
        return index, IndexUsed(index.revision, str(shown))
    return (
        build_index_at(root, commit, documentation_globs),
        IndexUsed(commit, None, reason),
    )


def _committed(root: Path, base: str, path: Path, shown: Path) -> Index | str:
    """The index at `path` as of `base`, or why there is none to use."""
    if path.is_relative_to(root):
        try:
            # `./` makes the path relative to `root` rather than to the top
            # of the repository, which `root` need not be.
            document = run(root, "show", f"{base}:./{shown.as_posix()}")
        except subprocess.CalledProcessError:
            return f"there is no index at {shown} at the base"
    elif path.is_file():
        document = path.read_text(encoding="utf-8")
    else:
        return f"there is no index at {shown}"
    try:
        return Index.from_json(document)
    except (ValueError, KeyError, TypeError):
        # A merge conflict left in it, or a shape this version never wrote.
        # Either way an index can be built instead, so this is no reason to fail.
        return f"{shown} cannot be read as an index"


def _out_of_date(
    root: Path,
    index: Index,
    base: str,
    documentation_globs: Sequence[str],
    path: Path,
) -> str | None:
    """Why `index` does not describe `base`, or `None` when it does."""
    if index.revision is None:
        return f"{path} does not record the commit it was built at"
    if index.documentation_globs is None:
        return f"{path} does not record the documentation it was built from"
    # As sets: `build_index` sorts what the globs match, so their order changes
    # nothing in the index.
    if set(index.documentation_globs) != set(documentation_globs):
        return (
            f"{path} was built from documentation {', '.join(index.documentation_globs)}, "
            f"not {', '.join(documentation_globs)}"
        )
    built_at = index.revision[:7]
    try:
        run(root, "cat-file", "-e", f"{index.revision}^{{commit}}")
    except subprocess.CalledProcessError:
        return f"{path} was built at {built_at}, which this clone does not have"
    changed = [
        name
        for name in run(
            root, "diff", "-z", "--name-only", index.revision, base, "--",
            *_pathspecs(documentation_globs),
        ).split("\0")
        if name
    ]
    if changed:
        files = "1 file it covers" if len(changed) == 1 else f"{len(changed)} files it covers"
        return f"{path} was built at {built_at}, and {files} changed between then and the base"
    return None


def working_revision(root: Path, documentation_globs: Sequence[str]) -> str | None:
    """The commit an index of `root`'s working tree describes, or `None` if none does.

    That is the checked-out commit, as long as no file the index reads has
    uncommitted changes or is ignored. An index built from edits nobody has committed
    describes no revision, and recording one would let it pass as fresh.
    """
    try:
        head = run(root, "rev-parse", "--verify", "HEAD").strip()
    except subprocess.CalledProcessError:
        # Not a repository, or one with nothing committed yet.
        return None
    # Ignored files too: `build_index` walks the directory rather than asking
    # git, so it reads an ignored page that a clone at `head` does not have.
    # Except where `_source` never looks, or a virtualenv would count.
    status = run(
        root, "status", "--porcelain", "-z", "--ignored", "--",
        *_pathspecs(documentation_globs),
    )
    if any(not _skipped(Path(entry[3:])) for entry in status.split("\0") if entry):
        return None
    return head


def _pathspecs(documentation_globs: Sequence[str]) -> list[str]:
    # git's glob magic reads `**` as `Path.glob` does, so the files git
    # compares are the files `build_index` reads. Python is every `.py` file,
    # a superset of what `_source` keeps: a change to one it skips costs a
    # rebuild, never a wrong answer.
    return [":(glob)**/*.py", *(f":(glob){glob}" for glob in documentation_globs)]


def _source(root: Path) -> list[Path]:
    # TODO: replace with `git ls-files`, now that git is a hard dependency.
    # That would also exclude a non-hidden virtualenv and anything else the
    # repository has chosen to ignore.
    return sorted(
        path for path in root.rglob("*.py") if not _skipped(path.relative_to(root))
    )


def _skipped(relative: Path) -> bool:
    return any(part.startswith(".") or part == "__pycache__" for part in relative.parts)


def _documentation(root: Path, documentation_globs: Sequence[str]) -> list[Path]:
    matched = {
        path
        for glob in documentation_globs
        for path in root.glob(glob)
        if path.is_file() and _within(root, path)
    }
    return sorted(matched)


def _within(root: Path, path: Path) -> bool:
    # A glob can climb out with `..`, and documentation outside the repository
    # is out of scope — synclint only ever reports on what the repository owns.
    return path.resolve().is_relative_to(root.resolve())


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
