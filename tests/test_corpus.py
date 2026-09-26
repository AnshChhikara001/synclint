import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from synclint.__main__ import render_audit
from synclint.analyse import analyse
from synclint.corpus import (
    BASE_REF,
    DECOY_KINDS,
    KINDS,
    Audit,
    Case,
    Corpus,
    CorpusError,
    Decoy,
    audit_corpus,
    build_corpus,
    case_ref,
    decoy_ref,
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
    manifest: Sequence[Mapping[str, object]],
    decoys: dict[str, dict[str, str]] | None = None,
    decoy_manifest: Sequence[Mapping[str, object]] | None = None,
) -> None:
    write(source / "base", base)
    for case_id, overlay in cases.items():
        write(source / "cases" / case_id, overlay)
    for decoy_id, overlay in (decoys or {}).items():
        write(source / "decoys" / decoy_id, overlay)
    entries = [_table("case", entry) for entry in manifest]
    entries += [_table("decoy", entry) for entry in decoy_manifest or []]
    (source / "manifest.toml").write_text("\n".join(entries))


def _table(name: str, entry: Mapping[str, object]) -> str:
    # A JSON string or list of strings is also a TOML one.
    fields = "".join(
        f"{field} = {json.dumps(value)}\n" for field, value in sorted(entry.items())
    )
    return f"[[{name}]]\n{fields}"


FIND_QUERY_RENAMED = {
    "id": "find-query-renamed",
    "kind": "renamed-parameter",
    "section": "docs/catalogue.md#Catalogue > Finding books",
    "chunk": "catalogue.py::find",
    "description": "find's query parameter is now called text",
    "repair_says": ["find(text)"],
    "repair_drops": ["find(query)"],
}

FIND_CASE = Case(
    id="find-query-renamed",
    kind="renamed-parameter",
    section="docs/catalogue.md#Catalogue > Finding books",
    chunk="catalogue.py::find",
    description="find's query parameter is now called text",
    repair_says=("find(text)",),
    repair_drops=("find(query)",),
)


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


def test_a_case_the_same_size_and_age_as_the_base_still_changes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh clone gives every file the same modification time, and git on
    # Linux compares ctime only to the second, which a build can run inside.
    # An overlay the same size as the file it rewrites is then invisible to
    # git's stat check unless the copy stamps it with a new time.
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={
            "find-limit-default": {
                "catalogue.py": CATALOGUE.replace("limit=20", "limit=50")
            }
        },
        manifest=[
            {
                **FIND_QUERY_RENAMED,
                "id": "find-limit-default",
                "kind": "changed-default",
                "description": "find returns at most fifty books by default",
            }
        ],
    )
    for path in source.rglob("*"):
        os.utime(path, (1_700_000_000, 1_700_000_000))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.trustctime")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")

    corpus = build_corpus(source, tmp_path / "built")

    assert "limit=50" in file_at(
        corpus.root, case_ref("find-limit-default"), "catalogue.py"
    )


def test_a_manifest_entry_missing_a_field_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[{f: v for f, v in FIND_QUERY_RENAMED.items() if f != "chunk"}],
    )

    with pytest.raises(CorpusError, match="find-query-renamed is missing chunk"):
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
    "repair_says": ["twenty-five"],
    "repair_drops": ["fifty"],
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
        "find-query-renamed: no chunk catalogue.py::search at base or "
        "case/find-query-renamed",
        "find-query-renamed: the base index has no section "
        "docs/catalogue.md#Catalogue > Borrowing",
    )


def test_repair_ground_truth_the_unrepaired_section_already_meets_is_a_fault(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED | {"repair_says": [], "repair_drops": ["find(text)"]}],
    )

    audit = audit_corpus(build_corpus(source, tmp_path / "built"))

    assert audit.faults == (
        "find-query-renamed: the section as it stands already meets its repair "
        "ground truth, so a repair that changed nothing would be judged correct",
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
        "renamed-parameter, changed-default, removed-capability, contradicted-claim, "
        "deleted-chunk",
        "half-written: an overlay with no case in the manifest",
    )


