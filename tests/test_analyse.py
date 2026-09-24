import json
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from synclint.__main__ import render
from synclint.analyse import Finding, Flag, Repair, Report, analyse, suspects
from synclint.index import Index, build_index
from synclint.model import ModelClient, ModelResponse, Pricing, Spend

PRICING = Pricing(input=1.00, output=2.00)


class ScriptedModel:
    """Answers each question the way a test needs it answered.

    Which question is being asked is read off the answer's schema, the one
    thing about a question that a reworded prompt leaves alone. `edits` is what
    a repair quotes and replaces, and `rejection` is what validation refuses a
    repair with; empty means it passes. `confidence` is the score it gives a
    repair inside the gate, and `rated` collects every repair it was asked to
    score.
    """

    name = "scripted-model"

    def __init__(
        self,
        accurate: bool,
        explanation: str = "",
        *,
        edits: Sequence[tuple[str, str]] = (),
        rejection: str = "",
        confidence: float = 0.95,
        input_tokens: int = 100,
        output_tokens: int = 20,
        max_output_tokens: int = 500,
    ) -> None:
        self.accurate = accurate
        self.explanation = explanation
        self.edits = edits
        self.rejection = rejection
        self.confidence = confidence
        self.rated: list[str] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.max_output_tokens = max_output_tokens
        self.asked: list[str] = []

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse:
        properties = schema["properties"]
        assert isinstance(properties, dict)
        answer: dict[str, object]
        if "edits" in properties:
            answer = {"edits": [{"find": f, "replace": r} for f, r in self.edits]}
        elif "valid" in properties:
            answer = {"valid": not self.rejection, "reason": self.rejection}
        elif "confidence" in properties:
            self.rated.append(user)
            answer = {"confidence": self.confidence}
        else:
            self.asked.append(user)
            answer = {"accurate": self.accurate, "explanation": self.explanation}
        return ModelResponse(
            text=json.dumps(answer),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


def client(model: ScriptedModel) -> ModelClient:
    return ModelClient(model, pricing=PRICING, ceiling=1.00)


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "synclint",
            "GIT_AUTHOR_EMAIL": "synclint@example.com",
            "GIT_COMMITTER_NAME": "synclint",
            "GIT_COMMITTER_EMAIL": "synclint@example.com",
        },
    )


def commit(root: Path, files: dict[str, str], message: str) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).lstrip())
    git(root, "add", "-A")
    git(root, "commit", "-m", message)


def start(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    commit(root, files, "base")


FETCHING_DOCS = """
    # Fetching

    Call `fetch(url)`. It gives up after three attempts.
    """

BASE_SOURCE = """
    def fetch(url, retries=3):
        return url
    """

DRIFTED_SOURCE = """
    def fetch(url, retries=5):
        return url
    """


def test_reports_a_section_the_model_finds_no_longer_accurate(tmp_path: Path) -> None:
    start(tmp_path, {"src/http.py": BASE_SOURCE, "README.md": FETCHING_DOCS})
    index = build_index(tmp_path)
    commit(tmp_path, {"src/http.py": DRIFTED_SOURCE}, "raise the retry limit")

    model = ScriptedModel(accurate=False, explanation="It now gives up after five.")
    report = analyse(tmp_path, index, "main~1", "main", client(model))

    assert [(f.section, f.chunk, f.explanation) for f in report.findings] == [
        ("README.md#Fetching", "src/http.py::fetch", "It now gives up after five.")
    ]
    assert report.verified == ("README.md#Fetching",)


def test_a_section_the_model_finds_accurate_is_checked_but_not_reported(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"src/http.py": BASE_SOURCE, "README.md": FETCHING_DOCS})
    index = build_index(tmp_path)
    commit(tmp_path, {"src/http.py": DRIFTED_SOURCE}, "raise the retry limit")

    report = analyse(tmp_path, index, "main~1", "main", client(ScriptedModel(True)))

    assert report.findings == ()
    assert report.verified == ("README.md#Fetching",)


RETRIES_DOCS = """
    # Fetching

    Call `fetch(url)` to download a page. It gives up after three attempts,
    so a flaky server costs you at most three requests.
    """


