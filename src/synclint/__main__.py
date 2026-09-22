"""Command line: build an index, analyse a change against one, or score the corpus."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from synclint.analyse import Report, analyse
from synclint.corpus import (
    DECOY_KINDS,
    KINDS,
    Audit,
    Corpus,
    audit_corpus,
    build_corpus,
)
from synclint.index import DEFAULT_DOCUMENTATION_GLOBS, Index, build_index
from synclint.model import DEFAULT_MODEL, PRICES, ModelClient, OpenAIModel
from synclint.score import Score, Scored, score_corpus

# A run checks a handful of sections, which at the default model costs cents.
# The ceiling is here for the run that is not typical — a pull request touching
# hundreds of chunks — and is the first thing to raise if an honest run hits it.
DEFAULT_CEILING = 0.25

DEFAULT_CACHE = Path(".cache/synclint")

# The corpus's recorded answers live with the ground truth they are scored
# against, and are committed: a published accuracy figure that cannot be
# recomputed by whoever is reading it is a claim rather than a measurement.
ANSWERS = Path("answers")


def main(argv: Sequence[str] | None = None) -> None:
    """Run the subcommand named on the command line.

    `argv` defaults to the real one. Tests pass their own, so that the
    wiring each subcommand does before it reaches a renderer — which model
    client, which gate, which exit code — is covered by something.
    """
    parser = argparse.ArgumentParser(
        prog="synclint",
        description="Find the documentation a code change has made inaccurate.",
    )
    subcommands = parser.add_subparsers(dest="subcommand", required=True)

    index_parser = subcommands.add_parser("index", help="index a repository")
    index_parser.add_argument("root", type=Path, help="the repository to index")
    index_parser.add_argument(
        "--out",
        type=Path,
        help="file to write the index to; defaults to standard output",
    )
    _add_documentation_glob(index_parser)

    analyse_parser = subcommands.add_parser(
        "analyse", help="find the documentation a change invalidated"
    )
    analyse_parser.add_argument("root", type=Path, help="the repository to analyse")
    analyse_parser.add_argument("--base", required=True, help="the revision changed from")
    analyse_parser.add_argument("--head", required=True, help="the revision changed to")
    analyse_parser.add_argument(
        "--index",
        type=Path,
        dest="index_path",
        help="the index to analyse against; built in memory if not given",
    )
    # An index built in memory has to be built the way the committed one was,
    # so this subcommand takes the glob too.
    _add_documentation_glob(analyse_parser)
    analyse_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"the model to ask; defaults to {DEFAULT_MODEL}",
    )
    analyse_parser.add_argument(
        "--ceiling",
        type=float,
        default=DEFAULT_CEILING,
        help=f"dollars this run may spend before it stops; defaults to {DEFAULT_CEILING}",
    )
    analyse_parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"directory of recorded model answers; defaults to {DEFAULT_CACHE}",
    )

    corpus_parser = subcommands.add_parser(
        "corpus", help="audit the fixture corpus against its manifest"
    )
    corpus_parser.add_argument("source", type=Path, help="the corpus to audit")
    corpus_parser.add_argument(
        "--build-to",
        type=Path,
        dest="build_to",
        help="where to leave the built repository; a temporary directory by default",
    )

    score_parser = subcommands.add_parser(
        "score", help="score the fixture corpus against its ground truth"
    )
    score_parser.add_argument("source", type=Path, help="the corpus to score")
    score_parser.add_argument(
        "--answers",
        type=Path,
        help=(
            "directory of recorded model answers to replay; "
            f"defaults to {ANSWERS} under the corpus"
        ),
    )
    score_parser.add_argument(
        "--record",
        action="store_true",
        help="ask the model for the answers that are missing and record them; costs money",
    )
    score_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"the model whose answers are scored; defaults to {DEFAULT_MODEL}",
    )
    score_parser.add_argument(
        "--ceiling",
        type=float,
        default=DEFAULT_CEILING,
        help=f"dollars a recording run may spend; defaults to {DEFAULT_CEILING}",
    )

    arguments = parser.parse_args(argv)
    if arguments.subcommand == "index":
        _index(arguments)
    elif arguments.subcommand == "corpus":
        _corpus(arguments)
    elif arguments.subcommand == "score":
        _score(arguments)
    else:
        _analyse(arguments)


def _add_documentation_glob(parser: argparse.ArgumentParser) -> None:
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


def _index(arguments: argparse.Namespace) -> None:
    globs = arguments.documentation_globs or DEFAULT_DOCUMENTATION_GLOBS
    document = build_index(arguments.root, globs).to_json()
    if arguments.out:
        arguments.out.write_text(document, encoding="utf-8")
    else:
        sys.stdout.write(document)


def _corpus(arguments: argparse.Namespace) -> None:
    if arguments.build_to:
        _audit(build_corpus(arguments.source, arguments.build_to))
        return
    with tempfile.TemporaryDirectory() as directory:
        _audit(build_corpus(arguments.source, Path(directory) / "built"))


def _audit(corpus: Corpus) -> None:
    audit = audit_corpus(corpus)
    sys.stdout.write(render_audit(audit))
    if audit.faults:
        raise SystemExit(1)


def _score(arguments: argparse.Namespace) -> None:
    if arguments.record and arguments.model not in PRICES:
        raise SystemExit(
            f"no price is recorded for {arguments.model}; the spend ceiling cannot "
            f"be enforced without one. Known models: {', '.join(sorted(PRICES))}"
        )
    answers = arguments.answers or arguments.source / ANSWERS
    with tempfile.TemporaryDirectory() as directory:
        corpus = build_corpus(arguments.source, Path(directory) / "built")
        # The audit gates the score. A number measured over a corpus with a
        # fault in it would be measuring the corpus's mistakes as synclint's.
        audit = audit_corpus(corpus)
        if audit.faults:
            sys.stdout.write(render_audit(audit))
            raise SystemExit(1)
        score = score_corpus(corpus, _scoring_model(arguments, answers))
    sys.stdout.write(render_score(score))
    if score.unrecorded or score.unfinished:
        raise SystemExit(1)


def _scoring_model(arguments: argparse.Namespace, answers: Path) -> ModelClient:
    if not arguments.record:
        return ModelClient.replaying(arguments.model, answers)
    return ModelClient(
        OpenAIModel(arguments.model),
        pricing=PRICES[arguments.model],
        ceiling=arguments.ceiling,
        cache=answers,
    )


def _analyse(arguments: argparse.Namespace) -> None:
    if arguments.model not in PRICES:
        raise SystemExit(
            f"no price is recorded for {arguments.model}; the spend ceiling cannot "
            f"be enforced without one. Known models: {', '.join(sorted(PRICES))}"
        )
    # TODO: #11 decides what to do when the index is missing or older than the
    # base revision. Until then a missing one is rebuilt from the working tree,
    # which is the head revision rather than the base.
    globs = arguments.documentation_globs or DEFAULT_DOCUMENTATION_GLOBS
    index = (
        Index.from_json(arguments.index_path.read_text(encoding="utf-8"))
        if arguments.index_path
        else build_index(arguments.root, globs)
    )
    model = ModelClient(
        OpenAIModel(arguments.model),
        pricing=PRICES[arguments.model],
        ceiling=arguments.ceiling,
        cache=arguments.cache,
    )
    report = analyse(arguments.root, index, arguments.base, arguments.head, model)
    sys.stdout.write(render(report))
    if report.unchecked:
        raise SystemExit(1)


def render(report: Report) -> str:
    """Render a report for a terminal."""
    # Counted over sections rather than findings: one section can drift against
    # two changed chunks, and "1 section; 2 have drifted" reads as nonsense.
    drifted = len({finding.section for finding in report.findings})
    lines = [
        f"Verified {len(report.verified)} section{_plural(len(report.verified))}; "
        f"{drifted} {'has' if drifted == 1 else 'have'} drifted.",
        "",
    ]
    for finding in report.findings:
        lines += [
            f"{finding.section}  ({finding.chunk})",
            f"    {finding.explanation}",
            "",
        ]
    if report.unchecked:
        lines += [
            f"Stopped at the spend ceiling with {report.unchecked} "
            f"suspect{_plural(report.unchecked)} unverified. Raise --ceiling to go on.",
            "",
        ]
    spend = report.spend
    lines.append(
        f"{spend.calls} model call{_plural(spend.calls)}, "
        f"{spend.input_tokens} tokens in, {spend.output_tokens} out, "
        f"${spend.dollars:.4f} spent."
    )
    return "\n".join(lines) + "\n"


def render_audit(audit: Audit) -> str:
    """Render an audited corpus for a terminal."""
    lines = _case_lines(audit) + [""]
    if audit.decoys:
        lines += _decoy_lines(audit) + [""]
    if audit.faults:
        lines.append(f"{len(audit.faults)} fault{_plural(len(audit.faults))}:")
        lines += [f"    {fault}" for fault in audit.faults]
    else:
        lines.append("No faults.")
    return "\n".join(lines) + "\n"


def _case_lines(audit: Audit) -> list[str]:
    reached = len(audit.reachable)
    total = reached + len(audit.unreachable)
    lines = [
        f"{len(audit.cases)} case{_plural(len(audit.cases))}: "
        f"{_shape(case.kind for case in audit.cases)}.",
        f"{reached} of {total} reachable as "
        f"{'a suspect' if reached == 1 else 'suspects'}",
    ]
    if audit.unreachable:
        lines[-1] += f"; {len(audit.unreachable)} unreachable:"
        lines += [f"    {case}" for case in audit.unreachable]
    else:
        lines[-1] += "."
    return lines


def _decoy_lines(audit: Audit) -> list[str]:
    # No ratio: a decoy with a fault is not measured, so "4 of 10" would be
    # counting decoys the audit never put a question to.
    lines = [
        f"{len(audit.decoys)} decoy{_plural(len(audit.decoys))}: "
        f"{_shape((decoy.kind for decoy in audit.decoys), order=DECOY_KINDS)}.",
    ]
    if audit.suspected:
        reaching = len(audit.suspected)
        lines.append(
            f"{reaching} {'reaches' if reaching == 1 else 'reach'} the model, "
            "where a false positive is still possible:"
        )
        lines += [f"    {decoy}" for decoy in audit.suspected]
    else:
        lines.append("None reaches the model, so none can produce a finding.")
    return lines


def render_score(score: Score) -> str:
    """Render a scored corpus as markdown that can be pasted into the README.

    A run that did not finish prints its gaps and no numbers at all. Precision
    and recall over the branches that happened to be answered would be a
    different measurement every time, and it would not say so on the page.
    """
    if score.unrecorded or score.unfinished:
        return "\n".join(_gap_lines(score)) + "\n"
    cases = [result for result in score.results if not result.decoy]
    lines = _kind_lines(cases) + [""] + _rate_lines(score, cases)
    if any(result.decoy for result in score.results):
        lines += ["", _decoy_line(score)]
    if score.false_positives:
        lines += [""] + _false_positive_lines(score)
    lines += [
        "",
        f"{score.spend.calls} model call{_plural(score.spend.calls)}, "
        f"${score.spend.dollars:.4f} spent.",
    ]
    return "\n".join(lines) + "\n"


def _gap_lines(score: Score) -> list[str]:
    lines = ["The corpus was not scored in full, so there are no numbers.", ""]
    if score.unrecorded:
        one = len(score.unrecorded) == 1
        lines += [
            f"{len(score.unrecorded)} branch{'' if one else 'es'} "
            f"{'has' if one else 'have'} no recorded answer:"
        ]
        lines += [f"    {entry}" for entry in score.unrecorded]
        lines += ["", "Record them with --record, then score again.", ""]
    if score.unfinished:
        lines += [
            f"{score.unfinished} suspect{_plural(score.unfinished)} were left "
            "unverified at the spend ceiling. Raise --ceiling to go on.",
            "",
        ]
    return lines[:-1]


def _kind_lines(cases: list[Scored]) -> list[str]:
    found = Counter(result.kind for result in cases if result.found)
    planted = Counter(result.kind for result in cases)
    declared = list(KINDS)
    ordered = declared + [kind for kind in planted if kind not in declared]
    rows = [
        f"| {kind} | {planted[kind]} | {found[kind]} | {_percent(found[kind], planted[kind])} |"
        for kind in ordered
        if planted[kind]
    ]
    total = sum(planted.values())
    return [
        "| Drift kind | Planted | Found | Recall |",
        "| --- | --- | --- | --- |",
        *rows,
        f"| All | {total} | {sum(found.values())} | "
        f"{_percent(sum(found.values()), total)} |",
    ]


def _rate_lines(score: Score, cases: list[Scored]) -> list[str]:
    return [
        f"Precision {_rate(score.precision)} ({score.true_positives} true "
        f"positive{_plural(score.true_positives)}, {score.false_positives} false "
        f"positive{_plural(score.false_positives)}). "
        f"Recall {_rate(score.recall)} ({score.true_positives} of {len(cases)} "
        f"planted case{_plural(len(cases))})."
    ]


def _decoy_line(score: Score) -> str:
    decoys = [result for result in score.results if result.decoy]
    # Split because the two halves measure different things: a decoy that
    # raises no suspect was ruled out by the design and costs nothing to be
    # right about, and only the rest put the model's judgement to the test.
    silent = [result for result in decoys if not result.verified]
    reaching = [result for result in decoys if result.verified]
    caught = sum(1 for result in reaching if result.spurious)
    return (
        f"{len(silent)} of {len(decoys)} decoys "
        f"{'raises' if len(silent) == 1 else 'raise'} no suspect and cannot "
        f"produce a finding; of the {len(reaching)} that "
        f"{'reaches' if len(reaching) == 1 else 'reach'} the model, {caught} did."
    )


def _false_positive_lines(score: Score) -> list[str]:
    lines = ["| False positive | Section | Chunk |", "| --- | --- | --- |"]
    for result in score.results:
        prefix = "decoy" if result.decoy else "case"
        lines += [
            f"| {prefix}/{result.id} | {finding.section} | {finding.chunk} |"
            for finding in result.spurious
        ]
    return lines


def _rate(rate: float | None) -> str:
    # Nothing reported means nothing to be right or wrong about, and a zero
    # there would read as a measurement rather than as the absence of one.
    return "not measured" if rate is None else f"{rate * 100:.0f}%"


def _percent(part: int, whole: int) -> str:
    return "—" if whole == 0 else f"{part / whole * 100:.0f}%"


def _shape(kinds: Iterable[str], order: Iterable[str] = KINDS) -> str:
    """How many of each kind there are, in the order the kinds are declared."""
    counts = Counter(kinds)
    # An unrecognised kind is a fault rather than a reason for this line to
    # disagree with the count beside it, so it is counted where it falls.
    declared = list(order)
    ordered = declared + [kind for kind in counts if kind not in declared]
    return ", ".join(f"{counts[kind]} {kind}" for kind in ordered if counts[kind])


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


if __name__ == "__main__":
    main()
