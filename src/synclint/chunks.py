"""Chunk extraction: the functions, methods and classes a repository defines."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def chunk_id(path: str, qualname: str) -> str:
    """The identity of a chunk: where it lives plus what it is called."""
    return f"{path}::{qualname}"


@dataclass(frozen=True)
class Chunk:
    """A function, method or class, recorded without its body.

    The body is deliberately absent: indexing cost stays proportional to
    repository size, and `analyse` reads bodies only for the chunks a diff
    touched.
    """

    path: str
    qualname: str
    signature: str
    docstring: str | None
    decorators: tuple[str, ...]

    @property
    def id(self) -> str:
        return chunk_id(self.path, self.qualname)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "qualname": self.qualname,
            "signature": self.signature,
            "docstring": self.docstring,
            "decorators": list(self.decorators),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Chunk:
        return cls(
            path=data["path"],
            qualname=data["qualname"],
            signature=data["signature"],
            docstring=data["docstring"],
            decorators=tuple(data["decorators"]),
        )


def extract_chunks(source: str | bytes, path: str) -> list[Chunk]:
    """Extract every chunk defined in one Python source file, in source order.

    Raises `SyntaxError` if the source does not parse. Bytes are accepted so
    that `ast` applies the file's own encoding declaration.
    """
    return [
        _chunk(node, path, qualname)
        for qualname, node in walk_definitions(ast.parse(source).body)
    ]


def walk_definitions(
    body: list[ast.stmt], prefix: str = ""
) -> Iterator[tuple[str, Definition]]:
    """Yield every definition in a module body, in source order, with its qualified name.

    Shared with `analyse`, which compares the same definitions across two revisions
    and must agree with the index about which ones exist.
    """
    for node in body:
        if not isinstance(node, Definition):
            continue
        # TODO: definitions nested in `if` or `try` blocks are not reached at
        # all, and two definitions sharing a qualified name in one file collide
        # on one id. The fixture corpus decides how much either costs.
        qualname = f"{prefix}{node.name}"
        yield qualname, node
        # Descend into classes but not into functions: a function-local
        # definition is unreachable from documentation, so indexing it would
        # only add noise to name matching.
        if isinstance(node, ast.ClassDef):
            yield from walk_definitions(node.body, prefix=f"{qualname}.")


def _chunk(node: Definition, path: str, qualname: str) -> Chunk:
    return Chunk(
        path=path,
        qualname=qualname,
        signature=_signature(node),
        docstring=ast.get_docstring(node),
        decorators=tuple(ast.unparse(decorator) for decorator in node.decorator_list),
    )


def _signature(node: Definition) -> str:
    # Unparsed rather than sliced from the source, so that reformatting a
    # definition without changing it leaves the index untouched.
    if isinstance(node, ast.ClassDef):
        parents = [ast.unparse(base) for base in node.bases]
        parents += [ast.unparse(keyword) for keyword in node.keywords]
        return f"class {node.name}({', '.join(parents)})" if parents else f"class {node.name}"
    keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{keyword} {node.name}({ast.unparse(node.args)}){returns}"