def drifted_retries(tmp_path: Path) -> Index:
    start(tmp_path, {"src/http.py": BASE_SOURCE, "README.md": RETRIES_DOCS})
    index = build_index(tmp_path)
    commit(tmp_path, {"src/http.py": DRIFTED_SOURCE}, "raise the retry limit")
    return index


def test_a_finding_is_repaired_by_rewriting_only_what_drifted(tmp_path: Path) -> None:
    index = drifted_retries(tmp_path)
    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five attempts.",
        edits=[("after three attempts", "after five attempts"), ("most three", "most five")],
    )

    report = analyse(tmp_path, index, "main~1", "main", client(model))

    (repair,) = report.repairs
    assert repair.finding == report.findings[0]
    assert repair.repaired == (
        "Call `fetch(url)` to download a page. It gives up after five attempts,\n"
        "so a flaky server costs you at most five requests."
    )
    assert report.flags == ()


def test_a_repair_that_fails_validation_is_flagged_with_the_reason(
    tmp_path: Path,
) -> None:
    index = drifted_retries(tmp_path)
    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five attempts.",
        edits=[("after three attempts", "after five attempts")],
        rejection="It still says a flaky server costs three requests.",
    )

    report = analyse(tmp_path, index, "main~1", "main", client(model))

    assert report.repairs == ()
    (flag,) = report.flags
    assert flag.finding == report.findings[0]
    assert flag.reason == "It still says a flaky server costs three requests."
    assert flag.attempt is not None and "after five attempts" in flag.attempt


def test_a_repair_inside_the_gate_carries_its_shape_and_the_models_confidence(
    tmp_path: Path,
) -> None:
    index = drifted_retries(tmp_path)
    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five attempts.",
        edits=[("after three attempts", "after five attempts"), ("most three", "most five")],
        confidence=0.95,
    )

    report = analyse(tmp_path, index, "main~1", "main", client(model))

    (repair,) = report.repairs
    assert repair.shape == "changed-default"
    assert repair.confidence == 0.95
    assert len(model.rated) == 1
    assert "after five attempts" in model.rated[0]


def test_a_repair_the_model_is_not_confident_enough_in_is_flagged_saying_so(
    tmp_path: Path,
) -> None:
    index = drifted_retries(tmp_path)
    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five attempts.",
        edits=[("after three attempts", "after five attempts"), ("most three", "most five")],
        confidence=0.6,
    )

    report = analyse(tmp_path, index, "main~1", "main", client(model), threshold=0.9)

    assert report.repairs == ()
    (flag,) = report.flags
    assert flag.cause == "doubted"
    assert flag.shape == "changed-default"
    assert flag.confidence == 0.6
    assert "60%" in flag.reason and "90%" in flag.reason
    assert flag.attempt is not None and "after five attempts" in flag.attempt


def test_the_confidence_threshold_is_the_callers_to_set(tmp_path: Path) -> None:
    index = drifted_retries(tmp_path)
    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five attempts.",
        edits=[("after three attempts", "after five attempts"), ("most three", "most five")],
        confidence=0.6,
    )

    report = analyse(tmp_path, index, "main~1", "main", client(model), threshold=0.5)

    assert len(report.repairs) == 1
    assert report.flags == ()


def test_a_change_outside_the_gate_is_flagged_however_confident_the_model_is(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"src/http.py": BASE_SOURCE, "README.md": RETRIES_DOCS})
    index = build_index(tmp_path)
    # The default moves and the body changes with it: no longer one narrow shape.
    commit(
        tmp_path,
        {
            "src/http.py": """
            def fetch(url, retries=5):
                return url.strip()
            """
        },
        "raise the retry limit and tidy the url",
    )
    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five attempts.",
        edits=[("after three attempts", "after five attempts"), ("most three", "most five")],
        confidence=1.0,
    )

    report = analyse(tmp_path, index, "main~1", "main", client(model), threshold=0.0)

    assert report.repairs == ()
    (flag,) = report.flags
    assert flag.cause == "outside"
    assert flag.shape is None
    assert flag.confidence is None
    assert "parameters and body" in flag.reason
    # The rewrite is still shown to whoever reads the flag, but the model was
    # never asked to score it: inside the gate is the only place a score counts.
    assert flag.attempt is not None
    assert model.rated == []


