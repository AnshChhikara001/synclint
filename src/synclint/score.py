"""Scoring the corpus: every finding a run produced, matched against ground truth."""

from __future__ import annotations

from dataclasses import dataclass, replace

from synclint.analyse import Finding, analyse, suspects
from synclint.corpus import BASE_REF, Corpus, case_ref, decoy_ref
from synclint.embeddings import EmbeddingClient
from synclint.index import Index, build_index
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
class Linking:
    """What one set of link mechanisms reaches in the corpus, and what it costs.

    `linked` and `unlinked` name the cases whose planted section and chunk
    the index does and does not link. The suspects are summed over every case
    and every decoy branch: each is one question a run would put to the model,
    so they are the price of a link, and on a decoy every one of them is a
    chance for a false positive.
    """

    pairs: int
    linked: tuple[str, ...]
    unlinked: tuple[str, ...]
    case_suspects: int
    decoy_suspects: int

    @property
    def planted(self) -> int:
        return len(self.linked) + len(self.unlinked)

    @property
    def recall(self) -> float | None:
        """How many planted pairs the index links, or `None` over no cases."""
        return len(self.linked) / self.planted if self.planted else None


@dataclass(frozen=True)
class LinkRecall:
    """Link recall by name matching alone, and with embedding similarity added.

    The embedding figures are what embedding the corpus's sections and chunks
    cost to record, whether this run recorded them or replayed them, and the
    calls this run actually made.
    """

    threshold: float
    by_name: Linking
    with_embeddings: Linking
    embedding_calls: int
    embedding_tokens: int
    embedding_dollars: float


def measure_links(
    corpus: Corpus, embeddings: EmbeddingClient, threshold: float
) -> LinkRecall:
    """Measure the planted pairs each linking mechanism reaches, and at what cost.

    Free apart from the embeddings: no model is asked anything, because a link
    either joins the planted section to the planted chunk or it does not. One
    index is built with both mechanisms and the name-only figures are read
    from its name links, so the two are measured over the same chunks and
    sections and differ only in what the embeddings added.
    """
    index = build_index(corpus.root, embeddings=embeddings, threshold=threshold)
    return LinkRecall(
        threshold=threshold,
        by_name=_linking(corpus, _only(index, "name")),
        with_embeddings=_linking(corpus, index),
        embedding_calls=embeddings.calls,
        embedding_tokens=embeddings.tokens,
        embedding_dollars=embeddings.dollars,
    )


def _only(index: Index, mechanism: str) -> Index:
    return replace(
        index, links=tuple(link for link in index.links if link.mechanism == mechanism)
    )


def _linking(corpus: Corpus, index: Index) -> Linking:
    pairs = {(link.section, link.chunk) for link in index.links}
    linked: list[str] = []
    unlinked: list[str] = []
    for case in corpus.cases:
        (linked if (case.section, case.chunk) in pairs else unlinked).append(case.id)
    return Linking(
        pairs=len(pairs),
        linked=tuple(linked),
        unlinked=tuple(unlinked),
        case_suspects=sum(
            len(suspects(corpus.root, index, BASE_REF, case_ref(case.id)))
            for case in corpus.cases
        ),
        decoy_suspects=sum(
            len(suspects(corpus.root, index, BASE_REF, decoy_ref(decoy.id)))
            for decoy in corpus.decoys
        ),
    )


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
