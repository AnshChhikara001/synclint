"""Command line: build an index, or analyse a change against one."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from synclint.analyse import Report, analyse
from synclint.index import DEFAULT_DOCUMENTATION_GLOBS, Index, build_index
from synclint.model import DEFAULT_MODEL, PRICES, ModelClient, OpenAIModel

# A run checks a handful of sections, which at the default model costs cents.
# The ceiling is here for the run that is not typical — a pull request touching
# hundreds of chunks — and is the first thing to raise if an honest run hits it.
DEFAULT_CEILING = 0.25

DEFAULT_CACHE = Path(".cache/synclint")


def main() -> None:
    """Run the subcommand named on the command line."""
    parser = argparse.ArgumentParser(
        prog="synclint",
        description="Find the documentation a code change has made inaccurate.",
    )
    subcommands = parser.add_subparsers(dest="subcommand", required=True)

    index = subcommands.add_parser("index", help="index a repository")
    index.add_argument("root", type=Path, help="the repository to index")
    index.add_argument(
        "--out",
        type=Path,
        help="file to write the index to; defaults to standard output",
    )
    index.add_argument(
        "--documentation-glob",
        action="append",
        dest="documentation_globs",
        metavar="GLOB",
        help=(
            "pattern, relative to the repository, naming markdown that counts as "
            f"documentation; repeatable, defaults to {' and '.join(DEFAULT_DOCUMENTATION_GLOBS)}"
        ),
    )

    check = subcommands.add_parser("analyse", help="check a change against an index")
    check.add_argument("root", type=Path, help="the repository to check")
    check.add_argument("--base", required=True, help="the revision changed from")
    check.add_argument("--head", required=True, help="the revision changed to")
    check.add_argument(
        "--index",
        type=Path,
        dest="index_path",
        help="the index to check against; built in memory if not given",
    )
    check.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"the model to ask; defaults to {DEFAULT_MODEL}"
    )
    check.add_argument(
        "--ceiling",
        type=float,
        default=DEFAULT_CEILING,
        help=f"dollars this run may spend before it aborts; defaults to {DEFAULT_CEILING}",
    )
    check.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"directory of recorded model answers; defaults to {DEFAULT_CACHE}",
    )

    arguments = parser.parse_args()
    if arguments.subcommand == "index":
        _index(arguments)
    else:
        _analyse(arguments)


def _index(arguments: argparse.Namespace) -> None:
    globs = arguments.documentation_globs or DEFAULT_DOCUMENTATION_GLOBS
    document = build_index(arguments.root, globs).to_json()
    if arguments.out:
        arguments.out.write_text(document, encoding="utf-8")
    else:
        sys.stdout.write(document)


def _analyse(arguments: argparse.Namespace) -> None:
    if arguments.model not in PRICES:
        raise SystemExit(
            f"no price is recorded for {arguments.model}; the spend ceiling cannot "
            f"be enforced without one. Known models: {', '.join(sorted(PRICES))}"
        )
    # TODO: #11 decides what to do when the index is missing or older than the
    # base revision. Until then a missing one is rebuilt from the working tree,
    # which is the head revision rather than the base.
    index = (
        Index.from_json(arguments.index_path.read_text(encoding="utf-8"))
        if arguments.index_path
        else build_index(arguments.root)
    )
    model = ModelClient(
        OpenAIModel(arguments.model),
        pricing=PRICES[arguments.model],
        ceiling=arguments.ceiling,
        cache=arguments.cache,
    )
    report = analyse(arguments.root, index, arguments.base, arguments.head, model)
    sys.stdout.write(render(report))


def render(report: Report) -> str:
    """Render a report for a terminal."""
    drifted = len(report.findings)
    lines = [
        f"Checked {len(report.checked)} section{_plural(len(report.checked))}; "
        f"{drifted} {'has' if drifted == 1 else 'have'} drifted.",
        "",
    ]
    for finding in report.findings:
        lines += [f"{finding.section}  ({finding.chunk})", f"    {finding.explanation}", ""]
    spend = report.spend
    lines.append(
        f"{spend.calls} model call{_plural(spend.calls)}, "
        f"{spend.input_tokens} tokens in, {spend.output_tokens} out, "
        f"${spend.dollars:.4f} spent."
    )
    return "\n".join(lines) + "\n"


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


if __name__ == "__main__":
    main()
