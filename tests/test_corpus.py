import json
from collections import Counter
from pathlib import Path
from textwrap import dedent

import pytest

from synclint.__main__ import render_audit
from synclint.analyse import analyse
from synclint.corpus import (
    BASE_REF,
    KINDS,
    Audit,
    Case,
    Corpus,
    CorpusError,
    audit_corpus,
    build_corpus,
    case_ref,
)
from synclint.git import file_at
from synclint.index import build_index
from synclint.model import ModelClient, ModelResponse, Pricing

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

    audit = audit_corpus(build_corpus(source, tmp_path / "built"))

    assert audit.faults == ()
    assert audit.reachable == ("find-query-renamed",)
    assert audit.unreachable == ()


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


def test_a_case_no_link_reaches_is_unreachable_rather_than_a_fault(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"shelves.py": SHELVES, "docs/shelves.md": SHELF_DOCS},
        cases={"shelf-capacity-default": {"shelves.py": SHELF_CAPACITY_HALVED}},
        manifest=[SHELF_CAPACITY_DEFAULT],
    )

    audit = audit_corpus(build_corpus(source, tmp_path / "built"))

    # The prose names `Shelf`, so the link lands on the class; the default it
    # describes lives in `__init__`, which is the chunk that changed. Real
    # drift, correctly recorded, that nothing in synclint reaches today.
    assert audit.faults == ()
    assert audit.unreachable == ("shelf-capacity-default",)


def test_a_case_that_edits_documentation_is_a_fault(tmp_path: Path) -> None:
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

    audit = audit_corpus(build_corpus(source, tmp_path / "built"))

    assert audit.faults == (
        "find-query-renamed: edits documentation (docs/catalogue.md); "
        "a planted case changes code and leaves the prose stale",
    )


def test_a_manifest_pointing_at_what_is_not_there_is_a_fault(tmp_path: Path) -> None:
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

    audit = audit_corpus(build_corpus(source, tmp_path / "built"))

    assert audit.faults == (
        "find-query-renamed: no chunk catalogue.py::search at case/find-query-renamed",
        "find-query-renamed: the base index has no section "
        "docs/catalogue.md#Catalogue > Borrowing",
    )


def test_an_unknown_kind_and_an_unclaimed_overlay_are_faults(tmp_path: Path) -> None:
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

    audit = audit_corpus(build_corpus(source, tmp_path / "built"))

    assert audit.faults == (
        "find-query-renamed: kind renamed-argument is not one of "
        "renamed-parameter, changed-default, removed-capability, undocumented-feature",
        "half-written: an overlay with no case in the manifest",
    )


def test_the_command_line_prints_the_shape_the_reach_and_the_faults() -> None:
    printed = render_audit(
        Audit(
            cases=(Case(**FIND_QUERY_RENAMED), Case(**SHELF_CAPACITY_DEFAULT)),
            faults=("half-written: an overlay with no case in the manifest",),
            reachable=("find-query-renamed",),
            unreachable=("shelf-capacity-default",),
        )
    )

    assert "2 cases: 1 renamed-parameter, 1 changed-default." in printed
    assert "1 of 2 reachable as a suspect; 1 unreachable:" in printed
    assert "    shelf-capacity-default" in printed
    assert "1 fault:" in printed
    assert "    half-written: an overlay with no case in the manifest" in printed


def test_the_command_line_says_so_when_the_corpus_is_sound() -> None:
    printed = render_audit(
        Audit(
            cases=(Case(**FIND_QUERY_RENAMED),),
            faults=(),
            reachable=("find-query-renamed",),
            unreachable=(),
        )
    )

    assert "1 of 1 reachable as a suspect." in printed
    assert "No faults." in printed


SHIPPED = Path(__file__).parent.parent / "corpus"


@pytest.fixture(scope="module")
def shipped(tmp_path_factory: pytest.TempPathFactory) -> Corpus:
    """The shipped corpus, built once and shared by the tests that read it."""
    return build_corpus(SHIPPED, tmp_path_factory.mktemp("shipped") / "built")


def test_the_shipped_corpus_plants_twenty_cases_and_holds_together(
    shipped: Corpus,
) -> None:
    corpus = shipped

    audit = audit_corpus(corpus)

    assert audit.faults == ()
    assert len(corpus.cases) == 20
    kinds = Counter(case.kind for case in corpus.cases)
    assert set(kinds) == set(KINDS)
    assert min(kinds.values()) >= 4
    # How many of them synclint reaches is a measurement rather than a
    # requirement — the manifest is ground truth, and a case nothing reaches is
    # a gap to be reported. What the corpus owes is an answer for every case.
    assert len(audit.reachable) + len(audit.unreachable) == 20


CATALOGUE_AND_SHELVE = """
    def find(query, limit=20):
        return [query] * limit


    def shelve(book):
        return book
    """

RENAMED_AND_SHELVE = """
    def find(text, limit=20):
        return [text] * limit


    def shelve(book):
        return book
    """


def test_a_case_that_does_not_change_the_chunk_it_names_is_a_fault(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE_AND_SHELVE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED_AND_SHELVE}},
        manifest=[FIND_QUERY_RENAMED | {"chunk": "catalogue.py::shelve"}],
    )

    audit = audit_corpus(build_corpus(source, tmp_path / "built"))

    # Unreachable would be the comfortable answer and the wrong one: it is
    # reserved for drift synclint cannot get to, not for a mis-aimed manifest.
    assert audit.faults == (
        "find-query-renamed: the commit at case/find-query-renamed "
        "does not change catalogue.py::shelve",
    )
    assert audit.unreachable == ()


class DriftedModel:
    """Answers that every suspect has drifted, whatever it is asked.

    Deliberately not `ScriptedModel` from the analyse tests: this one exists to
    take the model's judgement out of the picture entirely — measuring that is
    #6's job — and leave only the path from a planted case to a finding the
    manifest can be compared against.
    """

    name = "drifted-model"
    max_output_tokens = 500

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse:
        return ModelResponse(
            text=json.dumps({"accurate": False, "explanation": "it changed"}),
            input_tokens=1,
            output_tokens=1,
        )


def test_analyse_over_the_corpus_reports_the_cases_the_manifest_expects(
    shipped: Corpus,
) -> None:
    index = build_index(shipped.root)

    reported = set()
    for case in shipped.cases:
        client = ModelClient(
            DriftedModel(), pricing=Pricing(input=0.0, output=0.0), ceiling=1.0
        )
        report = analyse(shipped.root, index, BASE_REF, case_ref(case.id), client)
        if (case.section, case.chunk) in {
            (finding.section, finding.chunk) for finding in report.findings
        }:
            reported.add(case.id)

    # With the model agreeing to everything, what comes back is exactly what the
    # run reached — so this is the audit's reachability figure arrived at through
    # the real entry point rather than through `suspects` alone. Whether the
    # model agrees for the right reasons is the accuracy harness's question.
    assert reported == set(audit_corpus(shipped).reachable)
    assert reported
