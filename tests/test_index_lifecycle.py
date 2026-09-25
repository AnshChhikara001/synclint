import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from synclint.__main__ import main
from synclint.analyse import disappearances
from synclint.index import (
    DEFAULT_DOCUMENTATION_GLOBS,
    DEFAULT_INDEX_PATH,
    build_index,
    build_index_at,
    index_for,
    working_revision,
)

DOCS = """
    # Fetching

    Call `fetch(url)` to download a page.
    """

SOURCE = """
    def fetch(url):
        return url
    """

COMMITTED = Path(".synclint/index.json")

WORKFLOW = Path(__file__).parent.parent / ".github/workflows/index.yml"


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "synclint",
            "GIT_AUTHOR_EMAIL": "synclint@example.com",
            "GIT_COMMITTER_NAME": "synclint",
            "GIT_COMMITTER_EMAIL": "synclint@example.com",
        },
    ).stdout.strip()


def commit(root: Path, files: dict[str, str | None], message: str) -> None:
    """Write each file, or delete it where its content is `None`, and commit."""
    for relative, content in files.items():
        path = root / relative
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).lstrip())
    git(root, "add", "-A")
    git(root, "commit", "-m", message)


def start(root: Path, files: dict[str, str | None]) -> None:
    git(root, "init", "-q", "-b", "main")
    commit(root, files, "base")


def rev_parse(root: Path, revision: str) -> str:
    return git(root, "rev-parse", revision)


def commit_index(root: Path, message: str = "index") -> None:
    """Index the working tree as `synclint index` would, and commit the index."""
    index = replace(
        build_index(root), revision=working_revision(root, DEFAULT_DOCUMENTATION_GLOBS)
    )
    commit(root, {str(COMMITTED): index.to_json()}, message)


