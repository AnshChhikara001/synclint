import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synclint.__main__ import main
from synclint.analyse import Finding, Flag, Repair, Report
from synclint.github import GitHubError
from synclint.index import IndexUsed
from synclint.model import Spend
from synclint.publish import MARKER, Routing, publish, route, summary

REPOSITORY = "owner/library"

FETCHING = "Call `fetch(url)`. It gives up after three attempts."
FETCHING_REPAIRED = "Call `fetch(url)`. It gives up after five attempts."
SAVING = "Call `save(path)` to write the file."
LOADING = "Call `load(path)`; a missing file is created."

DOCS = f"""\
# Library

## Fetching

{FETCHING}

## Saving

{SAVING}

## Loading

{LOADING}
"""

FETCHING_ID = "docs/usage.md#Library > Fetching"
SAVING_ID = "docs/usage.md#Library > Saving"
LOADING_ID = "docs/usage.md#Library > Loading"


def finding(section: str, explanation: str = "The default is now five.") -> Finding:
    return Finding(section=section, chunk="library.net:fetch", explanation=explanation)


def repaired(section: str = FETCHING_ID, original: str = FETCHING) -> Repair:
    return Repair(
        finding=finding(section),
        original=original,
        repaired=original.replace("three", "five"),
        shape="changed-default",
        confidence=0.97,
    )


def flagged(attempt: str | None = "Call `load(path)`.") -> Flag:
    return Flag(
        finding=finding(LOADING_ID, "A missing file now raises."),
        reason="validation refused it",
        original=LOADING,
        attempt=attempt,
        cause="refused",
    )


def report(
    repairs: tuple[Repair, ...] = (),
    flags: tuple[Flag, ...] = (),
    verified: tuple[str, ...] = (FETCHING_ID, SAVING_ID, LOADING_ID),
    unchecked: int = 0,
) -> Report:
    return Report(
        findings=tuple(r.finding for r in repairs) + tuple(f.finding for f in flags),
        verified=verified,
        unchecked=unchecked,
        unrepaired=0,
        repairs=repairs,
        flags=flags,
        spend=Spend(calls=4, input_tokens=900, output_tokens=80, dollars=0.0012),
    )


FILES: dict[str, str | None] = {"docs/usage.md": DOCS}


# Routing, asserted on the report.


def test_a_repair_to_a_section_that_still_reads_as_analysed_is_proposed() -> None:
    repair = repaired()
    routing = route(report(repairs=(repair,)), FILES)
    assert routing.proposed == (repair,)
    assert routing.held == ()


def test_flags_never_reach_the_pull_request() -> None:
    routing = route(report(flags=(flagged(),)), FILES)
    assert routing == Routing(proposed=(), held=())


def test_two_repairs_to_one_section_are_both_held_back() -> None:
    first = repaired()
    second = Repair(
        finding=Finding(FETCHING_ID, "library.net:retry", "Retries are gone."),
        original=FETCHING,
        repaired="Call `fetch(url)`.",
        shape="changed-default",
        confidence=0.95,
    )
    routing = route(report(repairs=(first, second)), FILES)
    assert routing.proposed == ()
    assert [held.repair for held in routing.held] == [first, second]
    assert "only one of them could be applied" in routing.held[0].reason


def test_a_section_that_changed_since_it_was_analysed_is_held_back() -> None:
    moved_on = {"docs/usage.md": DOCS.replace("three attempts", "3 attempts")}
    routing = route(report(repairs=(repaired(),)), moved_on)
    assert routing.proposed == ()
    assert "as it did when it was analysed" in routing.held[0].reason


def test_a_section_whose_file_is_gone_is_held_back() -> None:
    routing = route(report(repairs=(repaired(),)), {"docs/usage.md": None})
    assert "not there" in routing.held[0].reason


# The comment.


def test_the_comment_starts_with_the_marker_and_counts_every_section_once() -> None:
    run = report(repairs=(repaired(),), flags=(flagged(),))
    body = summary(run, route(run, FILES), links={}, pull="https://example.com/pr/9")
    assert body.startswith(MARKER)
    assert "checked 3 documentation sections" in body
    assert "1 accurate, 1 repaired, 1 flagged" in body


