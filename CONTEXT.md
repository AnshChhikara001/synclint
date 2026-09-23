# synclint

A GitHub Action that detects when a code change has made documentation inaccurate, repairs what it can, and flags the rest for human review.

## Language

**Chunk**:
A unit of code the tool tracks and can link documentation to — a function, method, or class.
_Avoid_: node, symbol, snippet, entity

**Section**:
A unit of prose documentation, delimited by a markdown heading.
_Avoid_: block, fragment, passage, doc

**Link**:
A recorded relationship asserting that a section describes a chunk.
_Avoid_: edge, mapping, association, reference

**Mechanism**:
What proposed a link — name matching, or embedding similarity. Recorded on the link so that each one's separate contribution to recall can be measured.
_Avoid_: source, method, strategy, provenance

**Index**:
The complete set of chunks, sections, and links for a repository at a point in time.
_Avoid_: graph, store, database, embedding store

**Drift**:
The state of a section describing its linked chunk inaccurately, because the chunk changed and the section did not. Inaccuracy, not incompleteness: a section that never mentioned the thing that changed has not drifted, and a section whose claim the change has falsified has. The corpus measures that boundary from both sides.
_Avoid_: staleness, rot, decay, divergence

**Suspect**:
A section linked to a changed chunk, before anything has confirmed whether it actually drifted.
_Avoid_: candidate, match, hit

**Verification**:
The model pass that decides whether a suspect actually drifted. Every suspect is verified before it is reported, so a section that is never mentioned was never a suspect rather than one that passed.
_Avoid_: check, validation, review

**Finding**:
A suspect confirmed to have drifted, together with the explanation of what is now wrong.
_Avoid_: issue, error, problem, violation

**Repair**:
A rewritten section that resolves a finding while preserving the parts that were already accurate.
_Avoid_: fix, correction, patch, update

**Confidence**:
How certain the system is that a repair is safe to propose without a human reading the original first. Decides whether a finding becomes a repair or a flag.
_Avoid_: score, certainty, probability

**Flag**:
A finding surfaced for human review instead of repaired, because confidence was too low.
_Avoid_: warning, alert, notice

**Corpus**:
The fixture repository every published accuracy number is measured against: a small library, its documentation, and the planted cases and decoys committed against it.
_Avoid_: dataset, benchmark, suite, sample

**Case**:
One planted change to the corpus, together with the section and the chunk it is expected to invalidate. Ground truth, recorded independently of what synclint can currently detect.
_Avoid_: example, scenario, instance

**Reach**:
Whether a case's expected section and chunk meet as a suspect at all. The ceiling on what a run could possibly find before the model is asked anything, and the half of recall that costs nothing to measure. A decoy has no expected pair, so its reach is whether it raises a suspect at all — the ceiling on the false positives it could cause.
_Avoid_: coverage, detectable, hit

**Decoy**:
A fixture change deliberately designed to produce no finding, used to measure the false positive rate.
_Avoid_: negative case, control, noise

**Spend ceiling**:
The dollars a single run may spend before it refuses to make another model call. Enforced before each call rather than after, so a run stops short of the ceiling rather than past it.
_Avoid_: budget, limit, cap, quota

**Recorded answer**:
One model response saved to disk under a hash of the question that produced it, so that asking again replays it instead of paying for it. The corpus's recorded answers are committed, because a published accuracy figure nobody else can recompute is a claim rather than a measurement.
_Avoid_: cached response, fixture, snapshot

**Score**:
Every finding a run over the corpus produced, matched against the ground truth: true positives, false positives, false negatives, and the precision and recall computed from them. Distinct from reach, which is what the corpus can say for free; a score costs a model pass, and after the first one it costs nothing again.
_Avoid_: results, metrics, evaluation, benchmark