def test_a_repair_validation_refuses_is_never_scored(tmp_path: Path) -> None:
    index = drifted_retries(tmp_path)
    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five attempts.",
        edits=[("after three attempts", "after five attempts")],
        rejection="It still says three requests.",
    )

    report = analyse(tmp_path, index, "main~1", "main", client(model))

    (flag,) = report.flags
    assert flag.cause == "refused"
    assert flag.shape == "changed-default"
    assert model.rated == []


@pytest.mark.parametrize(
    "edits",
    [
        [("after four attempts", "after five attempts")],
        [("three", "five")],
        [("after three attempts", "after five"), ("three attempts,", "five,")],
        [("three attempts", "three attempts")],
        [],
    ],
    ids=["not-in-section", "ambiguous", "overlapping", "no-change", "no-edits"],
)
def test_a_repair_that_cannot_be_applied_is_flagged_rather_than_guessed_at(
    tmp_path: Path, edits: list[tuple[str, str]]
) -> None:
    index = drifted_retries(tmp_path)
    model = ScriptedModel(accurate=False, explanation="Five now.", edits=edits)

    report = analyse(tmp_path, index, "main~1", "main", client(model))

    assert report.repairs == ()
    (flag,) = report.flags
    assert flag.finding == report.findings[0]
    assert flag.attempt is None


def test_a_run_that_hits_its_ceiling_while_repairing_flags_what_it_could_not_repair(
    tmp_path: Path,
) -> None:
    index = drifted_retries(tmp_path)
    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five.",
        edits=[("after three attempts", "after five attempts")],
        input_tokens=100_000,
        output_tokens=10_000,
        max_output_tokens=10_000,
    )
    # The same arithmetic as the verification ceiling test: one $0.12 call
    # fits under $0.13, and the repair that would follow it does not.
    report = analyse(
        tmp_path,
        index,
        "main~1",
        "main",
        ModelClient(model, pricing=PRICING, ceiling=0.13),
    )

    assert len(report.findings) == 1
    assert report.unchecked == 0
    assert report.unrepaired == 1
    assert report.repairs == ()
    (flag,) = report.flags
    assert "spend ceiling" in flag.reason
    assert "1 finding unrepaired" in render(report)


def test_a_repair_reads_as_a_diff_against_the_original_section() -> None:
    repair = Repair(
        finding=Finding("README.md#Fetching", "src/http.py::fetch", "Five now."),
        original="Call `fetch(url)`.\nIt gives up after three attempts.",
        repaired="Call `fetch(url)`.\nIt gives up after five attempts.",
        shape="changed-default",
        confidence=0.95,
    )

    assert repair.diff == (
        "--- README.md#Fetching\n"
        "+++ README.md#Fetching\n"
        "@@ -1,2 +1,2 @@\n"
        " Call `fetch(url)`.\n"
        "-It gives up after three attempts.\n"
        "+It gives up after five attempts.\n"
    )


def test_reformatting_and_comment_edits_produce_no_suspects(tmp_path: Path) -> None:
    start(tmp_path, {"src/http.py": BASE_SOURCE, "README.md": FETCHING_DOCS})
    index = build_index(tmp_path)
    commit(
        tmp_path,
        {
            "src/http.py": """
            def fetch(
                url,
                retries=3,
            ):
                # Three is plenty.
                return url
            """
        },
        "tidy up",
    )

    model = ScriptedModel(accurate=False, explanation="never asked")
    report = analyse(tmp_path, index, "main~1", "main", client(model))

    assert report.verified == ()
    assert model.asked == []


def test_a_change_confined_to_test_files_produces_no_suspects(tmp_path: Path) -> None:
    start(
        tmp_path,
        {
            "src/http.py": BASE_SOURCE,
            "README.md": FETCHING_DOCS,
            "tests/test_http.py": "def fetch():\n    return 3\n",
        },
    )
    index = build_index(tmp_path)
    commit(tmp_path, {"tests/test_http.py": "def fetch():\n    return 5\n"}, "fix test")

    model = ScriptedModel(accurate=False, explanation="never asked")
    report = analyse(tmp_path, index, "main~1", "main", client(model))

    assert report.verified == ()
    assert model.asked == []