def test_the_command_line_prints_the_shape_the_reach_and_the_faults() -> None:
    printed = render_audit(
        Audit(
            cases=(FIND_CASE, replace(FIND_CASE, id="shelf-capacity-default", kind="changed-default")),
            decoys=(),
            faults=("half-written: an overlay with no case in the manifest",),
            reachable=("find-query-renamed",),
            unreachable=("shelf-capacity-default",),
            suspected=(),
        )
    )

    assert "2 cases: 1 renamed-parameter, 1 changed-default." in printed
    assert "1 of 2 reachable; 1 unreachable:" in printed
    assert "    shelf-capacity-default" in printed
    assert "1 fault:" in printed
    assert "    half-written: an overlay with no case in the manifest" in printed


def test_the_command_line_says_so_when_the_corpus_is_sound() -> None:
    printed = render_audit(
        Audit(
            cases=(FIND_CASE,),
            decoys=(),
            faults=(),
            reachable=("find-query-renamed",),
            unreachable=(),
            suspected=(),
        )
    )

    assert "1 of 1 reachable." in printed
    assert "No faults." in printed
    # A corpus with no decoys says nothing about decoys rather than "0 decoys".
    assert "decoy" not in printed


FIND_REFLOWED = """
    def find(
        query,
        limit=20,
    ):
        return [query] * limit
    """

FIND_REFLOWED_DECOY = {
    "id": "find-reflowed",
    "kind": "formatting",
    "description": "find's signature is wrapped across lines",
}


def test_each_decoy_becomes_its_own_branch_off_the_base(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
        decoys={"find-reflowed": {"catalogue.py": FIND_REFLOWED}},
        decoy_manifest=[FIND_REFLOWED_DECOY],
    )

    corpus = build_corpus(source, tmp_path / "built")

    assert [decoy.id for decoy in corpus.decoys] == ["find-reflowed"]
    reflowed = file_at(corpus.root, decoy_ref("find-reflowed"), "catalogue.py")
    assert "def find(\n" in reflowed
    # Off the base rather than on top of the cases, so that the diff a decoy
    # is measured on holds nothing but the decoy.
    assert "def find(text" not in reflowed


FIND_REFACTORED = """
    def find(query, limit=20):
        found = [query] * limit
        return found
    """

FIND_BODY_REFACTORED = {
    "id": "find-body-refactored",
    "kind": "internal-refactor",
    "description": "find names the list it returns",
}


def audit_decoys(
    tmp_path: Path,
    decoys: dict[str, dict[str, str]],
    decoy_manifest: list[dict[str, str]],
    base: dict[str, str] | None = None,
) -> Audit:
    """Build a one-case corpus carrying these decoys, and audit it."""
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base=base or {"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
        decoys=decoys,
        decoy_manifest=decoy_manifest,
    )
    return audit_corpus(build_corpus(source, tmp_path / "built"))


def test_a_decoy_that_only_reformats_puts_nothing_to_the_model(tmp_path: Path) -> None:
    audit = audit_decoys(
        tmp_path,
        decoys={"find-reflowed": {"catalogue.py": FIND_REFLOWED}},
        decoy_manifest=[FIND_REFLOWED_DECOY],
    )

    assert audit.faults == ()
    assert audit.decoys == (Decoy(**FIND_REFLOWED_DECOY),)
    # Reformatting does not survive a parse, so there is no changed chunk for a
    # section to hang off: this decoy cannot produce a finding whatever a model
    # would have said about it.
    assert audit.suspected == ()


def test_a_refactor_decoy_is_put_to_the_model_like_any_other_change(
    tmp_path: Path,
) -> None:
    audit = audit_decoys(
        tmp_path,
        decoys={"find-body-refactored": {"catalogue.py": FIND_REFACTORED}},
        decoy_manifest=[FIND_BODY_REFACTORED],
    )

    assert audit.faults == ()
    # The body really did change, so the section linked to it is a suspect and
    # only the model's judgement keeps this from being a false positive.
    assert audit.suspected == ("find-body-refactored",)


