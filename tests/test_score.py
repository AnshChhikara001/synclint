import json
from collections.abc import Iterable
from pathlib import Path
from textwrap import dedent

import pytest

from synclint.__main__ import ANSWERS, main, render_score
from synclint.analyse import Finding
from synclint.corpus import Corpus, build_corpus
from synclint.model import DEFAULT_MODEL, ModelClient, ModelResponse, Pricing, Spend
from synclint.score import Score, Scored, score_corpus

PRICING = Pricing(input=1.00, output=2.00)

CATALOGUE = """
    def find(query, limit=20):
        return [query] * limit
    """

RENAMED = """
    def find(text, limit=20):
        return [text] * limit
    """

REFACTORED = """
    def find(query, limit=20):
        found = [query] * limit
        return found
    """

DOCS = """
    # Catalogue

    ## Finding books

    Call `find(query)`. It returns at most twenty books.
    """

FIND_SECTION = "docs/catalogue.md#Catalogue > Finding books"
FIND_CHUNK = "catalogue.py::find"

FIND_QUERY_RENAMED = {
    "id": "find-query-renamed",
    "kind": "renamed-parameter",
    "section": FIND_SECTION,
    "chunk": FIND_CHUNK,
    "description": "find's query parameter is now called text",
}

FIND_REFACTORED = {
    "id": "find-keeps-its-result",
    "kind": "internal-refactor",
    "description": "find names its result before returning it",
}


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
    decoys: dict[str, dict[str, str]] | None = None,
    decoy_manifest: list[dict[str, str]] | None = None,
) -> None:
    write(source / "base", base)
    for case_id, overlay in cases.items():
        write(source / "cases" / case_id, overlay)
    for decoy_id, overlay in (decoys or {}).items():
        write(source / "decoys" / decoy_id, overlay)
    entries = [_table("case", entry) for entry in manifest]
    entries += [_table("decoy", entry) for entry in decoy_manifest or []]
    (source / "manifest.toml").write_text("\n".join(entries))


def _table(name: str, entry: dict[str, str]) -> str:
    fields = "".join(f'{field} = "{value}"\n' for field, value in sorted(entry.items()))
    return f"[[{name}]]\n{fields}"


class DecidingModel:
    """Calls a section drifted when the question names something it was given.

    A marker is any text the prompt carries — a section id, a chunk id, an
    identifier out of the code — so a test can fail one suspect and clear the
    others without knowing the order they are asked in.
    """

    name = "deciding-model"
    max_output_tokens = 500

    def __init__(self, drifted: Iterable[str] = ()) -> None:
        self._drifted = tuple(drifted)
        self.asked: list[str] = []

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse:
        self.asked.append(user)
        drifted = any(marker in user for marker in self._drifted)
        return ModelResponse(
            text=json.dumps(
                {
                    "accurate": not drifted,
                    "explanation": "the parameter is called something else" if drifted else "",
                }
            ),
            input_tokens=10,
            output_tokens=5,
        )


def client(model: DecidingModel, cache: Path | None = None) -> ModelClient:
    return ModelClient(model, pricing=PRICING, ceiling=1.00, cache=cache)


def one_case(tmp_path: Path, model: DecidingModel) -> tuple[Path, ModelClient]:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
    )
    return source, client(model)


def test_a_planted_case_the_model_reports_is_a_true_positive(tmp_path: Path) -> None:
    model = DecidingModel(drifted=[FIND_SECTION])
    source, asked = one_case(tmp_path, model)

    score = score_corpus(build_corpus(source, tmp_path / "built"), asked)

    assert score.true_positives == 1
    assert score.false_negatives == 0
    assert score.false_positives == 0
    assert score.recall == 1.0
    assert score.precision == 1.0


def test_a_planted_case_the_model_clears_is_a_false_negative(tmp_path: Path) -> None:
    model = DecidingModel()
    source, asked = one_case(tmp_path, model)

    score = score_corpus(build_corpus(source, tmp_path / "built"), asked)

    assert score.true_positives == 0
    assert score.false_negatives == 1
    assert score.recall == 0.0
    # Nothing was reported at all, so there is no precision to compute. Zero
    # would read as "everything it said was wrong", and it said nothing.
    assert score.precision is None


def with_a_decoy(tmp_path: Path, model: DecidingModel) -> tuple[Path, ModelClient]:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
        decoys={"find-keeps-its-result": {"catalogue.py": REFACTORED}},
        decoy_manifest=[FIND_REFACTORED],
    )
    return source, client(model)


def test_a_finding_on_a_decoy_branch_is_a_false_positive(tmp_path: Path) -> None:
    # The marker is in the code rather than the section, so both branches are
    # called drift: the one that planted it and the refactor that did not.
    model = DecidingModel(drifted=["find"])
    source, asked = with_a_decoy(tmp_path, model)

    score = score_corpus(build_corpus(source, tmp_path / "built"), asked)

    assert score.true_positives == 1
    assert score.false_positives == 1
    assert score.precision == 0.5
    assert score.recall == 1.0
    decoy = next(result for result in score.branches if result.decoy)
    assert decoy.id == "find-keeps-its-result"
    assert [finding.section for finding in decoy.spurious] == [FIND_SECTION]


