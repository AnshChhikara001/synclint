import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from textwrap import dedent

import pytest

from synclint.__main__ import ANSWERS, EMBEDDINGS, main, render_links, render_score
from synclint.analyse import Finding
from synclint.corpus import Corpus, build_corpus
from synclint.index import DEFAULT_SIMILARITY_THRESHOLD
from synclint.embeddings import DEFAULT_EMBEDDING_MODEL, Embedded, EmbeddingClient
from synclint.model import DEFAULT_MODEL, ModelClient, ModelResponse, Pricing, Spend
from synclint.score import (
    LinkRecall,
    Linking,
    RepairOutcome,
    Score,
    Scored,
    measure_links,
    score_corpus,
)

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
    "repair_says": ["find(text)"],
    "repair_drops": ["find(query)"],
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


class DecidingModel:
    """Calls a section drifted when the question names something it was given.

    A marker is any text the prompt carries — a section id, a chunk id, an
    identifier out of the code — so a test can fail one suspect and clear the
    others without knowing the order they are asked in.

    Asked for a repair, it makes every edit in `edits` whose quote the section
    contains. Asked to validate one, it rejects the repair when the question
    names anything in `rejected`. Asked to score one, it is 95% confident
    unless the question names anything in `doubted`, when it is 50%.
    """

    name = "deciding-model"
    max_output_tokens = 500

    def __init__(
        self,
        drifted: Iterable[str] = (),
        edits: Iterable[tuple[str, str]] = (),
        rejected: Iterable[str] = (),
        doubted: Iterable[str] = (),
    ) -> None:
        self._drifted = tuple(drifted)
        self._edits = tuple(edits)
        self._rejected = tuple(rejected)
        self._doubted = tuple(doubted)
        self.asked: list[str] = []

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse:
        properties = schema["properties"]
        assert isinstance(properties, dict)
        answer: dict[str, object]
        if "edits" in properties:
            answer = {
                "edits": [
                    {"find": find, "replace": replace}
                    for find, replace in self._edits
                    if find in user
                ]
            }
        elif "valid" in properties:
            rejected = any(marker in user for marker in self._rejected)
            answer = {"valid": not rejected, "reason": "no" if rejected else ""}
        elif "confidence" in properties:
            doubted = any(marker in user for marker in self._doubted)
            answer = {"confidence": 0.5 if doubted else 0.95}
        else:
            self.asked.append(user)
            drifted = any(marker in user for marker in self._drifted)
            answer = {
                "accurate": not drifted,
                "explanation": "the parameter is called something else" if drifted else "",
            }
        return ModelResponse(text=json.dumps(answer), input_tokens=10, output_tokens=5)


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


def planted(score: Score) -> Scored:
    (branch,) = [branch for branch in score.branches if not branch.decoy]
    return branch


def test_a_repair_the_ground_truth_accepts_and_validation_passes_is_proposed_and_correct(
    tmp_path: Path,
) -> None:
    model = DecidingModel(drifted=[FIND_SECTION], edits=[("find(query)", "find(text)")])
    source, asked = one_case(tmp_path, model)

    repair = planted(score_corpus(build_corpus(source, tmp_path / "built"), asked)).repair

    assert repair is not None
    assert repair.proposed
    assert repair.correct
    assert repair.shape == "renamed-parameter"
    assert repair.confidence == 0.95
    # One word of a fifty-two character section rewritten: "query" out.
    assert repair.kept == pytest.approx(47 / 52)


def test_a_repair_that_misses_the_drift_is_incorrect_even_when_validation_passes_it(
    tmp_path: Path,
) -> None:
    model = DecidingModel(drifted=[FIND_SECTION], edits=[("twenty", "thirty")])
    source, asked = one_case(tmp_path, model)

    repair = planted(score_corpus(build_corpus(source, tmp_path / "built"), asked)).repair

    assert repair is not None
    assert repair.proposed
    assert not repair.correct


def test_a_correct_repair_validation_rejects_is_flagged_and_still_judged(
    tmp_path: Path,
) -> None:
    model = DecidingModel(
        drifted=[FIND_SECTION],
        edits=[("find(query)", "find(text)")],
        rejected=["find(text)"],
    )
    source, asked = one_case(tmp_path, model)

    repair = planted(score_corpus(build_corpus(source, tmp_path / "built"), asked)).repair

    assert repair is not None
    assert not repair.proposed
    assert repair.cause == "refused"
    assert repair.correct


def test_a_correct_repair_the_model_doubts_is_flagged_and_still_judged(
    tmp_path: Path,
) -> None:
    model = DecidingModel(
        drifted=[FIND_SECTION],
        edits=[("find(query)", "find(text)")],
        doubted=["find(text)"],
    )
    source, asked = one_case(tmp_path, model)

    repair = planted(score_corpus(build_corpus(source, tmp_path / "built"), asked)).repair

    assert repair is not None
    assert repair.cause == "doubted"
    assert repair.confidence == 0.5
    assert repair.correct


