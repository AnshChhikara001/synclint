"""Command line: index a repository and write the index out."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from synclint.index import DEFAULT_DOCUMENTATION_GLOBS, build_index


def main() -> None:
    """Build the index for the repository named on the command line."""
    parser = argparse.ArgumentParser(
        prog="synclint",
        description="Index a repository's chunks, sections and the links between them.",
    )
    parser.add_argument("root", type=Path, help="the repository to index")
    parser.add_argument(
        "--out",
        type=Path,
        help="file to write the index to; defaults to standard output",
    )
    parser.add_argument(
        "--documentation-glob",
        action="append",
        dest="documentation_globs",
        metavar="GLOB",
        help=(
            "pattern, relative to the repository, naming markdown that counts as "
            f"documentation; repeatable, defaults to {' and '.join(DEFAULT_DOCUMENTATION_GLOBS)}"
        ),
    )
    arguments = parser.parse_args()

    globs = arguments.documentation_globs or DEFAULT_DOCUMENTATION_GLOBS
    document = build_index(arguments.root, globs).to_json()
    if arguments.out:
        arguments.out.write_text(document, encoding="utf-8")
    else:
        sys.stdout.write(document)


if __name__ == "__main__":
    main()
