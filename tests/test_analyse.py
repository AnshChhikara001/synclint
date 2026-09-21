import json
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from synclint.__main__ import render
from synclint.analyse import Finding, Report, analyse
from synclint.index import build_index
from synclint.model import ModelClient, ModelResponse, Pricing, Spend

PRICING = Pricing(input=1.00, output=2.00)


class ScriptedModel:
    """Answers the verification question the way a test needs it answered."""

    name = "scripted-model"
    max_output_tokens = 500

    def __init__(self, accurate: bool, explanation: str = "") -> None:
        self.accurate = accurate
        self.explanation = explanation
        self.asked: list[str] = []

    def complete(
        self, system: str, user: str, schema: dict[str, object]
    ) -> ModelResponse:
        self.asked.append(user)
        return ModelResponse(
            text=json.dumps(
                {"accurate": self.accurate, "explanation": self.explanation}
            ),
            input_tokens=100,
            output_tokens=20,
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
    assert report.checked == ("README.md#Fetching",)


def test_a_section_the_model_finds_accurate_is_checked_but_not_reported(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"src/http.py": BASE_SOURCE, "README.md": FETCHING_DOCS})
    index = build_index(tmp_path)
    commit(tmp_path, {"src/http.py": DRIFTED_SOURCE}, "raise the retry limit")

    report = analyse(tmp_path, index, "main~1", "main", client(ScriptedModel(True)))

    assert report.findings == ()
    assert report.checked == ("README.md#Fetching",)


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

    assert report.checked == ()
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

    assert report.checked == ()
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
    report = Report(
        findings=(
            Finding(
                section="README.md#Fetching",
                chunk="src/http.py::fetch",
                explanation="It now gives up after five attempts, not three.",
            ),
        ),
        checked=("README.md#Fetching", "docs/guide.md#Retries"),
        spend=Spend(calls=2, input_tokens=1200, output_tokens=140, dollars=0.00148),
    )

    printed = render(report)

    assert "Checked 2 sections; 1 has drifted." in printed
    assert "README.md#Fetching  (src/http.py::fetch)" in printed
    assert "It now gives up after five attempts, not three." in printed
    assert "2 model calls, 1200 tokens in, 140 out, $0.0015 spent." in printed


def test_the_command_line_says_so_when_nothing_drifted() -> None:
    printed = render(Report(findings=(), checked=("README.md#Fetching",), spend=Spend()))

    assert "Checked 1 section; 0 have drifted." in printed