def test_the_comment_links_each_section_and_the_pull_request_of_repairs() -> None:
    run = report(repairs=(repaired(),), flags=(flagged(),))
    links = {section: f"https://example.com/{n}" for n, section in enumerate(run.verified)}
    body = summary(run, route(run, FILES), links=links, pull="https://example.com/pr/9")
    for section, link in links.items():
        assert f"[{section}]({link})" in body
    assert "The repairs are in https://example.com/pr/9." in body


def test_a_held_repair_is_counted_as_flagged_and_shown_as_a_diff() -> None:
    run = report(repairs=(repaired(),))
    moved_on = {"docs/usage.md": DOCS.replace("three attempts", "3 attempts")}
    body = summary(run, route(run, moved_on), links={})
    assert "0 repaired, 1 flagged" in body
    assert "Repaired, but not proposed" in body
    assert "+Call `fetch(url)`. It gives up after five attempts." in body


def test_a_degraded_run_says_why_and_puts_the_repairs_in_the_comment() -> None:
    run = report(repairs=(repaired(),))
    body = summary(run, route(run, FILES), links={}, degraded="the token is read-only")
    assert "rather than in a pull request: the token is read-only." in body
    assert "-Call `fetch(url)`. It gives up after three attempts." in body
    assert "+Call `fetch(url)`. It gives up after five attempts." in body


def test_a_flag_shows_the_rewrite_that_was_tried_and_why_it_was_not_proposed() -> None:
    # The reason is the model's own sentence, full stop included.
    run = report(flags=(replace(flagged(), reason="validation refused it."),))
    body = summary(run, route(run, FILES), links={})
    assert "Not repaired: validation refused it." in body
    assert "The rewrite that was tried" in body
    assert "+Call `load(path)`." in body


def test_the_comment_says_which_index_the_run_used() -> None:
    used = IndexUsed("abc1234" + "0" * 33, None, "there is no index at .synclint/index.json")
    run = replace(report(), index=used)

    body = summary(run, route(run, FILES), links={})

    assert "built for this run at the base, abc1234" in body
    assert "there is no index at .synclint/index.json" in body


def test_a_diff_that_quotes_a_code_fence_is_fenced_with_a_longer_one() -> None:
    fenced = "Example:\n\n```python\nfetch(url)\n```\n\nIt retries three times."
    run = report(repairs=(repaired(original=fenced),))
    body = summary(run, route(run, {"docs/usage.md": fenced}), links={}, degraded="x")
    assert "````diff" in body


def test_a_run_with_nothing_linked_still_says_so() -> None:
    body = summary(report(verified=()), Routing((), ()), links={})
    assert body.startswith(MARKER)
    assert "nothing to check" in body


def test_a_run_stopped_at_its_ceiling_says_the_answer_is_incomplete() -> None:
    run = report(unchecked=2)
    body = summary(run, route(run, FILES), links={})
    assert "2 suspects never checked" in body


def gone(section: str = SAVING_ID) -> Flag:
    return Flag(
        finding=Finding(
            section=section,
            chunk="library/io.py::save",
            explanation="It names `save`, which no longer exists.",
            kind="disappearance",
        ),
        reason="the code it describes is gone",
        original=SAVING,
        attempt=None,
        cause="vanished",
    )


def test_a_section_naming_deleted_code_is_listed_apart_from_the_flags() -> None:
    run = report(flags=(gone(), flagged()))
    body = summary(run, route(run, FILES), links={})
    listed = body.index("### Names code this pull request deletes")
    assert body.index("It names `save`", listed) < body.index("### Flagged")
    assert "A missing file now raises." in body[body.index("### Flagged") :]
    assert "1 section names code this pull request deletes" in body


def test_a_run_whose_only_findings_are_deleted_code_does_not_say_nothing_was_checked() -> (
    None
):
    run = report(flags=(gone(),), verified=())
    body = summary(run, route(run, FILES), links={})
    assert "nothing to check" not in body
    assert "1 section names code this pull request deletes" in body


