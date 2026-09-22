"""The accuracy harness: every finding the corpus produces, matched against ground truth."""

from __future__ import annotations

from dataclasses import dataclass

from synclint.analyse import Finding, analyse
from synclint.corpus import BASE_REF, Corpus, case_ref, decoy_ref
from synclint.index import build_index
from synclint.model import AnswerNotRecorded, ModelClient, Spend


@dataclass(frozen=True)
class Scored:
    """One branch of the corpus, run and matched against what the manifest expected.

    `found` is whether the finding the manifest planted came back. It is always
    false for a decoy, which plants none. `spurious` are the findings nothing
    asked for: on a decoy branch that is every finding, and on a case branch
    every finding but the planted one.

    `verified` counts the sections put to the model on this branch. Zero means
    the branch raised no suspect, so no answer of any kind could have produced
    a finding — the distinction between what the design rules out and what the
    model's judgement earned.
    """

    id: str
    kind: str
    decoy: bool
    verified: int
    found: bool
    spurious: tuple[Finding, ...]


@dataclass(frozen=True)
class Score:
    """What one run of the whole corpus measured.

    `unrecorded` names the branches whose answers are not on disk and
    `unfinished` counts the suspects a run stopped short of at its ceiling.
    Either one means the numbers below are computed over part of the corpus,
    so a caller that is about to publish them has to refuse.
    """

    results: tuple[Scored, ...]
    unrecorded: tuple[str, ...]
    unfinished: int
    spend: Spend

    @property
    def true_positives(self) -> int:
        """Planted findings the run reported."""
        return sum(1 for result in self.results if result.found)

    @property
    def false_negatives(self) -> int:
        """Planted findings the run missed."""
        return sum(1 for result in self.results if not result.decoy and not result.found)

    @property
    def false_positives(self) -> int:
        """Findings nothing planted, on a decoy branch or beside a planted one."""
        return sum(len(result.spurious) for result in self.results)

    @property
    def precision(self) -> float | None:
        """How much of what the run reported was real drift, or `None` if it reported none."""
        reported = self.true_positives + self.false_positives
        return self.true_positives / reported if reported else None

    @property
    def recall(self) -> float | None:
        """How much of the planted drift the run found, or `None` over no cases."""
        planted = self.true_positives + self.false_negatives
        return self.true_positives / planted if planted else None


def score_corpus(corpus: Corpus, model: ModelClient) -> Score:
    """Run `analyse` over every case and decoy branch and match the findings to ground truth.

    One index, built at the base, serves every branch, and one client is shared
    across all of them, so a prompt two branches have in common is asked once
    and the spend reported is the whole run's.

    A branch whose answers are not recorded is skipped and named in the score
    rather than allowed to spend: replaying the corpus has to be free, and a
    number computed over the branches that happened to be cached would be a
    different measurement every time.
    """
    index = build_index(corpus.root)
    results: list[Scored] = []
    unrecorded: list[str] = []
    unfinished = 0

    for case in corpus.cases:
        try:
            report = analyse(corpus.root, index, BASE_REF, case_ref(case.id), model)
        except AnswerNotRecorded:
            unrecorded.append(case.id)
            continue
        unfinished += report.unchecked
        planted = (case.section, case.chunk)
        results.append(
            Scored(
                id=case.id,
                kind=case.kind,
                decoy=False,
                verified=len(report.verified),
                found=planted in {(f.section, f.chunk) for f in report.findings},
                spurious=tuple(
                    finding
                    for finding in report.findings
                    if (finding.section, finding.chunk) != planted
                ),
            )
        )

    for decoy in corpus.decoys:
        try:
            report = analyse(corpus.root, index, BASE_REF, decoy_ref(decoy.id), model)
        except AnswerNotRecorded:
            unrecorded.append(decoy.id)
            continue
        unfinished += report.unchecked
        results.append(
            Scored(
                id=decoy.id,
                kind=decoy.kind,
                decoy=True,
                verified=len(report.verified),
                found=False,
                spurious=report.findings,
            )
        )

    return Score(
        results=tuple(results),
        unrecorded=tuple(unrecorded),
        unfinished=unfinished,
        spend=model.spend,
    )