def test_an_index_built_at_a_revision_describes_that_revision_not_the_working_tree(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    base = rev_parse(tmp_path, "HEAD")
    commit(tmp_path, {"src/fetch.py": None}, "delete fetch")

    index = build_index_at(tmp_path, base, DEFAULT_DOCUMENTATION_GLOBS)

    assert [chunk.id for chunk in index.chunks] == ["src/fetch.py::fetch"]
    assert index.revision == base
    assert not (tmp_path / "src/fetch.py").exists()


def test_with_no_committed_index_one_is_built_at_the_base(tmp_path: Path) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    base = rev_parse(tmp_path, "HEAD")
    commit(tmp_path, {"src/fetch.py": None}, "delete fetch")

    index, used = index_for(tmp_path, base, tmp_path / COMMITTED, DEFAULT_DOCUMENTATION_GLOBS)

    assert used.path is None
    assert used.revision == base
    assert used.passed_over is not None and "no index" in used.passed_over
    # Built from the working tree, at the head, this would find nothing: the
    # chunk the section names is only there at the base.
    gone = disappearances(tmp_path, index, base, "HEAD")
    assert [flag.finding.section for flag in gone] == ["README.md#Fetching"]


def test_a_committed_index_is_used_while_nothing_it_covers_has_changed(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    built_at = rev_parse(tmp_path, "HEAD")
    commit_index(tmp_path)
    # The commit that commits an index is never the one it was built at, and
    # neither is a later change to a file the index does not read.
    commit(tmp_path, {"setup.cfg": "[metadata]\n"}, "unrelated")
    base = rev_parse(tmp_path, "HEAD")

    index, used = index_for(tmp_path, base, tmp_path / COMMITTED, DEFAULT_DOCUMENTATION_GLOBS)

    assert used.path == str(COMMITTED)
    assert used.revision == built_at
    assert used.passed_over is None
    assert index.revision == built_at


def test_a_committed_index_older_than_a_change_to_its_code_is_rebuilt(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    commit_index(tmp_path)
    commit(tmp_path, {"src/fetch.py": "def fetch(url, retries=3):\n    return url\n"}, "retries")
    base = rev_parse(tmp_path, "HEAD")

    index, used = index_for(tmp_path, base, tmp_path / COMMITTED, DEFAULT_DOCUMENTATION_GLOBS)

    assert used.path is None
    assert used.revision == base
    assert used.passed_over is not None and "1 file it covers" in used.passed_over
    assert "retries=3" in index.chunks[0].signature


def test_a_committed_index_older_than_a_change_to_its_documentation_is_rebuilt(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    commit_index(tmp_path)
    commit(tmp_path, {"docs/more.md": "# More\n\nSee `fetch`.\n"}, "more docs")

    _, used = index_for(tmp_path, "HEAD", tmp_path / COMMITTED, DEFAULT_DOCUMENTATION_GLOBS)

    assert used.path is None


def test_an_index_that_does_not_say_where_it_was_built_is_rebuilt(tmp_path: Path) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    commit(tmp_path, {str(COMMITTED): build_index(tmp_path).to_json()}, "index")

    _, used = index_for(tmp_path, "HEAD", tmp_path / COMMITTED, DEFAULT_DOCUMENTATION_GLOBS)

    assert used.path is None
    assert used.passed_over is not None and "does not record" in used.passed_over


def test_an_index_built_from_other_documentation_is_rebuilt(tmp_path: Path) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    commit_index(tmp_path)

    _, used = index_for(tmp_path, "HEAD", tmp_path / COMMITTED, ("guide/*.md",))

    assert used.path is None
    assert used.passed_over is not None and "guide/*.md" in used.passed_over


def test_an_index_built_at_a_commit_this_clone_lacks_is_rebuilt(tmp_path: Path) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    stranger = replace(build_index(tmp_path), revision="0" * 40)
    commit(tmp_path, {str(COMMITTED): stranger.to_json()}, "index")

    _, used = index_for(tmp_path, "HEAD", tmp_path / COMMITTED, DEFAULT_DOCUMENTATION_GLOBS)

    assert used.path is None
    assert used.passed_over is not None and "does not have" in used.passed_over


def test_a_clean_working_tree_is_described_by_its_head(tmp_path: Path) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    # Only files the index reads count: a stray build artefact changes nothing.
    (tmp_path / "notes.txt").write_text("scratch\n")

    assert working_revision(tmp_path, DEFAULT_DOCUMENTATION_GLOBS) == rev_parse(
        tmp_path, "HEAD"
    )


def test_a_working_tree_with_uncommitted_code_is_described_by_no_revision(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    (tmp_path / "src/new.py").write_text("def new(): ...\n")

    assert working_revision(tmp_path, DEFAULT_DOCUMENTATION_GLOBS) is None


def test_a_directory_outside_any_repository_is_described_by_no_revision(
    tmp_path: Path,
) -> None:
    assert working_revision(tmp_path, DEFAULT_DOCUMENTATION_GLOBS) is None


def test_an_index_reads_back_with_the_revision_and_documentation_it_was_built_from(
    tmp_path: Path,
) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    base = rev_parse(tmp_path, "HEAD")

    index = build_index_at(tmp_path, base, ("README.md",))

    assert index.documentation_globs == ("README.md",)
    assert type(index).from_json(index.to_json()) == index


def test_the_command_line_records_the_commit_it_indexed(tmp_path: Path) -> None:
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    out = tmp_path / COMMITTED

    main(["index", str(tmp_path), "--out", str(out)])

    assert json.loads(out.read_text())["revision"] == rev_parse(tmp_path, "HEAD")


def test_analyse_with_no_committed_index_reports_what_the_base_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The Action's path: no --index, a working tree at the head, and a head
    # that deleted what the documentation names. A disappearance costs no
    # model call, so the key is never used.
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    base = rev_parse(tmp_path, "HEAD")
    commit(tmp_path, {"src/fetch.py": None}, "delete fetch")

    main(["analyse", str(tmp_path), "--base", base, "--head", "HEAD"])

    printed = capsys.readouterr().out
    assert "1 names code the change deleted" in printed
    assert f"Index: built for this run at the base, {base[:7]}" in printed
    assert "0 model calls" in printed


def test_analyse_names_the_committed_index_when_it_used_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    start(tmp_path, {"README.md": DOCS, "src/fetch.py": SOURCE})
    built_at = rev_parse(tmp_path, "HEAD")
    commit_index(tmp_path)

    main(["analyse", str(tmp_path), "--base", "HEAD", "--head", "HEAD"])

    assert (
        f"Index: {COMMITTED}, built at {built_at[:7]}."
        in capsys.readouterr().out
    )


def test_the_rebuild_workflow_watches_the_files_the_index_reads() -> None:
    # Read with a regex, as action.yml is: one line of a file this repository
    # owns does not warrant a YAML parser.
    workflow = WORKFLOW.read_text()
    [paths] = re.findall(r"^    paths: (\[.*\])$", workflow, re.M)

    assert json.loads(paths) == ["**.py", *DEFAULT_DOCUMENTATION_GLOBS]
    assert f"--out {DEFAULT_INDEX_PATH}" in workflow
