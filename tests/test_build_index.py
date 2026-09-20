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