def test_the_threshold_a_corpus_is_scored_at_is_the_callers_to_set(
    tmp_path: Path,
) -> None:
    model = DecidingModel(
        drifted=[FIND_SECTION],
        edits=[("find(query)", "find(text)")],
        doubted=["find(text)"],
    )
    source, asked = one_case(tmp_path, model)

    score = score_corpus(build_corpus(source, tmp_path / "built"), asked, threshold=0.4)

    assert score.threshold == 0.4
    repair = planted(score).repair
    assert repair is not None and repair.proposed


def test_a_repair_that_could_not_be_applied_is_neither_proposed_nor_correct(
    tmp_path: Path,
) -> None:
    model = DecidingModel(drifted=[FIND_SECTION])
    source, asked = one_case(tmp_path, model)

    repair = planted(score_corpus(build_corpus(source, tmp_path / "built"), asked)).repair

    assert repair == RepairOutcome(
        shape="renamed-parameter",
        cause="unappliable",
        confidence=None,
        correct=False,
        kept=None,
    )


def test_a_planted_case_that_was_never_found_has_no_repair_to_score(
    tmp_path: Path,
) -> None:
    source, asked = one_case(tmp_path, DecidingModel())

    assert planted(score_corpus(build_corpus(source, tmp_path / "built"), asked)).repair is None


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
        "repair": None,
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


def outcome(**fields: object) -> RepairOutcome:
    defaults: dict[str, object] = {
        "shape": "renamed-parameter",
        "cause": None,
        "confidence": 0.95,
        "correct": True,
        "kept": 0.9,
    }
    return RepairOutcome(**{**defaults, **fields})  # type: ignore[arg-type]


REPAIRED = Score(
    branches=(
        scored(id="a", repair=outcome()),
        scored(id="b", repair=outcome(correct=False, kept=0.8)),
        scored(id="c", repair=outcome(cause="refused", confidence=None, kept=0.95)),
        scored(
            id="d",
            repair=outcome(cause="unappliable", confidence=None, correct=False, kept=None),
        ),
        scored(
            id="e",
            repair=outcome(
                shape="changed-default", cause="doubted", confidence=0.5, kept=0.7
            ),
        ),
        scored(
            id="f",
            kind="removed-capability",
            repair=outcome(shape=None, cause="outside", confidence=None, correct=False),
        ),
        scored(id="g", found=False),
    ),
    unrecorded=(),
    unfinished=0,
    spend=Spend(),
    threshold=0.9,
)


def test_the_report_sets_every_repairs_outcome_beside_the_ground_truth() -> None:
    report = render_score(REPAIRED)

    assert "| a | renamed-parameter | proposed | 95% | correct | 90% |" in report
    assert "| b | renamed-parameter | proposed | 95% | incorrect | 80% |" in report
    assert "| c | renamed-parameter | refused by validation | — | correct | 95% |" in report
    assert "| d | renamed-parameter | not applied | — | not applied | — |" in report
    assert "| e | changed-default | below threshold | 50% | correct | 70% |" in report
    assert "| f | — | outside the gate | — | incorrect | 90% |" in report
    assert "| g |" not in report
    # An edit that could not be applied never reached validation, so it is
    # counted apart rather than laid at validation's door.
    assert (
        "6 repairs, 1 of which could not be applied. Validation passed 4 of the "
        "5 it saw; of those, 1 was outside the gate and 1 fell short of the 90% "
        "threshold. 2 were proposed, 1 of them correct." in report
    )


def test_the_report_carries_a_calibration_table_for_each_eligible_shape() -> None:
    report = render_score(REPAIRED)

    # Every shape the gate admits gets a row, and so does everything outside
    # it: the comparison is what says whether the gate earns its keep. A
    # repair that could not be applied counts, as one that was not correct.
    assert "| Shape | Repairs | Correct | Proposed at ≥ 90% | Proposed and correct |" in report
    assert "| renamed-parameter | 4 | 2 (50%) | 2 | 1 |" in report
    assert "| changed-default | 1 | 1 (100%) | 0 | 0 |" in report
    assert "| outside the gate | 1 | 0 (0%) | 0 | 0 |" in report


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
    record_embeddings(source, tmp_path / "embedded")

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
    assert "| name | 1 | 1 of 1 | 100% | 1 | 1 |" in report
    assert "0 calls this run." in report


def record_embeddings(source: Path, built: Path) -> None:
    """Record the corpus's embeddings where the command line will replay them from."""

    class Named(TopicEmbedder):
        name = DEFAULT_EMBEDDING_MODEL

    measure_links(
        build_corpus(source, built),
        EmbeddingClient(Named(), price=0.02, cache=source / EMBEDDINGS),
        threshold=0.9,
    )


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


