"""Scoring the corpus: every finding a run produced, matched against ground truth."""

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

    branches: tuple[Scored, ...]
    unrecorded: tuple[str, ...]
    unfinished: int
    spend: Spend

    @property
    def true_positives(self) -> int:
        """Planted findings the run reported."""
        return sum(1 for branch in self.branches if branch.found)

    @property
    def false_negatives(self) -> int:
        """Planted findings the run missed."""
        return sum(1 for branch in self.branches if not branch.decoy and not branch.found)

    @property
    def false_positives(self) -> int:
        """Findings nothing planted, on a decoy branch or beside a planted one."""
        return sum(len(branch.spurious) for branch in self.branches)

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


@dataclass(frozen=True)
class _Branch:
    """One branch to run, and the finding it owes.

    `planted` is the section and chunk a case is expected to invalidate, and is
    `None` for a decoy, which owes nothing. Carrying the difference as data
    rather than as two loops is what lets a decoy be scored by the same rule as
    a case: no finding matches what was never planted.
    """

    id: str
    kind: str
    decoy: bool
    ref: str
    planted: tuple[str, str] | None


def score_corpus(corpus: Corpus, model: ModelClient) -> Score:
    """Run `analyse` over every case and decoy branch and match the findings to ground truth.

    One index, built at the base, serves every branch, and one client is shared
    across all of them, so a prompt two branches have in common is asked once
    and the spend reported is the whole run's.

    A branch whose answers are not recorded is skipped and named in the score
    rather than allowed to spend: replaying the corpus has to be free, and a
    number computed over the branches that happened to be recorded would be a
    different measurement every time.
    """
    index = build_index(corpus.root)
    scored: list[Scored] = []
    unrecorded: list[str] = []
    unfinished = 0

    for branch in _branches(corpus):
        try:
            report = analyse(corpus.root, index, BASE_REF, branch.ref, model)
        except AnswerNotRecorded:
            unrecorded.append(branch.id)
            continue
        unfinished += report.unchecked
        reported = {(finding.section, finding.chunk) for finding in report.findings}
        scored.append(
            Scored(
                id=branch.id,
                kind=branch.kind,
                decoy=branch.decoy,
                verified=len(report.verified),
                found=branch.planted in reported,
                spurious=tuple(
                    finding
                    for finding in report.findings
                    if (finding.section, finding.chunk) != branch.planted
                ),
            )
        )

    return Score(
        branches=tuple(scored),
        unrecorded=tuple(unrecorded),
        unfinished=unfinished,
        spend=model.spend,
    )


def _branches(corpus: Corpus) -> list[_Branch]:
    """Every branch to run, cases first, in the order the manifest writes them down."""
    return [
        _Branch(
            id=case.id,
            kind=case.kind,
            decoy=False,
            ref=case_ref(case.id),
            planted=(case.section, case.chunk),
        )
        for case in corpus.cases
    ] + [
        _Branch(
            id=decoy.id,
            kind=decoy.kind,
            decoy=True,
            ref=decoy_ref(decoy.id),
            planted=None,
        )
        for decoy in corpus.decoys
    ]