def test_a_refactor_decoy_that_changes_no_chunk_is_a_fault(tmp_path: Path) -> None:
    audit = audit_decoys(
        tmp_path,
        decoys={"find-body-refactored": {"catalogue.py": FIND_REFLOWED}},
        decoy_manifest=[FIND_BODY_REFACTORED],
    )

    # The one thing a decoy must not be is a no-op wearing the label of a real
    # change: nothing could ever have gone wrong about it, so clearing it is
    # not evidence of anything.
    assert audit.faults == (
        "find-body-refactored: kind internal-refactor promises a changed chunk, "
        "and this commit changes none",
    )
    assert audit.suspected == ()


def test_a_formatting_decoy_that_changes_a_chunk_is_a_fault(tmp_path: Path) -> None:
    audit = audit_decoys(
        tmp_path,
        decoys={"find-reflowed": {"catalogue.py": FIND_REFACTORED}},
        decoy_manifest=[FIND_REFLOWED_DECOY],
    )

    assert audit.faults == (
        "find-reflowed: kind formatting promises no changed chunk, "
        "and this commit changes catalogue.py::find",
    )


def test_a_decoy_that_edits_documentation_is_a_fault(tmp_path: Path) -> None:
    audit = audit_decoys(
        tmp_path,
        decoys={
            "find-reflowed": {
                "catalogue.py": FIND_REFLOWED,
                "docs/catalogue.md": "# Catalogue\n\n## Finding books\n\nCall it.\n",
            }
        },
        decoy_manifest=[FIND_REFLOWED_DECOY],
    )

    assert audit.faults == (
        "find-reflowed: edits documentation (docs/catalogue.md); a decoy changes "
        "code and leaves the prose alone",
    )


CATALOGUE_TESTS = """
    from catalogue import find


    def test_find_returns_one_result_per_place_in_the_limit():
        assert find("dune") == ["dune"] * 20
    """

CATALOGUE_TESTS_EXTENDED = """
    from catalogue import find


    def test_find_returns_one_result_per_place_in_the_limit():
        assert find("dune") == ["dune"] * 20


    def test_find_honours_a_limit_it_is_given():
        assert find("dune", 2) == ["dune", "dune"]
    """

TESTS_COVER_THE_LIMIT = {
    "id": "tests-cover-the-limit",
    "kind": "test-only",
    "description": "find's limit gains a test",
}

BASE_WITH_TESTS = {
    "catalogue.py": CATALOGUE,
    "docs/catalogue.md": DOCS,
    "tests/test_catalogue.py": CATALOGUE_TESTS,
}


def test_a_test_only_decoy_puts_nothing_to_the_model(tmp_path: Path) -> None:
    audit = audit_decoys(
        tmp_path,
        base=BASE_WITH_TESTS,
        decoys={
            "tests-cover-the-limit": {"tests/test_catalogue.py": CATALOGUE_TESTS_EXTENDED}
        },
        decoy_manifest=[TESTS_COVER_THE_LIMIT],
    )

    assert audit.faults == ()
    assert audit.suspected == ()


def test_a_test_only_decoy_that_touches_source_is_a_fault(tmp_path: Path) -> None:
    audit = audit_decoys(
        tmp_path,
        base=BASE_WITH_TESTS,
        decoys={
            "tests-cover-the-limit": {
                "tests/test_catalogue.py": CATALOGUE_TESTS_EXTENDED,
                "catalogue.py": FIND_REFLOWED,
            }
        },
        decoy_manifest=[TESTS_COVER_THE_LIMIT],
    )

    # Reformatting the source alongside would clear the chunk check, and the
    # decoy would then be measuring something other than what it is called.
    assert audit.faults == (
        "tests-cover-the-limit: kind test-only promises a change to test files "
        "only, and this commit changes catalogue.py",
    )