def test_the_command_line_says_so_when_the_corpus_was_never_embedded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = DecidingModel()
    source, _ = one_case(tmp_path, model)
    score_corpus(
        build_corpus(source, tmp_path / "built"), client(model, cache=source / ANSWERS)
    )

    with pytest.raises(SystemExit) as stopped:
        main(["score", str(source), "--model", model.name])

    assert stopped.value.code == 1
    report = capsys.readouterr().out
    assert "has a recorded embedding, so there is no link recall" in report
    assert "| name |" not in report


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
    assert score.true_positives + score.false_negatives == 18


def test_scoring_the_shipped_corpus_twice_gives_the_same_numbers(
    shipped: Corpus,
) -> None:
    assert replay(shipped) == replay(shipped)


def test_the_shipped_corpus_scores_what_the_readme_publishes(shipped: Corpus) -> None:
    score = replay(shipped)

    # The README quotes these. Pinning them here is what keeps the two from
    # drifting apart: change the prompt, the model or the linker and this
    # fails, which is the moment to re-record and rewrite the paragraph.
    # One of the eleven is the deleted chunk, found without asking the model.
    assert score.true_positives == 11
    assert score.false_negatives == 7
    assert score.false_positives == 0
    assert score.recall == 11 / 18
    assert score.precision == 1.0

    reached = [
        result for result in score.branches if result.decoy and result.verified
    ]
    assert len(reached) == 7
    assert all(result.spurious == () for result in reached)

    repairs = {branch.id: branch.repair for branch in score.branches if branch.repair}
    assert len(repairs) == 10
    proposed = {case for case, repair in repairs.items() if repair.proposed}
    # Validation passed seven, and the gate held back the four whose change is
    # neither a renamed parameter nor a changed default. One of those four is
    # the only incorrect repair validation let through.
    assert proposed == {
        "write-json-path-renamed",
        "write-csv-columns-default",
        "matches-case-sensitive-default",
    }
    assert all(repairs[case].correct for case in proposed)
    outside = {case for case, repair in repairs.items() if repair.cause == "outside"}
    assert outside == {
        "load-drops-create-missing",
        "overdue-drops-grace",
        "titles-drops-sort",
        "is-valid-gains-isbn10",
    }
    assert [case for case in outside if not repairs[case].correct] == [
        "is-valid-gains-isbn10"
    ]
    # The model's score fell short for none of them: all three sat at 97% or
    # 98%, so on this corpus the rules did the work and the threshold none.
    assert all(
        (repairs[case].confidence or 0) >= 0.97 for case in proposed
    )
    # The other three had edits that could not be applied at all.
    assert all(
        repair.kept is None
        for case, repair in repairs.items()
        if case not in proposed | outside
    )


class TopicEmbedder:
    """Places a text on one axis per topic word it contains.

    Every text in `two_cases` carries at least one topic, so none embeds as the
    zero vector: the catalogue page and `find` on `query`, the shelves page and
    `Shelf.__init__` on `capacity`, and the class itself on `class`.
    """

    name = "topic-embedder"
    TOPICS = ("capacity", "query", "class")

    def embed(self, text: str) -> Embedded:
        vector = [float(topic in text) for topic in self.TOPICS]
        return Embedded(vector=vector, tokens=len(text.split()))


SHELF = '''
    class Shelf:
        def __init__(self, capacity=50):
            self.capacity = capacity
    '''

SHELF_SHRUNK = '''
    class Shelf:
        def __init__(self, capacity=40):
            self.capacity = capacity
    '''

SHELF_DOCS = """
    # Shelves

    A new shelf takes fifty books unless you give it another capacity.
    """

SHELF_SECTION = "docs/shelves.md#Shelves"

SHELF_CAPACITY_DEFAULT = {
    "id": "shelf-capacity-default",
    "kind": "changed-default",
    "section": SHELF_SECTION,
    "chunk": "shelves.py::Shelf.__init__",
    "description": "a shelf holds forty books by default",
    "repair_says": ["forty"],
    "repair_drops": ["fifty"],
}


def two_cases(tmp_path: Path) -> Corpus:
    """One case name matching reaches, and one only embedding similarity does."""
    source = tmp_path / "corpus"
    write_corpus(
        source,
        base={
            "catalogue.py": CATALOGUE,
            "shelves.py": SHELF,
            "docs/catalogue.md": DOCS,
            "docs/shelves.md": SHELF_DOCS,
        },
        cases={
            "find-query-renamed": {"catalogue.py": RENAMED},
            "shelf-capacity-default": {"shelves.py": SHELF_SHRUNK},
        },
        manifest=[FIND_QUERY_RENAMED, SHELF_CAPACITY_DEFAULT],
        decoys={"find-keeps-its-result": {"catalogue.py": REFACTORED}},
        decoy_manifest=[FIND_REFACTORED],
    )
    return build_corpus(source, tmp_path / "built")