def test_a_section_naming_deleted_code_is_not_counted_among_those_checked() -> None:
    run = report(flags=(gone(),), verified=(FETCHING_ID,))
    body = summary(run, route(run, FILES), links={})
    assert "checked 1 documentation section" in body
    assert "1 accurate, 0 repaired, 0 flagged" in body


# The I/O around them, against a GitHub that is only a dictionary.


class FakeGitHub:
    """Answers the handful of calls `publish` makes, and records every one."""

    def __init__(
        self,
        *,
        comments: list[dict[str, Any]] | None = None,
        from_fork: bool = False,
        refuse_writes: int | None = None,
        branch_exists: bool = False,
    ) -> None:
        self.comments = comments or []
        self.from_fork = from_fork
        self.refuse_writes = refuse_writes
        self.branch_exists = branch_exists
        self.calls: list[tuple[str, str, object]] = []
        self.head = ""

    def __call__(self, method: str, path: str, body: object = None) -> Any:
        self.calls.append((method, path, body))
        base = f"/repos/{REPOSITORY}"
        if path == f"{base}/pulls/7":
            head_repo = "someone/library" if self.from_fork else REPOSITORY
            return {
                "head": {
                    "ref": "feature",
                    "sha": self.head,
                    "repo": {"full_name": head_repo},
                },
                "base": {"repo": {"full_name": REPOSITORY}},
            }
        if path.startswith(f"{base}/issues/7/comments?"):
            return self.comments if path.endswith("page=1") else []
        if method == "PATCH" and path.startswith(f"{base}/issues/comments/"):
            return {"html_url": "https://github.com/comment/edited"}
        if method == "POST" and path == f"{base}/issues/7/comments":
            return {"html_url": "https://github.com/comment/new"}
        if path.startswith(f"{base}/git/commits/"):
            return {"tree": {"sha": "base-tree"}}
        if self.refuse_writes and path.startswith(f"{base}/git/"):
            raise GitHubError(self.refuse_writes, "Resource not accessible by integration")
        if path == f"{base}/git/trees":
            return {"sha": "new-tree"}
        if path == f"{base}/git/commits":
            return {"sha": "new-commit"}
        if method == "POST" and path == f"{base}/git/refs":
            if self.branch_exists:
                raise GitHubError(422, "Reference already exists")
            return {}
        if method == "PATCH" and path.startswith(f"{base}/git/refs/"):
            return {}
        if method == "GET" and path.startswith(f"{base}/pulls?"):
            return []
        if method == "POST" and path == f"{base}/pulls":
            return {"html_url": "https://github.com/owner/library/pull/8"}
        raise AssertionError(f"unexpected call: {method} {path}")

    def bodies(self, method: str, path: str) -> list[Any]:
        return [body for m, p, body in self.calls if m == method and p.endswith(path)]


@pytest.fixture
def clone(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "library"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "usage.md").write_text(DOCS)
    for command in (
        ["init", "-q", "-b", "feature"],
        ["add", "-A"],
        [
            *("-c", "user.name=t", "-c", "user.email=t@example.com"),
            *("-c", "commit.gpgsign=false", "commit", "-qm", "docs"),
        ],
    ):
        subprocess.run(["git", "-C", str(root), *command], check=True)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True
    ).stdout.decode().strip()
    return root, head


def github_at(head: str, **options: Any) -> FakeGitHub:
    github = FakeGitHub(**options)
    github.head = head
    return github


def test_publish_opens_a_pull_request_of_repairs_against_the_branch_under_review(
    clone: tuple[Path, str],
) -> None:
    root, head = clone
    github = github_at(head)
    publish(report(repairs=(repaired(),)), root, github, REPOSITORY, 7)

    (tree,) = github.bodies("POST", "/git/trees")
    assert tree["tree"] == [
        {
            "path": "docs/usage.md",
            "mode": "100644",
            "type": "blob",
            "content": DOCS.replace("three attempts", "five attempts"),
        }
    ]
    (commit,) = github.bodies("POST", "/git/commits")
    assert commit["parents"] == [head]
    (pull,) = github.bodies("POST", "/pulls")
    assert pull["base"] == "feature"
    assert pull["head"] == "synclint/repairs-7"
    (comment,) = github.bodies("POST", "/issues/7/comments")
    assert "https://github.com/owner/library/pull/8" in comment["body"]