def test_a_decoy_the_model_clears_reports_nothing(tmp_path: Path) -> None:
    model = DecidingModel()
    source, asked = with_a_decoy(tmp_path, model)

    score = score_corpus(build_corpus(source, tmp_path / "built"), asked)

    decoy = next(result for result in score.branches if result.decoy)
    assert decoy.spurious == ()
    # It still reached the model, which is what separates a decoy the judgement
    # cleared from one the design ruled out before anything was asked.
    assert decoy.verified == 1


TWO_SECTIONS = """
    # Catalogue

    ## Finding books

    Call `find(query)`. It returns at most twenty books.

    ## Performance

    A call to `find` is linear in the size of the catalogue.
    """


def test_a_finding_beside_the_planted_one_is_a_false_positive(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": TWO_SECTIONS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
    )
    model = DecidingModel(drifted=["find"])

    score = score_corpus(build_corpus(source, tmp_path / "built"), client(model))

    # Both sections name `find`, so both are suspects, but the manifest planted
    # drift in only one. The other is a false positive on a case branch, which
    # counts exactly as one on a decoy does.
    assert score.true_positives == 1
    assert score.false_positives == 1
    assert [finding.section for finding in score.branches[0].spurious] == [
        "docs/catalogue.md#Catalogue > Performance"
    ]


def test_a_recorded_run_replays_free_and_reports_the_same_numbers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
    )
    corpus = build_corpus(source, tmp_path / "built")
    answers = tmp_path / "answers"
    model = DecidingModel(drifted=[FIND_SECTION])
    recorded = score_corpus(corpus, ModelClient(model, pricing=PRICING, ceiling=1.00, cache=answers))

    replayed = score_corpus(corpus, ModelClient.replaying(model.name, answers))

    assert replayed.unrecorded == ()
    assert replayed.spend == Spend()
    assert replayed.true_positives == recorded.true_positives
    assert replayed.precision == recorded.precision
    assert replayed.recall == recorded.recall
    assert len(model.asked) == 1


def test_a_branch_with_no_recorded_answer_is_named_rather_than_asked(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
    )
    corpus = build_corpus(source, tmp_path / "built")

    score = score_corpus(corpus, ModelClient.replaying("deciding-model", tmp_path / "empty"))

    assert score.unrecorded == ("find-query-renamed",)
    assert score.branches == ()
    assert score.spend == Spend()


def test_answers_recorded_under_another_model_are_not_replayed(tmp_path: Path) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
    )
    corpus = build_corpus(source, tmp_path / "built")
    answers = tmp_path / "answers"
    score_corpus(corpus, client(DecidingModel(drifted=[FIND_SECTION]), cache=answers))

    # The cache key carries the model name, so a number recorded against one
    # model cannot be republished as another's.
    score = score_corpus(corpus, ModelClient.replaying("some-other-model", answers))

    assert score.unrecorded == ("find-query-renamed",)


def scored(**fields: object) -> Scored:
    defaults: dict[str, object] = {
        "id": "a-case",
        "kind": "renamed-parameter",
        "decoy": False,
        "verified": 1,
        "found": True,
        "spurious": (),
    }
    return Scored(**{**defaults, **fields})  # type: ignore[arg-type]


def test_the_report_breaks_recall_down_by_drift_kind() -> None:
    score = Score(
        branches=(
            scored(id="a", kind="renamed-parameter", found=True),
            scored(id="b", kind="renamed-parameter", found=False),
            scored(id="c", kind="changed-default", found=True),
        ),
        unrecorded=(),
        unfinished=0,
        spend=Spend(),
    )

    report = render_score(score)

    assert "| renamed-parameter | 2 | 1 | 50% |" in report
    assert "| changed-default | 1 | 1 | 100% |" in report
    # A kind with no case is left out rather than printed as a row of zeroes.
    assert "removed-capability" not in report
    assert "| All | 3 | 2 | 67% |" in report


def test_the_report_carries_precision_recall_and_what_it_cost() -> None:
    score = Score(
        branches=(
            scored(id="a", found=True),
            scored(id="b", found=False),
            scored(
                id="c",
                kind="internal-refactor",
                decoy=True,
                found=False,
                spurious=(Finding(section="docs/a.md#A", chunk="a.py::f", explanation="no"),),
            ),
        ),
        unrecorded=(),
        unfinished=0,
        spend=Spend(),
    )

    report = render_score(score)

    assert "Precision 50% (1 true positive, 1 false positive)" in report
    assert "Recall 50% (1 true positive, 1 false negative)" in report
    assert "$0.0000" in report
    assert "| decoy/c | docs/a.md#A | a.py::f |" in report