def test_an_unknown_decoy_kind_and_an_unclaimed_decoy_overlay_are_faults(
    tmp_path: Path,
) -> None:
    audit = audit_decoys(
        tmp_path,
        decoys={
            "find-reflowed": {"catalogue.py": FIND_REFLOWED},
            "half-written": {"catalogue.py": FIND_REFACTORED},
        },
        decoy_manifest=[FIND_REFLOWED_DECOY | {"kind": "whitespace"}],
    )

    assert audit.faults == (
        "find-reflowed: kind whitespace is not one of internal-refactor, "
        "added-parameter, comment-edit, test-only, formatting, moved-chunk",
        "half-written: an overlay with no decoy in the manifest",
    )


def test_the_command_line_prints_the_decoys_and_what_reaches_the_model() -> None:
    printed = render_audit(
        Audit(
            cases=(FIND_CASE,),
            decoys=(Decoy(**FIND_BODY_REFACTORED), Decoy(**FIND_REFLOWED_DECOY)),
            faults=(),
            reachable=("find-query-renamed",),
            unreachable=(),
            suspected=("find-body-refactored",),
        )
    )

    assert "2 decoys: 1 internal-refactor, 1 formatting." in printed
    assert "1 reaches the model, where a false positive is still possible:" in printed
    assert "    find-body-refactored" in printed


def test_the_command_line_says_when_no_decoy_reaches_the_model() -> None:
    printed = render_audit(
        Audit(
            cases=(FIND_CASE,),
            decoys=(Decoy(**FIND_REFLOWED_DECOY),),
            faults=(),
            reachable=("find-query-renamed",),
            unreachable=(),
            suspected=(),
        )
    )

    assert "1 decoy: 1 formatting." in printed
    assert "None reaches the model, so none can produce a finding." in printed


SHIPPED = Path(__file__).parent.parent / "corpus"


@pytest.fixture(scope="module")
def shipped(tmp_path_factory: pytest.TempPathFactory) -> Corpus:
    """The shipped corpus, built once and shared by the tests that read it."""
    return build_corpus(SHIPPED, tmp_path_factory.mktemp("shipped") / "built")


@pytest.fixture(scope="module")
def audited(shipped: Corpus) -> Audit:
    """The shipped corpus audited once: thirty branches is not a cheap answer."""
    return audit_corpus(shipped)


