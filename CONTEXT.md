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

**Index**:
The complete set of chunks, sections, and links for a repository at a point in time.
_Avoid_: graph, store, database, embedding store

**Drift**:
The state of a section describing its linked chunk inaccurately, because the chunk changed and the section did not.
_Avoid_: staleness, rot, decay, divergence

**Suspect**:
A section linked to a changed chunk, before anything has confirmed whether it actually drifted.
_Avoid_: candidate, match, hit

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

**Decoy**:
A fixture change deliberately designed to produce no finding, used to measure the false positive rate.
_Avoid_: negative case, control, noise
