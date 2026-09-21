from pathlib import Path
from textwrap import dedent

import pytest

from synclint.corpus import (
    BASE_REF,
    CorpusError,
    build_corpus,
    case_ref,
    validate_corpus,
)
from synclint.git import file_at

CATALOGUE = """
    def find(query, limit=20):
        return [query] * limit
    """

DOCS = """
    # Catalogue

    ## Finding books

    Call `find(query)`. It returns at most twenty books.
    """

RENAMED = """
    def find(text, limit=20):
        return [text] * limit
    """


def write(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).lstrip())


def write_corpus(
    source: Path,
    base: dict[str, str],
    cases: dict[str, dict[str, str]],
    manifest: list[dict[str, str]],
) -> None:
    write(source / "base", base)
    for case_id, overlay in cases.items():
        write(source / "cases" / case_id, overlay)
    entries = [
        "[[case]]\n" + "".join(f'{field} = "{value}"\n' for field, value in case)
        for case in (sorted(entry.items()) for entry in manifest)
    ]
    (source / "manifest.toml").write_text("\n".join(entries))


FIND_QUERY_RENAMED = {
    "id": "find-query-renamed",
    "kind": "renamed-parameter",
    "section": "docs/catalogue.md#Catalogue > Finding books",
    "chunk": "catalogue.py::find",
    "description": "find's query parameter is now called text",
}


def test_each_case_becomes_its_own_commit_off_the_base(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
    )

    corpus = build_corpus(source, tmp_path / "built")

    assert [case.id for case in corpus.cases] == ["find-query-renamed"]
    assert "def find(text" in file_at(
        corpus.root, case_ref("find-query-renamed"), "catalogue.py"
    )
    assert "def find(query" in file_at(corpus.root, BASE_REF, "catalogue.py")
    # Left at base so that one index built from the working tree serves every
    # case, which is the whole reason the cases fan out rather than stack.
    assert "def find(query" in (corpus.root / "catalogue.py").read_text()


def test_a_case_that_changes_nothing_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": CATALOGUE}},
        manifest=[FIND_QUERY_RENAMED],
    )

    with pytest.raises(CorpusError, match="changes nothing"):
        build_corpus(source, tmp_path / "built")


def test_a_case_whose_section_names_its_changed_chunk_is_reachable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
    )

    validation = validate_corpus(build_corpus(source, tmp_path / "built"))

    assert validation.problems == ()
    assert validation.reachable == ("find-query-renamed",)
    assert validation.unreachable == ()


SHELVES = """
    class Shelf:
        def __init__(self, name, capacity=50):
            self.name = name
            self.capacity = capacity
    """

SHELF_DOCS = """
    # Shelves

    ## Capacity

    A `Shelf` holds fifty books unless you say otherwise.
    """

SHELF_CAPACITY_HALVED = """
    class Shelf:
        def __init__(self, name, capacity=25):
            self.name = name
            self.capacity = capacity
    """

SHELF_CAPACITY_DEFAULT = {
    "id": "shelf-capacity-default",
    "kind": "changed-default",
    "section": "docs/shelves.md#Shelves > Capacity",
    "chunk": "shelves.py::Shelf.__init__",
    "description": "a shelf now holds twenty-five books by default, not fifty",
}


def test_a_case_no_link_reaches_is_unreachable_rather_than_a_problem(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"shelves.py": SHELVES, "docs/shelves.md": SHELF_DOCS},
        cases={"shelf-capacity-default": {"shelves.py": SHELF_CAPACITY_HALVED}},
        manifest=[SHELF_CAPACITY_DEFAULT],
    )

    validation = validate_corpus(build_corpus(source, tmp_path / "built"))

    # The prose names `Shelf`, so the link lands on the class; the default it
    # describes lives in `__init__`, which is the chunk that changed. Real
    # drift, correctly recorded, that nothing in synclint reaches today.
    assert validation.problems == ()
    assert validation.unreachable == ("shelf-capacity-default",)


def test_a_case_that_edits_documentation_is_a_problem(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={
            "find-query-renamed": {
                "catalogue.py": RENAMED,
                "docs/catalogue.md": "# Catalogue\n\n## Finding books\n\nCall `find(text)`.\n",
            }
        },
        manifest=[FIND_QUERY_RENAMED],
    )

    validation = validate_corpus(build_corpus(source, tmp_path / "built"))

    assert validation.problems == (
        "find-query-renamed: edits documentation (docs/catalogue.md); "
        "a planted case changes code and leaves the prose stale",
    )


def test_a_manifest_pointing_at_what_is_not_there_is_a_problem(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[
            FIND_QUERY_RENAMED
            | {
                "chunk": "catalogue.py::search",
                "section": "docs/catalogue.md#Catalogue > Borrowing",
            }
        ],
    )

    validation = validate_corpus(build_corpus(source, tmp_path / "built"))

    assert validation.problems == (
        "find-query-renamed: no chunk catalogue.py::search at case/find-query-renamed",
        "find-query-renamed: the base index has no section "
        "docs/catalogue.md#Catalogue > Borrowing",
    )


def test_an_unknown_kind_and_an_unclaimed_overlay_are_problems(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={
            "find-query-renamed": {"catalogue.py": RENAMED},
            "half-written": {"catalogue.py": CATALOGUE},
        },
        manifest=[FIND_QUERY_RENAMED | {"kind": "renamed-argument"}],
    )

    validation = validate_corpus(build_corpus(source, tmp_path / "built"))

    assert validation.problems == (
        "find-query-renamed: kind renamed-argument is not one of "
        "renamed-parameter, changed-default, removed-capability, undocumented-feature",
        "half-written: an overlay with no case in the manifest",
    )
