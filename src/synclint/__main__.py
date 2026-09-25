"""Command line: build an index, analyse a change against one, or score the corpus."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from synclint.analyse import Report, analyse
from synclint.confidence import DEFAULT_THRESHOLD, SHAPES
from synclint.corpus import (
    DECOY_KINDS,
    KINDS,
    Audit,
    Corpus,
    audit_corpus,
    build_corpus,
)
from synclint.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_PRICES,
    EmbeddingClient,
    OpenAIEmbedder,
)
from synclint.index import (
    DEFAULT_DOCUMENTATION_GLOBS,
    DEFAULT_SIMILARITY_THRESHOLD,
    Index,
    build_index,
)
from synclint.github import GitHub
from synclint.model import (
    DEFAULT_MODEL,
    PRICES,
    AnswerNotRecorded,
    ModelClient,
    OpenAIModel,
)
from synclint.publish import publish
from synclint.score import (
    LinkRecall,
    Linking,
    RepairOutcome,
    Score,
    Scored,
    measure_links,
    score_corpus,
)

# A run checks a handful of sections, which at the default model costs cents.
# The ceiling is here for the run that is not typical — a pull request touching
# hundreds of chunks — and is the first thing to raise if an honest run hits it.
DEFAULT_CEILING = 0.25

DEFAULT_CACHE = Path(".cache/synclint")

# The corpus's recorded answers live with the ground truth they are scored
# against, and are committed: a published accuracy figure that cannot be
# recomputed by whoever is reading it is a claim rather than a measurement.
ANSWERS = Path("answers")

# Committed beside the answers, and for the same reason.
EMBEDDINGS = Path("embeddings")


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
    index_parser.add_argument(
        "--embed",
        action="store_true",
        help=f"link by {DEFAULT_EMBEDDING_MODEL} similarity as well as by name; costs money",
    )
    _add_threshold(index_parser)
    index_parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE / EMBEDDINGS,
        help=f"directory of recorded embeddings; defaults to {DEFAULT_CACHE / EMBEDDINGS}",
    )

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

    _add_confidence_threshold(analyse_parser)
    analyse_parser.add_argument(
        "--pull-request",
        type=int,
        dest="pull_request",
        metavar="NUMBER",
        help=(
            "publish the report to this pull request: a summary comment, and a "
            "pull request of the repairs. Needs GITHUB_TOKEN in the environment"
        ),
    )
    analyse_parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="the repository as owner/name; defaults to GITHUB_REPOSITORY",
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
    _add_threshold(score_parser)
    _add_confidence_threshold(score_parser)

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


def _add_threshold(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help=(
            "cosine similarity at or above which a section and a chunk are linked "
            f"by embedding; defaults to {DEFAULT_SIMILARITY_THRESHOLD}"
        ),
    )


def _add_confidence_threshold(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--confidence-threshold",
        type=_proportion,
        default=DEFAULT_THRESHOLD,
        dest="confidence_threshold",
        help=(
            "the model's confidence, from 0 to 1, that a repair inside the gate "
            "needs before it is proposed; defaults to "
            f"{DEFAULT_THRESHOLD}. No value proposes a repair outside the gate"
        ),
    )


def _proportion(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError(f"{text} is not between 0 and 1")
    return value


def _embedding_client(cache: Path) -> EmbeddingClient:
    return EmbeddingClient(
        OpenAIEmbedder(DEFAULT_EMBEDDING_MODEL),
        price=EMBEDDING_PRICES[DEFAULT_EMBEDDING_MODEL],
        cache=cache,
    )


def _index(arguments: argparse.Namespace) -> None:
    globs = arguments.documentation_globs or DEFAULT_DOCUMENTATION_GLOBS
    embeddings = _embedding_client(arguments.cache) if arguments.embed else None
    document = build_index(
        arguments.root, globs, embeddings=embeddings, threshold=arguments.threshold
    ).to_json()
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
    answers = arguments.source / ANSWERS
    # Replaying needs no price, because it cannot spend. Recording does, and
    # the check has to happen before the corpus is built rather than after.
    model = (
        _paying_client(arguments, answers)
        if arguments.record
        else ModelClient.replaying(arguments.model, answers)
    )
    vectors = arguments.source / EMBEDDINGS
    embeddings = (
        _embedding_client(vectors)
        if arguments.record
        else EmbeddingClient.replaying(DEFAULT_EMBEDDING_MODEL, vectors)
    )
    with tempfile.TemporaryDirectory() as directory:
        corpus = build_corpus(arguments.source, Path(directory) / "built")
        # The audit gates the score. A number measured over a corpus with a
        # fault in it would be measuring the corpus's mistakes as synclint's.
        audit = audit_corpus(corpus)
        if audit.faults:
            sys.stdout.write(render_audit(audit))
            raise SystemExit(1)
        # TODO: findings are still scored over name links alone, because the
        # suspects embedding links add have no recorded answers yet. Recording
        # them is a paid run, and the link table below says whether it is worth
        # paying for before anyone does.
        score = score_corpus(corpus, model, threshold=arguments.confidence_threshold)
        try:
            links: LinkRecall | None = measure_links(corpus, embeddings, arguments.threshold)
        except AnswerNotRecorded:
            links = None
    sys.stdout.write(render_score(score))
    sys.stdout.write("\n" + (render_links(links) if links else _UNEMBEDDED))
    if score.unrecorded or score.unfinished or links is None:
        raise SystemExit(1)


_UNEMBEDDED = (
    "Not every section and chunk has a recorded embedding, so there is no link "
    "recall. "
    "Record them with --record, then score again.\n"
)


def _paying_client(arguments: argparse.Namespace, cache: Path) -> ModelClient:
    """A client that can spend, refused outright for a model with no published price.

    The ceiling is only as honest as the price behind it, so a model synclint
    has never been run against is turned away rather than priced at zero.
    """
    if arguments.model not in PRICES:
        raise SystemExit(
            f"no price is recorded for {arguments.model}; the spend ceiling cannot "
            f"be enforced without one. Known models: {', '.join(sorted(PRICES))}"
        )
    return ModelClient(
        OpenAIModel(arguments.model),
        pricing=PRICES[arguments.model],
        ceiling=arguments.ceiling,
        cache=cache,
    )


def _analyse(arguments: argparse.Namespace) -> None:
    # Checked before anything is spent: a report that cannot be published has
    # been paid for and thrown away.
    github = _github(arguments) if arguments.pull_request is not None else None
    model = _paying_client(arguments, arguments.cache)
    # TODO: #11 decides what to do when the index is missing or older than the
    # base revision. Until then a missing one is rebuilt from the working tree,
    # which is the head revision rather than the base.
    globs = arguments.documentation_globs or DEFAULT_DOCUMENTATION_GLOBS
    index = (
        Index.from_json(arguments.index_path.read_text(encoding="utf-8"))
        if arguments.index_path
        else build_index(arguments.root, globs)
    )
    report = analyse(
        arguments.root,
        index,
        arguments.base,
        arguments.head,
        model,
        threshold=arguments.confidence_threshold,
    )
    sys.stdout.write(render(report))
    if github is not None:
        comment = publish(
            report, arguments.root, github, arguments.repository, arguments.pull_request
        )
        sys.stdout.write(f"Published to {comment}\n")
    if report.unchecked or report.unrepaired:
        raise SystemExit(1)


def _github(arguments: argparse.Namespace) -> GitHub:
    if not arguments.repository:
        raise SystemExit("--pull-request needs --repository or GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("--pull-request needs a token in GITHUB_TOKEN")
    return GitHub(token)


def render(report: Report) -> str:
    """Render a report for a terminal."""
    # Counted over sections rather than findings: one section can drift against
    # two changed chunks, and "1 section; 2 have drifted" reads as nonsense.
    drifted = len({f.section for f in report.findings if f.kind == "drift"})
    gone = len({f.section for f in report.findings if f.kind == "disappearance"})
    headline = (
        f"Verified {len(report.verified)} section{_plural(len(report.verified))}; "
        f"{drifted} {'has' if drifted == 1 else 'have'} drifted."
    )
    if gone:
        headline += f" {gone} {'names' if gone == 1 else 'name'} code the change deleted."
    lines = [headline, ""]
    repairs = {repair.finding: repair for repair in report.repairs}
    flags = {flag.finding: flag for flag in report.flags}
    for finding in report.findings:
        deleted = ", deleted" if finding.kind == "disappearance" else ""
        lines += [
            f"{finding.section}  ({finding.chunk}{deleted})",
            f"    {finding.explanation}",
            "",
        ]
        if finding in repairs:
            proposed = repairs[finding]
            lines += [f"    {line}" for line in proposed.diff.splitlines()]
            lines.append(
                f"    Proposed: a {proposed.shape}, {proposed.confidence:.0%} confident."
            )
        else:
            lines.append(f"    Flagged: {flags[finding].reason}")
        lines.append("")
    if report.unchecked:
        lines += [
            f"Stopped at the spend ceiling with {report.unchecked} "
            f"suspect{_plural(report.unchecked)} unverified. Raise --ceiling to go on.",
            "",
        ]
    elif report.unrepaired:
        lines += [
            f"Stopped at the spend ceiling with {report.unrepaired} "
            f"finding{_plural(report.unrepaired)} unrepaired. Raise --ceiling to go on.",
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
        # A deleted chunk's section is reached as a disappearance rather than
        # a suspect; either is the most a run can find before the model.
        f"{reached} of {total} reachable",
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
    lines = _kind_lines(score) + [""] + _rate_lines(score)
    if any(branch.decoy for branch in score.branches):
        lines += ["", *_decoy_kind_lines(score), "", _decoy_line(score)]
    if score.false_positives:
        lines += [""] + _false_positive_lines(score)
    if any(branch.repair for branch in score.branches):
        lines += ["", *_repair_lines(score), "", *_calibration_lines(score)]
    lines += [
        "",
        f"{score.spend.calls} model call{_plural(score.spend.calls)}, "
        f"${score.spend.dollars:.4f} spent.",
    ]
    return "\n".join(lines) + "\n"


def render_links(measured: LinkRecall) -> str:
    """Render link recall for both mechanisms as markdown for the README.

    The suspect columns sit beside recall because a link is not free: every
    extra one is a question a run pays to ask, and on a decoy a chance to be
    wrong. A recall gain that doubles the questions is a different finding
    from one that costs nothing, and the table should not hide which it is.
    """
    rows = [
        ("name", measured.by_name),
        (f"name + embedding ≥ {measured.threshold}", measured.with_embeddings),
    ]
    lines = [
        "| Links | Pairs linked | Planted pairs linked | Link recall "
        "| Suspects on cases | Suspects on decoys |",
        "| --- | --- | --- | --- | --- | --- |",
        *(_linking_row(label, linking) for label, linking in rows),
    ]
    if measured.with_embeddings.unlinked:
        lines += [
            "",
            "Linked by neither: " + ", ".join(measured.with_embeddings.unlinked) + ".",
        ]
    lines += [
        "",
        f"Embedding the corpus: {measured.embedding_tokens} tokens, "
        f"${measured.embedding_dollars:.6f} at {DEFAULT_EMBEDDING_MODEL}'s price; "
        f"{measured.embedding_calls} call{_plural(measured.embedding_calls)} this run.",
    ]
    return "\n".join(lines) + "\n"


def _linking_row(label: str, linking: Linking) -> str:
    return (
        f"| {label} | {linking.pairs} | {len(linking.linked)} of {linking.planted} | "
        f"{_rate(linking.recall)} | {linking.case_suspects} | {linking.decoy_suspects} |"
    )


def _gap_lines(score: Score) -> list[str]:
    reasons: list[list[str]] = []
    if score.unrecorded:
        one = len(score.unrecorded) == 1
        reasons.append(
            [
                f"{len(score.unrecorded)} branch{'' if one else 'es'} "
                f"{'has' if one else 'have'} no recorded answer:",
                *(f"    {entry}" for entry in score.unrecorded),
                "",
                "Record them with --record, then score again.",
            ]
        )
    if score.unfinished:
        reasons.append(
            [
                f"{score.unfinished} suspects or findings were left unverified "
                "or unrepaired at the spend ceiling. Raise --ceiling to go on."
            ]
        )
    lines = ["The corpus was not scored in full, so there are no numbers."]
    for reason in reasons:
        lines += ["", *reason]
    return lines


def _kind_lines(score: Score) -> list[str]:
    cases = [branch for branch in score.branches if not branch.decoy]
    found = Counter(branch.kind for branch in cases if branch.found)
    planted = Counter(branch.kind for branch in cases)
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


def _rate_lines(score: Score) -> list[str]:
    # All three counts are named rather than left to be subtracted: a false
    # negative is the number this project is worst at and least able to hide.
    return [
        f"Precision {_rate(score.precision)} ({score.true_positives} true "
        f"positive{_plural(score.true_positives)}, {score.false_positives} false "
        f"positive{_plural(score.false_positives)}). "
        f"Recall {_rate(score.recall)} ({score.true_positives} true "
        f"positive{_plural(score.true_positives)}, {score.false_negatives} false "
        f"negative{_plural(score.false_negatives)})."
    ]


def _decoy_kind_lines(score: Score) -> list[str]:
    """The decoy half of the breakdown: what each kind reaches, and what it cost.

    Reach is carried beside the false positives because a kind with none of
    either has earned nothing — six of the ten decoys cannot produce a finding
    whatever a model says, and a table without that column would read as though
    the judgement had cleared them.
    """
    decoys = [branch for branch in score.branches if branch.decoy]
    counts = Counter(branch.kind for branch in decoys)
    declared = list(DECOY_KINDS)
    ordered = declared + [kind for kind in counts if kind not in declared]
    rows = [
        f"| {kind} | {counts[kind]} | {_reaching(decoys, kind)} | "
        f"{_spurious(decoys, kind)} |"
        for kind in ordered
        if counts[kind]
    ]
    return [
        "| Decoy kind | Decoys | Reach the model | False positives |",
        "| --- | --- | --- | --- |",
        *rows,
        f"| All | {len(decoys)} | {_reaching(decoys, None)} | "
        f"{_spurious(decoys, None)} |",
    ]


def _reaching(decoys: list[Scored], kind: str | None) -> int:
    return sum(
        1 for branch in decoys if branch.verified and kind in (None, branch.kind)
    )


def _spurious(decoys: list[Scored], kind: str | None) -> int:
    return sum(
        len(branch.spurious) for branch in decoys if kind in (None, branch.kind)
    )


def _decoy_line(score: Score) -> str:
    decoys = [branch for branch in score.branches if branch.decoy]
    # Split because the two halves measure different things: a decoy that
    # raises no suspect was ruled out by the design and costs nothing to be
    # right about, and only the rest put the model's judgement to the test.
    silent = [branch for branch in decoys if not branch.verified]
    reaching = [branch for branch in decoys if branch.verified]
    caught = sum(1 for branch in reaching if branch.spurious)
    return (
        f"{len(silent)} of {len(decoys)} decoys "
        f"{'raises' if len(silent) == 1 else 'raise'} no suspect and cannot "
        f"produce a finding; of the {len(reaching)} that "
        f"{'reaches' if len(reaching) == 1 else 'reach'} the model, {caught} did."
    )


def _false_positive_lines(score: Score) -> list[str]:
    lines = ["| False positive | Section | Chunk |", "| --- | --- | --- |"]
    for branch in score.branches:
        prefix = "decoy" if branch.decoy else "case"
        lines += [
            f"| {prefix}/{branch.id} | {finding.section} | {finding.chunk} |"
            for finding in branch.spurious
        ]
    return lines


def _repair_lines(score: Score) -> list[str]:
    """Every planted finding's repair: what became of it beside the ground truth's verdict.

    Set side by side because validation, and the confidence inside the gate,
    are both a model's judgement, and the manifest is the only thing here that
    is not. A flagged repair is judged too: one the ground truth accepts is
    work thrown away, and without its row the flags would read as all deserved.
    """
    repaired = [(branch.id, branch.repair) for branch in score.branches if branch.repair]
    rows = [
        f"| {case} | {repair.shape or '—'} | {_OUTCOMES[repair.cause]} | "
        f"{_rate(repair.confidence)} | {_ground_truth(repair)} | {_rate(repair.kept)} |"
        for case, repair in repaired
    ]
    seen = [repair for _, repair in repaired if repair.kept is not None]
    passed = [repair for repair in seen if repair.cause != "refused"]
    outside = sum(1 for repair in passed if repair.cause == "outside")
    doubted = sum(1 for repair in passed if repair.cause == "doubted")
    proposed = [repair for repair in passed if repair.proposed]
    right = sum(1 for repair in proposed if repair.correct)
    unapplied = len(repaired) - len(seen)
    return [
        "| Repaired case | Shape | Outcome | Confidence | Ground truth | Text kept |",
        "| --- | --- | --- | --- | --- | --- |",
        *rows,
        "",
        f"{len(repaired)} repair{_plural(len(repaired))}, {unapplied} of which "
        f"could not be applied. Validation passed {len(passed)} of the "
        f"{len(seen)} it saw; of those, {outside} {'was' if outside == 1 else 'were'} "
        f"outside the gate and {doubted} fell short of the "
        f"{_rate(score.threshold)} threshold. {len(proposed)} "
        f"{'was' if len(proposed) == 1 else 'were'} proposed, {right} of them correct.",
    ]


_OUTCOMES: dict[str | None, str] = {
    None: "proposed",
    "refused": "refused by validation",
    "unappliable": "not applied",
    "outside": "outside the gate",
    "doubted": "below threshold",
    "ceiling": "cut off at the ceiling",
}


def _calibration_lines(score: Score) -> list[str]:
    """How often a repair of each eligible shape was right, and how often it was proposed.

    Counted over every repair, proposed or not, because the question the gate
    answers is whether a shape is safe to repair at all; the proposed columns
    are what the threshold then made of it. A repair whose edits could not be
    applied counts as one that was not correct. The row outside the gate is
    the comparison that says whether the gate is drawn in the right place.
    """
    repairs = [branch.repair for branch in score.branches if branch.repair]
    rows = [(shape.name, shape.name) for shape in SHAPES] + [("outside the gate", None)]
    lines = [
        f"| Shape | Repairs | Correct | Proposed at ≥ {_rate(score.threshold)} "
        "| Proposed and correct |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, name in rows:
        shaped = [repair for repair in repairs if repair.shape == name]
        correct = sum(1 for repair in shaped if repair.correct)
        proposed = [repair for repair in shaped if repair.proposed]
        lines.append(
            f"| {label} | {len(shaped)} | {correct} ({_percent(correct, len(shaped))}) | "
            f"{len(proposed)} | {sum(1 for repair in proposed if repair.correct)} |"
        )
    return lines


def _ground_truth(repair: RepairOutcome) -> str:
    if repair.kept is None:
        return "not applied"
    return "correct" if repair.correct else "incorrect"


def _rate(rate: float | None) -> str:
    # Nothing reported means nothing to be right or wrong about, and a zero
    # there would read as a measurement rather than as the absence of one.
    return "—" if rate is None else f"{rate * 100:.0f}%"


def _percent(part: int, whole: int) -> str:
    return _rate(part / whole if whole else None)


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