def test_asks_about_the_body_of_the_touched_chunk_and_no_other(tmp_path: Path) -> None:
    start(
        tmp_path,
        {
            "src/http.py": """
            def fetch(url, retries=3):
                return url

            def backoff(attempt):
                return SECONDS_BETWEEN_ATTEMPTS * attempt
            """,
            "README.md": """
            # Fetching

            Call `fetch(url)`, which waits between attempts using `backoff`.
            """,
        },
    )
    index = build_index(tmp_path)
    commit(
        tmp_path,
        {
            "src/http.py": """
            def fetch(url, retries=5):
                return url

            def backoff(attempt):
                return SECONDS_BETWEEN_ATTEMPTS * attempt
            """
        },
        "raise the retry limit",
    )

    model = ScriptedModel(accurate=True)
    analyse(tmp_path, index, "main~1", "main", client(model))

    (question,) = model.asked
    assert "def fetch(url, retries=5)" in question
    assert "SECONDS_BETWEEN_ATTEMPTS" not in question


def test_reports_what_the_run_consumed(tmp_path: Path) -> None:
    start(tmp_path, {"src/http.py": BASE_SOURCE, "README.md": FETCHING_DOCS})
    index = build_index(tmp_path)
    commit(tmp_path, {"src/http.py": DRIFTED_SOURCE}, "raise the retry limit")

    report = analyse(tmp_path, index, "main~1", "main", client(ScriptedModel(True)))

    # 100 in at $1.00/M is $0.0001, 20 out at $2.00/M is $0.00004.
    assert report.spend.calls == 1
    assert report.spend.input_tokens == 100
    assert report.spend.dollars == pytest.approx(0.00014)


def test_the_command_line_prints_the_findings_and_what_they_cost() -> None:
    repaired = Finding(
        section="README.md#Fetching",
        chunk="src/http.py::fetch",
        explanation="It now gives up after five attempts, not three.",
    )
    flagged = Finding(
        section="docs/guide.md#Retries",
        chunk="src/http.py::fetch",
        explanation="It retries five times, not three.",
    )
    report = Report(
        findings=(repaired, flagged),
        verified=("README.md#Fetching", "docs/guide.md#Retries"),
        unchecked=0,
        unrepaired=0,
        repairs=(
            Repair(
                finding=repaired,
                original="It gives up after three attempts.",
                repaired="It gives up after five attempts.",
                shape="changed-default",
                confidence=0.95,
            ),
        ),
        flags=(
            Flag(
                finding=flagged,
                reason="The repair dropped the note about backoff.",
                original="`fetch` retries three times, backing off between them.",
                attempt="`fetch` retries five times.",
                cause="refused",
            ),
        ),
        spend=Spend(calls=2, input_tokens=1200, output_tokens=140, dollars=0.00148),
    )

    printed = render(report)

    assert "Verified 2 sections; 2 have drifted." in printed
    assert "README.md#Fetching  (src/http.py::fetch)" in printed
    assert "It now gives up after five attempts, not three." in printed
    assert "    -It gives up after three attempts.\n    +It gives up after five attempts." in printed
    assert "Proposed: a changed-default, 95% confident." in printed
    assert "Flagged: The repair dropped the note about backoff." in printed
    assert "2 model calls, 1200 tokens in, 140 out, $0.0015 spent." in printed


def test_the_command_line_says_so_when_nothing_drifted() -> None:
    printed = render(
        Report(
            findings=(),
            verified=("README.md#Fetching",),
            unchecked=0,
            unrepaired=0,
            repairs=(),
            flags=(),
            spend=Spend(),
        )
    )

    assert "Verified 1 section; 0 have drifted." in printed


