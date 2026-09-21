import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from synclint.index import Index, build_index


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip())


def test_records_a_module_level_function_as_a_chunk(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/greeting.py",
        '''
        from functools import lru_cache


        @lru_cache(maxsize=8)
        def greet(name: str, punctuation: str = "!") -> str:
            """Return a greeting for name."""
            return f"Hello {name}{punctuation}"
        ''',
    )

    index = build_index(tmp_path)

    assert [chunk.id for chunk in index.chunks] == ["src/greeting.py::greet"]
    chunk = index.chunks[0]
    assert chunk.signature == "def greet(name: str, punctuation: str='!') -> str"
    assert chunk.docstring == "Return a greeting for name."
    assert chunk.decorators == ("lru_cache(maxsize=8)",)


def test_records_classes_and_methods_by_qualified_name(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/repo.py",
        '''
        class Repo(Base, metaclass=Meta):
            """A checked-out repository."""

            async def fetch(self, url: str) -> bytes:
                def decode(raw: bytes) -> str:
                    return raw.decode()

                return b""
        ''',
    )

    index = build_index(tmp_path)

    # `decode` is absent: function-local definitions are not chunks.
    assert [chunk.id for chunk in index.chunks] == [
        "src/repo.py::Repo",
        "src/repo.py::Repo.fetch",
    ]
    assert index.chunks[0].signature == "class Repo(Base, metaclass=Meta)"
    assert index.chunks[1].signature == "async def fetch(self, url: str) -> bytes"


def test_splits_documentation_into_sections_by_heading(tmp_path: Path) -> None:
    write(
        tmp_path,
        "README.md",
        """
        # synclint

        Checks that the docs still match the code.

        ## Usage

        Add the workflow file.

        ### Configuration

        Set the documentation glob.

        ## Limitations

        Python only.
        """,
    )

    index = build_index(tmp_path)

    assert [section.heading_path for section in index.sections] == [
        ("synclint",),
        ("synclint", "Usage"),
        ("synclint", "Usage", "Configuration"),
        ("synclint", "Limitations"),
    ]
    assert index.sections[2].id == "README.md#synclint > Usage > Configuration"
    assert index.sections[2].text == "Set the documentation glob."


def test_does_not_split_on_a_heading_inside_a_fenced_code_block(tmp_path: Path) -> None:
    write(
        tmp_path,
        "README.md",
        """
        # Usage

        Run it like this:

        ```python
        # Build the index
        build_index(root)
        ```

        That is all.
        """,
    )

    index = build_index(tmp_path)

    assert [section.heading_path for section in index.sections] == [("Usage",)]
    assert "build_index(root)" in index.sections[0].text


def test_links_a_section_to_a_chunk_it_names(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/indexing.py",
        '''
        def build_index(root):
            """Walk a repository and record what it contains."""


        def prune_orphans(root):
            """Nothing in the documentation mentions this."""
        ''',
    )
    write(
        tmp_path,
        "README.md",
        """
        # Usage

        Call `build_index(root)` and it walks the repository for you.

        ## Internals

        Nothing here names anything.
        """,
    )

    index = build_index(tmp_path)

    assert [(link.section, link.chunk, link.mechanism) for link in index.links] == [
        ("README.md#Usage", "src/indexing.py::build_index", "name"),
    ]


def write_documentation_tree(root: Path) -> None:
    for relative in (
        "README.md",
        "docs/guide.md",
        "docs/reference/api.md",
        "notes/scratch.md",
        "CHANGELOG.md",
    ):
        write(root, relative, f"# {relative}\n\nSome prose.\n")


def test_documentation_defaults_to_the_readme_and_the_docs_directory(tmp_path: Path) -> None:
    write_documentation_tree(tmp_path)

    index = build_index(tmp_path)

    assert sorted({section.path for section in index.sections}) == [
        "README.md",
        "docs/guide.md",
        "docs/reference/api.md",
    ]


def test_documentation_glob_is_configurable(tmp_path: Path) -> None:
    write_documentation_tree(tmp_path)

    index = build_index(tmp_path, documentation_globs=["notes/*.md", "CHANGELOG.md"])

    assert sorted({section.path for section in index.sections}) == [
        "CHANGELOG.md",
        "notes/scratch.md",
    ]


def write_authentication_repository(root: Path) -> None:
    write(
        root,
        "src/auth.py",
        '''
        def authenticate(token: str) -> bool:
            """Return whether the token is still valid."""
            return token == DEVELOPMENT_BACKDOOR
        ''',
    )
    write(
        root,
        "README.md",
        """
        # Auth

        Call `authenticate(token)` before serving a request.
        """,
    )