def test_the_shipped_corpus_plants_eighteen_cases_and_holds_together(
    shipped: Corpus, audited: Audit
) -> None:
    corpus = shipped

    audit = audited

    assert audit.faults == ()
    assert len(corpus.cases) == 18
    kinds = Counter(case.kind for case in corpus.cases)
    assert set(kinds) == set(KINDS)
    # Not five of each any more. `contradicted-claim` holds the two cases left
    # when `undocumented-feature` split, and planting more needs a recording
    # pass, so what the corpus owes is every kind represented, not a balance.
    # `deleted-chunk` is found without a model, so one case says all it can.
    assert min(count for kind, count in kinds.items() if kind != "deleted-chunk") >= 2
    # How many of them synclint reaches is a measurement rather than a
    # requirement — the manifest is ground truth, and a case nothing reaches is
    # a gap to be reported. What the corpus owes is an answer for every case.
    assert len(audit.reachable) + len(audit.unreachable) == 18


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
    manifest can be compared against. Asked for a repair, it proposes none.
    """

    name = "drifted-model"
    max_output_tokens = 500

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse:
        properties = schema["properties"]
        assert isinstance(properties, dict)
        answer = (
            {"edits": []}
            if "edits" in properties
            else {"accurate": False, "explanation": "it changed"}
        )
        return ModelResponse(
            text=json.dumps(answer),
            input_tokens=1,
            output_tokens=1,
        )


def test_analyse_over_the_corpus_reports_the_cases_the_manifest_expects(
    shipped: Corpus, audited: Audit
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
    assert reported == set(audited.reachable)
    assert reported


def test_the_shipped_corpus_plants_fourteen_decoys_across_every_kind(
    shipped: Corpus, audited: Audit
) -> None:
    assert audited.faults == ()
    assert len(shipped.decoys) == 14
    kinds = Counter(decoy.kind for decoy in shipped.decoys)
    assert set(kinds) == set(DECOY_KINDS)
    # A move followed raises nothing whatever a model thinks, so one decoy
    # says all a second would.
    assert min(count for kind, count in kinds.items() if kind != "moved-chunk") >= 2


def test_a_decoy_nothing_reaches_cannot_produce_a_finding(
    shipped: Corpus, audited: Audit
) -> None:
    index = build_index(shipped.root)
    silent = [
        decoy.id for decoy in shipped.decoys if decoy.id not in audited.suspected
    ]

    assert len(silent) == 7
    for decoy_id in silent:
        client = ModelClient(
            DriftedModel(), pricing=Pricing(input=0.0, output=0.0), ceiling=1.0
        )
        report = analyse(shipped.root, index, BASE_REF, decoy_ref(decoy_id), client)

        # Asked with a model that calls everything drift, these seven still
        # report nothing: comments, formatting and test code do not survive a
        # parse, and a chunk moved word for word is followed and compares
        # equal, so there is no suspect to put a question about and nothing is
        # spent.
        assert report.findings == ()
        assert report.spend.calls == 0


def test_a_decoy_that_reaches_the_model_is_only_cleared_by_its_judgement(
    shipped: Corpus, audited: Audit
) -> None:
    index = build_index(shipped.root)

    assert audited.suspected
    for decoy_id in audited.suspected:
        client = ModelClient(
            DriftedModel(), pricing=Pricing(input=0.0, output=0.0), ceiling=1.0
        )
        report = analyse(shipped.root, index, BASE_REF, decoy_ref(decoy_id), client)

        # The other four are false positives the moment the model says so, which
        # is what makes them worth having: nothing structural saves them, and the
        # rate at which a real model clears them is the precision figure #6 owes.
        assert report.findings


SHELVE_ONLY = """
    def shelve(book):
        return book
    """

FIND_DELETED = {
    "id": "find-deleted",
    "kind": "deleted-chunk",
    "section": "docs/catalogue.md#Catalogue > Finding books",
    "chunk": "catalogue.py::find",
    "description": "find is gone",
    "repair_says": [],
    "repair_drops": ["find(query)"],
}


def test_a_case_that_deletes_the_chunk_its_section_names_is_reachable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE_AND_SHELVE, "docs/catalogue.md": DOCS},
        cases={"find-deleted": {"catalogue.py": SHELVE_ONLY}},
        manifest=[FIND_DELETED],
    )

    audit = audit_corpus(build_corpus(source, tmp_path / "built"))

    assert audit.faults == ()
    assert audit.reachable == ("find-deleted",)


FIND_MOVED = {"id": "find-moves", "kind": "moved-chunk", "description": "find moves"}


def test_a_decoy_that_moves_a_chunk_word_for_word_raises_nothing(tmp_path: Path) -> None:
    audit = audit_decoys(
        tmp_path,
        decoys={"find-moves": {"catalogue.py": SHELVE_ONLY, "finding.py": CATALOGUE}},
        decoy_manifest=[FIND_MOVED],
        base={"catalogue.py": CATALOGUE_AND_SHELVE, "docs/catalogue.md": DOCS},
    )

    assert audit.faults == ()
    # Not followed, the page naming `find` would read as naming deleted code.
    assert audit.suspected == ()


def test_a_move_decoy_that_moves_nothing_is_a_fault(tmp_path: Path) -> None:
    audit = audit_decoys(
        tmp_path,
        decoys={"find-moves": {"catalogue.py": FIND_REFLOWED}},
        decoy_manifest=[FIND_MOVED],
    )

    assert audit.faults == (
        "find-moves: kind moved-chunk promises a chunk moved to another file, "
        "and this commit moves none",
    )