def test_a_run_that_hits_its_ceiling_keeps_what_it_already_paid_for(
    tmp_path: Path,
) -> None:
    start(
        tmp_path,
        {
            "src/http.py": """
            def fetch(url, retries=3):
                return url
            """,
            "README.md": """
            # Fetching

            Call `fetch(url)`. It gives up after three attempts.

            ## Retrying

            `fetch` retries three times.
            """,
        },
    )
    index = build_index(tmp_path)
    commit(tmp_path, {"src/http.py": DRIFTED_SOURCE}, "raise the retry limit")

    model = ScriptedModel(
        accurate=False,
        explanation="It now gives up after five.",
        input_tokens=100_000,
        output_tokens=10_000,
        max_output_tokens=10_000,
    )
    # An answered call costs $0.12 — 100k in at $1.00/M, 10k out at $2.00/M —
    # while reserving one costs $0.02. One call fits under the ceiling and a
    # second does not, by a margin far wider than the prompt's own length.
    client = ModelClient(model, pricing=PRICING, ceiling=0.13)
    report = analyse(tmp_path, index, "main~1", "main", client)

    assert len(report.findings) == 1
    assert len(report.verified) == 1
    assert report.unchecked == 1
    assert "Stopped at the spend ceiling with 1 suspect unverified" in render(report)


def test_survives_a_modified_file_that_does_not_parse(tmp_path: Path) -> None:
    start(
        tmp_path,
        {
            "src/http.py": BASE_SOURCE,
            "src/legacy.py": "def fetch_old(url):\n    return url\n",
            "README.md": FETCHING_DOCS,
        },
    )
    index = build_index(tmp_path)
    commit(
        tmp_path,
        {"src/http.py": DRIFTED_SOURCE, "src/legacy.py": "print 'python 2'\n"},
        "raise the retry limit and touch the old file",
    )

    model = ScriptedModel(accurate=False, explanation="It now gives up after five.")
    report = analyse(tmp_path, index, "main~1", "main", client(model))

    assert [finding.chunk for finding in report.findings] == ["src/http.py::fetch"]


def test_a_method_change_is_put_to_the_model_once_not_twice(tmp_path: Path) -> None:
    start(
        tmp_path,
        {
            "src/http.py": """
            class Client:
                def retries(self):
                    return 3
            """,
            "README.md": """
            # Fetching

            `Client` gives up after three attempts; see `retries`.
            """,
        },
    )
    index = build_index(tmp_path)
    # The section names both `Client` and `retries`, so the index links it to
    # both chunks — but only one of them changed.
    assert len(index.links) == 2
    commit(
        tmp_path,
        {
            "src/http.py": """
            class Client:
                def retries(self):
                    return 5
            """
        },
        "raise the retry limit",
    )

    model = ScriptedModel(accurate=False, explanation="It now gives up after five.")
    report = analyse(tmp_path, index, "main~1", "main", client(model))

    assert len(model.asked) == 1
    assert [finding.chunk for finding in report.findings] == [
        "src/http.py::Client.retries"
    ]
    assert "1 has drifted" in render(report)


def test_suspects_pair_each_changed_chunk_with_the_sections_linked_to_it(
    tmp_path: Path,
) -> None:
    start(
        tmp_path,
        {
            "src/http.py": """
            def fetch(url, retries=3):
                return url

            def backoff(attempt):
                return attempt
            """,
            "README.md": """
            # Fetching

            Call `fetch(url)`. It gives up after three attempts.

            ## Waiting

            `fetch` waits between attempts using `backoff`.
            """,
        },
    )
    index = build_index(tmp_path)
    commit(tmp_path, {"src/http.py": DRIFTED_SOURCE}, "raise the retry limit")

    found = suspects(tmp_path, index, "main~1", "main")

    assert [(suspect.section.id, suspect.change.chunk) for suspect in found] == [
        ("README.md#Fetching", "src/http.py::fetch"),
        ("README.md#Fetching > Waiting", "src/http.py::fetch"),
    ]


def test_a_pair_linked_by_both_mechanisms_is_one_suspect(tmp_path: Path) -> None:
    start(tmp_path, {"src/http.py": BASE_SOURCE, "README.md": FETCHING_DOCS})
    named = build_index(tmp_path)
    # What an index built with embeddings holds when both mechanisms agree.
    index = replace(
        named,
        links=named.links
        + tuple(replace(link, mechanism="embedding") for link in named.links),
    )
    commit(tmp_path, {"src/http.py": DRIFTED_SOURCE}, "raise the retry limit")

    found = suspects(tmp_path, index, "main~1", "main")

    assert [(suspect.section.id, suspect.change.chunk) for suspect in found] == [
        ("README.md#Fetching", "src/http.py::fetch"),
    ]