def test_serialises_chunks_sections_and_links_to_json(tmp_path: Path) -> None:
    write_authentication_repository(tmp_path)

    document = json.loads(build_index(tmp_path).to_json())

    assert document == {
        "chunks": [
            {
                "id": "src/auth.py::authenticate",
                "path": "src/auth.py",
                "qualname": "authenticate",
                "signature": "def authenticate(token: str) -> bool",
                "docstring": "Return whether the token is still valid.",
                "decorators": [],
            }
        ],
        "sections": [
            {
                "id": "README.md#Auth",
                "path": "README.md",
                "heading_path": ["Auth"],
                "text": "Call `authenticate(token)` before serving a request.",
            }
        ],
        "links": [
            {
                "section": "README.md#Auth",
                "chunk": "src/auth.py::authenticate",
                "mechanism": "name",
            }
        ],
    }


def test_omits_function_bodies_from_the_index(tmp_path: Path) -> None:
    write_authentication_repository(tmp_path)

    assert "DEVELOPMENT_BACKDOOR" not in build_index(tmp_path).to_json()


def test_reads_back_the_index_it_wrote(tmp_path: Path) -> None:
    write_authentication_repository(tmp_path)

    index = build_index(tmp_path)

    assert Index.from_json(index.to_json()) == index


def render_index(root: Path, hash_seed: str) -> str:
    """Build and render the index in a fresh interpreter with a given hash seed."""
    script = (
        "import sys; from pathlib import Path; from synclint.index import build_index;"
        " sys.stdout.write(build_index(Path(sys.argv[1])).to_json())"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
    )
    return result.stdout


def test_rebuilding_unchanged_input_produces_byte_identical_json(tmp_path: Path) -> None:
    write_authentication_repository(tmp_path)
    write_documentation_tree(tmp_path)
    write(
        tmp_path,
        "src/indexing.py",
        '''
        class Indexer:
            """Walks a repository."""

            def scan(self, root):
                """Read every file under root."""
        ''',
    )

    # Separate interpreters with different hash seeds: set iteration order is
    # the way this output most plausibly stops being reproducible.
    renderings = {render_index(tmp_path, seed) for seed in ("0", "1", "2")}

    assert len(renderings) == 1


def test_ignores_python_under_hidden_and_cache_directories(tmp_path: Path) -> None:
    write(tmp_path, "src/indexing.py", "def build_index(root): ...\n")
    write(tmp_path, ".venv/lib/vendored.py", "def vendored(): ...\n")
    write(tmp_path, "src/__pycache__/stale.py", "def stale(): ...\n")

    index = build_index(tmp_path)

    assert [chunk.qualname for chunk in index.chunks] == ["build_index"]


def test_skips_a_python_file_that_does_not_parse(tmp_path: Path) -> None:
    write(tmp_path, "src/indexing.py", "def build_index(root): ...\n")
    write(tmp_path, "src/legacy.py", "print 'python 2'\n")

    index = build_index(tmp_path)

    assert [chunk.qualname for chunk in index.chunks] == ["build_index"]


def test_records_prose_that_sits_before_the_first_heading(tmp_path: Path) -> None:
    write(
        tmp_path,
        "README.md",
        """
        A one-line summary above every heading.

        # Usage

        Add the workflow file.
        """,
    )

    index = build_index(tmp_path)

    assert [(section.id, section.heading_path) for section in index.sections] == [
        ("README.md", ()),
        ("README.md#Usage", ("Usage",)),
    ]


def test_ignores_documentation_outside_the_repository(tmp_path: Path) -> None:
    write(tmp_path / "elsewhere", "leaked.md", "# Leaked\n\nNot ours to read.\n")
    write(tmp_path / "repo", "README.md", "# Ours\n\nOurs to read.\n")

    index = build_index(
        tmp_path / "repo", documentation_globs=["README.md", "../elsewhere/*.md"]
    )

    assert [section.path for section in index.sections] == ["README.md"]


def test_indexes_documentation_that_is_not_valid_utf8(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_bytes(b"# Caf\xe9\n\nLatin-1 prose about authenticate().\n")
    write(tmp_path, "src/auth.py", "def authenticate(token): ...\n")

    index = build_index(tmp_path)

    assert [section.path for section in index.sections] == ["README.md"]
    assert [link.chunk for link in index.links] == ["src/auth.py::authenticate"]


def test_command_line_writes_the_index_to_a_file(tmp_path: Path) -> None:
    write_authentication_repository(tmp_path)
    destination = tmp_path / "synclint-index.json"

    subprocess.run(
        [sys.executable, "-m", "synclint", "index", str(tmp_path), "--out", str(destination)],
        check=True,
        capture_output=True,
    )

    assert Index.from_json(destination.read_text()) == build_index(tmp_path)