def test_the_report_refuses_to_print_numbers_from_a_partial_run() -> None:
    score = Score(
        branches=(scored(id="a", found=True),),
        unrecorded=("b", "c"),
        unfinished=4,
        spend=Spend(),
    )

    report = render_score(score)

    assert "Precision" not in report
    assert "2 branches have no recorded answer" in report
    assert "    b" in report and "    c" in report
    assert "4 suspects" in report
    assert "--record" in report


def test_a_decoy_that_raises_no_suspect_is_counted_apart_from_one_the_model_cleared() -> None:
    score = Score(
        branches=(
            scored(id="a", found=True),
            scored(id="silent", kind="formatting", decoy=True, verified=0, found=False),
            scored(id="cleared", kind="internal-refactor", decoy=True, verified=1, found=False),
        ),
        unrecorded=(),
        unfinished=0,
        spend=Spend(),
    )

    report = render_score(score)

    assert (
        "1 of 2 decoys raises no suspect and cannot produce a finding; "
        "of the 1 that reaches the model, 0 did." in report
    )
    # The same split as a table, which is where the decoy half of the
    # by-kind breakdown lives.
    assert "| formatting | 1 | 0 | 0 |" in report
    assert "| internal-refactor | 1 | 1 | 0 |" in report
    assert "| All | 2 | 1 | 0 |" in report


def test_the_command_line_scores_a_corpus_from_its_recorded_answers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
        decoys={"find-keeps-its-result": {"catalogue.py": REFACTORED}},
        decoy_manifest=[FIND_REFACTORED],
    )
    model = DecidingModel(drifted=[FIND_SECTION])
    score_corpus(
        build_corpus(source, tmp_path / "built"),
        client(model, cache=source / ANSWERS),
    )

    main(["score", str(source), "--model", model.name])

    report = capsys.readouterr().out
    assert "| All | 1 | 1 | 100% |" in report
    # The decoy rewrites the body of the same function the case renames a
    # parameter of, so it raises the same section as a suspect and this model
    # calls it drift both times: one true positive and one false positive.
    assert "Precision 50% (1 true positive, 1 false positive)" in report
    assert (
        "| decoy/find-keeps-its-result | docs/catalogue.md#Catalogue > Finding books "
        "| catalogue.py::find |" in report
    )
    assert "0 model calls, $0.0000 spent." in report


def test_the_command_line_refuses_to_score_what_was_never_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED],
    )

    with pytest.raises(SystemExit) as stopped:
        main(["score", str(source)])

    assert stopped.value.code == 1
    report = capsys.readouterr().out
    assert "1 branch has no recorded answer" in report
    assert "Precision" not in report


def test_the_command_line_will_not_score_a_corpus_with_a_fault(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={"catalogue.py": CATALOGUE, "docs/catalogue.md": DOCS},
        cases={"find-query-renamed": {"catalogue.py": RENAMED}},
        manifest=[FIND_QUERY_RENAMED | {"chunk": "catalogue.py::shelve"}],
    )

    with pytest.raises(SystemExit) as stopped:
        main(["score", str(source)])

    assert stopped.value.code == 1
    # The audit's own report, not a score: a number measured over a corpus
    # that is wrong about itself would be measuring the wrong thing.
    assert "1 fault:" in capsys.readouterr().out


SHIPPED = Path(__file__).parent.parent / "corpus"


@pytest.fixture(scope="module")
def shipped(tmp_path_factory: pytest.TempPathFactory) -> Corpus:
    """The shipped corpus, built once and shared by the tests that score it."""
    return build_corpus(SHIPPED, tmp_path_factory.mktemp("shipped") / "built")


def replay(corpus: Corpus) -> Score:
    return score_corpus(corpus, ModelClient.replaying(DEFAULT_MODEL, SHIPPED / ANSWERS))


def test_every_question_the_shipped_corpus_asks_has_a_recorded_answer(
    shipped: Corpus,
) -> None:
    score = replay(shipped)

    # The guard on the published numbers. Edit a prompt, a fixture or the
    # linker and this fails rather than quietly scoring a smaller corpus.
    assert score.unrecorded == ()
    assert score.unfinished == 0
    assert score.spend == Spend()
    assert score.true_positives + score.false_negatives == 17


def test_scoring_the_shipped_corpus_twice_gives_the_same_numbers(
    shipped: Corpus,
) -> None:
    assert replay(shipped) == replay(shipped)


def test_the_shipped_corpus_scores_what_the_readme_publishes(shipped: Corpus) -> None:
    score = replay(shipped)

    # The README quotes these. Pinning them here is what keeps the two from
    # drifting apart: change the prompt, the model or the linker and this
    # fails, which is the moment to re-record and rewrite the paragraph.
    assert score.true_positives == 10
    assert score.false_negatives == 7
    assert score.false_positives == 0
    assert score.recall == 10 / 17
    assert score.precision == 1.0

    reached = [
        result for result in score.branches if result.decoy and result.verified
    ]
    assert len(reached) == 7
    assert all(result.spurious == () for result in reached)
