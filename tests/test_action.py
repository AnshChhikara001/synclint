import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from synclint import action
from synclint.__main__ import DEFAULT_CEILING
from synclint.confidence import DEFAULT_THRESHOLD
from synclint.index import DEFAULT_DOCUMENTATION_GLOBS
from synclint.model import DEFAULT_MODEL

KEY = "sk-test-not-a-real-key"

ACTION = Path(__file__).parent.parent / "action.yml"


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        [
            "git",
            *("-C", str(root)),
            *("-c", "user.name=t", "-c", "user.email=t@example.com"),
            *("-c", "commit.gpgsign=false"),
            *arguments,
        ],
        check=True,
        capture_output=True,
    )
    return result.stdout.decode().strip()


def commit(root: Path, name: str) -> str:
    (root / name).write_text(name)
    git(root, "add", "-A")
    git(root, "commit", "-qm", name)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A pull request branched from main, after which main moved on without it."""
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    forked = commit(root, "forked")
    git(root, "checkout", "-qb", "feature")
    head = commit(root, "feature")
    git(root, "checkout", "-q", "main")
    base = commit(root, "moved-on")
    return root, {"forked": forked, "head": head, "base": base}


def event(
    base: str, head: str, *, fork: bool = False, author: str = "someone"
) -> dict[str, Any]:
    return {
        "pull_request": {
            "number": 7,
            "user": {"login": author},
            "base": {"sha": base, "repo": {"full_name": "owner/library"}},
            "head": {
                "sha": head,
                "repo": {"full_name": "someone/library" if fork else "owner/library"},
            },
        },
    }


def test_analyses_from_where_the_branch_forked_not_from_the_base_tip(
    history: tuple[Path, dict[str, str]],
) -> None:
    root, commits = history

    argv = action.command(event(commits["base"], commits["head"]), root, action.Inputs())

    assert argv == [
        "analyse",
        str(root),
        *("--base", commits["forked"]),
        *("--head", commits["head"]),
        *("--pull-request", "7"),
        "--cache",
        argv[-1],
    ]
    assert not Path(argv[-1]).is_relative_to(root)


def test_inputs_left_empty_fall_back_to_the_command_lines_defaults(
    history: tuple[Path, dict[str, str]],
) -> None:
    root, commits = history
    inputs = action.Inputs(
        model="gpt-5.4",
        documentation_glob="README.md\n  docs/**/*.md\n\n",
        confidence_threshold="",
        ceiling="0.5",
    )

    argv = action.command(event(commits["base"], commits["head"]), root, inputs)

    assert argv[-8:] == [
        *("--model", "gpt-5.4"),
        *("--documentation-glob", "README.md"),
        *("--documentation-glob", "docs/**/*.md"),
        *("--ceiling", "0.5"),
    ]
    assert "--confidence-threshold" not in argv


def test_a_shallow_clone_is_refused_with_the_remedy(
    history: tuple[Path, dict[str, str]],
) -> None:
    root, commits = history
    missing = "0" * 40

    with pytest.raises(SystemExit, match="fetch-depth: 0"):
        action.command(event(missing, commits["head"]), root, action.Inputs())


@pytest.fixture
def workflow(
    history: tuple[Path, dict[str, str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[Sequence[str]]:
    """The environment GitHub gives the container, with `analyse` swapped for a recorder."""
    root, commits = history
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event(commits["base"], commits["head"])))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(root))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    ran: list[Sequence[str]] = []
    monkeypatch.setattr(action, "analyse_main", ran.append)
    return ran


def test_the_api_key_reaches_analyse_through_the_environment_and_nowhere_else(
    workflow: list[Sequence[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    action.main(["--model", "gpt-5.4-mini"])

    [argv] = workflow
    assert KEY not in " ".join(argv)
    assert KEY not in "".join(capsys.readouterr())


@pytest.mark.parametrize(
    "opened", [{"fork": True}, {"author": "dependabot[bot]"}], ids=["fork", "dependabot"]
)
def test_a_pull_request_github_gives_no_secrets_is_skipped_with_a_warning(
    opened: dict[str, Any],
    workflow: list[Sequence[str]],
    history: tuple[Path, dict[str, str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, commits = history
    path = tmp_path / "secretless.json"
    path.write_text(json.dumps(event(commits["base"], commits["head"], **opened)))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.delenv("OPENAI_API_KEY")

    action.main([])

    assert workflow == []
    assert capsys.readouterr().out.startswith("::warning::")


def test_a_missing_key_on_the_repositorys_own_branch_fails_the_run(
    workflow: list[Sequence[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")

    with pytest.raises(SystemExit, match="api-key"):
        action.main([])
    assert workflow == []


def test_an_event_that_is_not_a_pull_request_is_refused(
    workflow: list[Sequence[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "push.json"
    path.write_text(json.dumps({"ref": "refs/heads/main"}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))

    with pytest.raises(SystemExit, match="pull_request"):
        action.main([])


def inputs_declared() -> dict[str, dict[str, str]]:
    """Each input in action.yml with its keys, read with a regex rather than a YAML parser.

    The file is ours and flat, and a dependency to read five inputs would be
    the first thing in this project that exists only for a test.
    """
    block = ACTION.read_text().split("\ninputs:\n", 1)[1].split("\nruns:", 1)[0]
    declared: dict[str, dict[str, str]] = {}
    for name, body in re.findall(r"^  ([\w-]+):\n((?:    .*\n?)*)", block, re.M):
        declared[name] = dict(re.findall(r"^    (\w+): (.*)$", body, re.M))
    return declared


def test_every_input_has_a_description_and_a_default() -> None:
    declared = inputs_declared()

    assert set(declared) == {
        "api-key",
        "github-token",
        "documentation-glob",
        "model",
        "confidence-threshold",
        "ceiling",
    }
    for name, keys in declared.items():
        assert keys.get("description"), name
        assert "default" in keys, name


def test_the_actions_defaults_are_the_command_lines() -> None:
    declared = inputs_declared()

    # Double-quoted in the YAML, where JSON's escapes mean the same thing.
    assert declared["model"]["default"] == json.dumps(DEFAULT_MODEL)
    assert declared["confidence-threshold"]["default"] == json.dumps(str(DEFAULT_THRESHOLD))
    assert declared["ceiling"]["default"] == json.dumps(str(DEFAULT_CEILING))
    assert declared["documentation-glob"]["default"] == json.dumps(
        "\n".join(DEFAULT_DOCUMENTATION_GLOBS)
    )