def test_link_recall_is_measured_by_name_alone_and_with_embeddings(
    tmp_path: Path,
) -> None:
    corpus = two_cases(tmp_path)
    embeddings = EmbeddingClient(TopicEmbedder(), price=0.02)

    measured = measure_links(corpus, embeddings, threshold=0.9)

    # Name matching reaches `find`, which the page names; only the embeddings
    # reach `Shelf.__init__`, which it describes and never names.
    assert measured.by_name.linked == ("find-query-renamed",)
    assert measured.by_name.unlinked == ("shelf-capacity-default",)
    assert measured.by_name.recall == 0.5
    assert measured.with_embeddings.linked == (
        "find-query-renamed",
        "shelf-capacity-default",
    )
    assert measured.with_embeddings.recall == 1.0


def test_link_recall_counts_what_the_extra_links_cost_in_suspects(
    tmp_path: Path,
) -> None:
    corpus = two_cases(tmp_path)
    embeddings = EmbeddingClient(TopicEmbedder(), price=0.02)

    measured = measure_links(corpus, embeddings, threshold=0.9)

    # Embeddings propose the pair name matching already had, which is not a
    # new pair, and the shelf pair, which is — and which the shelf case then
    # raises as a suspect. The decoy touches `find` and nothing else, so it
    # raises the same one suspect either way.
    assert measured.by_name.pairs == 1
    assert measured.with_embeddings.pairs == 2
    assert (measured.by_name.case_suspects, measured.by_name.decoy_suspects) == (1, 1)
    assert (
        measured.with_embeddings.case_suspects,
        measured.with_embeddings.decoy_suspects,
    ) == (2, 1)


def test_link_recall_reports_what_embedding_the_corpus_cost(tmp_path: Path) -> None:
    corpus = two_cases(tmp_path)
    embeddings = EmbeddingClient(TopicEmbedder(), price=0.02)

    measured = measure_links(corpus, embeddings, threshold=0.9)

    assert measured.embedding_calls > 0
    assert measured.embedding_tokens == embeddings.tokens
    assert measured.embedding_dollars == pytest.approx(
        embeddings.tokens * 0.02 / 1_000_000
    )


def test_the_link_report_sets_both_mechanisms_side_by_side() -> None:
    measured = LinkRecall(
        threshold=0.55,
        by_name=Linking(
            pairs=48,
            linked=("a", "b"),
            unlinked=("c",),
            case_suspects=20,
            decoy_suspects=11,
        ),
        with_embeddings=Linking(
            pairs=84,
            linked=("a", "b", "c"),
            unlinked=(),
            case_suspects=36,
            decoy_suspects=15,
        ),
        embedding_calls=0,
        embedding_tokens=2400,
        embedding_dollars=0.000048,
    )

    report = render_links(measured)

    assert "| name | 48 | 2 of 3 | 67% | 20 | 11 |" in report
    assert "| name + embedding ≥ 0.55 | 84 | 3 of 3 | 100% | 36 | 15 |" in report
    assert "2400 tokens, $0.000048" in report


def test_the_shipped_corpus_links_what_the_readme_publishes(shipped: Corpus) -> None:
    embeddings = EmbeddingClient.replaying(DEFAULT_EMBEDDING_MODEL, SHIPPED / EMBEDDINGS)

    measured = measure_links(shipped, embeddings, DEFAULT_SIMILARITY_THRESHOLD)

    # Every vector is recorded, so this is free; and the README quotes the rest.
    # Change the threshold, the embedded text or the fixture and this fails,
    # which is the moment to re-measure and rewrite the paragraph.
    assert measured.embedding_calls == 0
    assert measured.embedding_tokens == 2400
    assert measured.by_name.unlinked == ("shelf-capacity-default", "catalogue-gains-merge")
    assert measured.with_embeddings.unlinked == ("catalogue-gains-merge",)
    assert (measured.by_name.pairs, measured.with_embeddings.pairs) == (48, 84)
    assert (measured.by_name.case_suspects, measured.by_name.decoy_suspects) == (20, 11)
    assert (
        measured.with_embeddings.case_suspects,
        measured.with_embeddings.decoy_suspects,
    ) == (36, 15)


@pytest.mark.parametrize("threshold", ["-0.1", "1.5"])
def test_a_confidence_threshold_outside_nought_to_one_is_refused(threshold: str) -> None:
    with pytest.raises(SystemExit):
        main(["score", "corpus", "--confidence-threshold", threshold])
