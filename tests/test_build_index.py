from pathlib import Path
from textwrap import dedent

from synclint.index import build_index


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

    # `decode` is absent: a function-local definition is unreachable from
    # documentation, so indexing it only adds noise to name matching.
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