def test_publish_links_each_section_to_the_lines_it_reads_on(
    clone: tuple[Path, str],
) -> None:
    root, head = clone
    github = github_at(head)
    publish(report(), root, github, REPOSITORY, 7)
    (comment,) = github.bodies("POST", "/issues/7/comments")
    blob = f"https://github.com/{REPOSITORY}/blob/{head}/docs/usage.md?plain=1"
    assert f"[{FETCHING_ID}]({blob}#L5-L5)" in comment["body"]
    assert f"[{LOADING_ID}]({blob}#L13-L13)" in comment["body"]


def test_publish_links_a_section_naming_deleted_code_too(
    clone: tuple[Path, str],
) -> None:
    root, head = clone
    github = github_at(head)
    publish(report(flags=(gone(),), verified=()), root, github, REPOSITORY, 7)
    (comment,) = github.bodies("POST", "/issues/7/comments")
    blob = f"https://github.com/{REPOSITORY}/blob/{head}/docs/usage.md?plain=1"
    assert f"[{SAVING_ID}]({blob}#L9-L9)" in comment["body"]


def test_publish_edits_its_own_comment_rather_than_adding_another(
    clone: tuple[Path, str],
) -> None:
    root, head = clone
    github = github_at(
        head,
        comments=[
            {"id": 1, "body": "Looks good to me."},
            {"id": 2, "body": f"{MARKER}\nan earlier run"},
        ],
    )
    url = publish(report(), root, github, REPOSITORY, 7)
    assert url == "https://github.com/comment/edited"
    assert github.bodies("POST", "/issues/7/comments") == []
    assert [p for m, p, _ in github.calls if m == "PATCH"] == [
        f"/repos/{REPOSITORY}/issues/comments/2"
    ]


def test_a_read_only_token_degrades_to_the_comment_and_says_so(
    clone: tuple[Path, str],
) -> None:
    root, head = clone
    github = github_at(head, refuse_writes=403)
    publish(report(repairs=(repaired(),)), root, github, REPOSITORY, 7)
    (comment,) = github.bodies("POST", "/issues/7/comments")
    assert "cannot write to the repository" in comment["body"]
    assert "+Call `fetch(url)`. It gives up after five attempts." in comment["body"]


def test_a_refusal_that_is_not_about_permission_is_not_mistaken_for_one(
    clone: tuple[Path, str],
) -> None:
    root, head = clone
    github = github_at(head, refuse_writes=500)
    with pytest.raises(GitHubError):
        publish(report(repairs=(repaired(),)), root, github, REPOSITORY, 7)


def test_a_pull_request_from_a_fork_is_never_written_to(
    clone: tuple[Path, str],
) -> None:
    root, head = clone
    github = github_at(head, from_fork=True)
    publish(report(repairs=(repaired(),)), root, github, REPOSITORY, 7)
    assert not [p for _, p, _ in github.calls if "/git/" in p]
    (comment,) = github.bodies("POST", "/issues/7/comments")
    assert "comes from a fork" in comment["body"]


def test_a_later_push_replaces_the_repair_branch(clone: tuple[Path, str]) -> None:
    root, head = clone
    github = github_at(head, branch_exists=True)
    publish(report(repairs=(repaired(),)), root, github, REPOSITORY, 7)
    (update,) = github.bodies("PATCH", "/git/refs/heads/synclint/repairs-7")
    assert update == {"sha": "new-commit", "force": True}


def test_publish_refuses_a_clone_without_the_head_commit(
    clone: tuple[Path, str],
) -> None:
    root, _ = clone
    with pytest.raises(RuntimeError, match="full history"):
        publish(report(), root, github_at("0" * 40), REPOSITORY, 7)


def test_the_command_line_refuses_to_publish_without_a_token_before_spending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    arguments = ["analyse", str(tmp_path), "--base", "a", "--head", "b"]
    arguments += ["--pull-request", "7", "--repository", REPOSITORY]
    with pytest.raises(SystemExit, match="GITHUB_TOKEN"):
        main(arguments)
