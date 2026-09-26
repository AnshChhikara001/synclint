# Measured results

Every figure here replays from committed answers, costs nothing to reproduce,
and is the same on every run. Tests pin the corpus and link-recall figures, so
those cannot drift from the harness that produced them; the humanize replay
compares itself with what the paying run printed. The model is gpt-5.4-mini at low reasoning
effort, and nothing was tuned against these numbers.

## On the fixture corpus

`corpus/` is a small library, eleven modules and nine markdown pages, with
eighteen planted drift cases and fourteen decoys — changes that break nothing
and must produce nothing — each with ground truth. Recording its 31
verification answers cost $0.0217.

    python -m synclint score corpus

| Drift kind | Planted | Found | Recall |
| --- | --- | --- | --- |
| renamed-parameter | 5 | 1 | 20% |
| changed-default | 5 | 3 | 60% |
| removed-capability | 5 | 5 | 100% |
| contradicted-claim | 2 | 1 | 50% |
| deleted-chunk | 1 | 1 | 100% |
| All | 18 | 11 | 61% |

**Precision 100%** (11 true positives, 0 false positives). **Recall 61%** (11
true positives, 7 false negatives). **False positive rate 0%** (0 of 14 decoys
reported drift).

| Decoy kind | Decoys | Reach the model | False positives |
| --- | --- | --- | --- |
| internal-refactor | 4 | 4 | 0 |
| added-parameter | 3 | 3 | 0 |
| comment-edit | 2 | 0 | 0 |
| test-only | 2 | 0 | 0 |
| formatting | 2 | 0 | 0 |
| moved-chunk | 1 | 0 | 0 |
| All | 14 | 7 | 0 |

Half of that 0% is the design, not the model: seven decoys raise no suspect and
cannot produce a finding whatever it says. The other seven reach the model and
it cleared all seven, which is a small denominator.

The model errs toward clearing: nothing it reported was wrong, and five of the
seven misses are suspects it saw and cleared:

- **Four of the five renamed parameters.** `find(query)` documented,
  `find(text)` shipped, and the model judged that a reader following the page is
  not misled. Anyone calling it by keyword gets a `TypeError`. This is the
  first thing to fix, and the harness will say whether it was fixed for two
  cents.
- **One changed default**, where `read_csv` stopped skipping invalid rows and
  started raising on them.

The other two never reach the model: a default that lives in `__init__` while
the prose names the class, and a wholly new method.

The first scoring read 50%, and the difference is not an improvement to the
tool. Five cases asserted that code gaining an undocumented parameter is drift;
the verification prompt denies it and the glossary settles against it — drift
is a section that is *inaccurate*, and a page that never mentioned the new
parameter is merely silent. Three became decoys; the two whose pages make a
claim the change falsifies stayed. No answer was re-asked: the same recorded
run was scored against ground truth that no longer argued with itself.
[`corpus/README.md`](../corpus/README.md) has the argument in full.

## What embedding links buy

The same harness measures link recall — how many planted section-chunk pairs
the index links at all — by name alone and with text-embedding-3-small added.
The vectors are committed, so this replays for free too.

| Links | Pairs linked | Planted pairs linked | Link recall | Suspects on cases | Suspects on decoys |
| --- | --- | --- | --- | --- | --- |
| name | 48 | 16 of 18 | 89% | 20 | 11 |
| name + embedding ≥ 0.55 | 84 | 17 of 18 | 94% | 36 | 15 |

**The delta is one case**, and it costs 36 more links and 20 more questions to
the model per corpus run, four of them on decoys. The pair gained is
`shelf-capacity-default`: the prose describes a constructor default and names
the class, and the embedding puts it next to `Shelf.__init__` at 0.597, where
name matching cannot. The other miss, a new method, has no chunk at the base for
anything to link to.

| Threshold | Pairs linked | Link recall | Suspects on cases | Suspects on decoys |
| --- | --- | --- | --- | --- |
| 0.65 | 55 | 89% | 22 | 11 |
| 0.60 | 61 | 89% | 24 | 12 |
| 0.55 | 84 | 94% | 36 | 15 |
| 0.50 | 124 | 94% | 48 | 23 |
| 0.45 | 164 | 94% | 60 | 26 |

The trade is lumpy, not smooth: at 0.60 and above the embeddings add links but
not that case, and below 0.55 only suspects. 0.55 is the 95th percentile of
similarity over every section-chunk pair in the corpus, chosen from that
distribution rather than from the cases. Embedding the corpus cost 2,400
tokens, $0.000048. On pages that name what they document, name matching was
already doing nearly all the work. Findings are still scored over name links:
the extra suspects have no recorded answers, so whether the model then finds
the shelf case is unmeasured.

## What repairs look like

The manifest carries ground truth for every repair — text a correct one must
say, and text it must no longer say — so validation, itself a model's
judgement, is scored against something that is not. Recording the 17 repair and
validation answers cost $0.0139, and the three confidence answers $0.0022.

| Repaired case | Shape | Outcome | Confidence | Ground truth | Text kept |
| --- | --- | --- | --- | --- | --- |
| write-json-path-renamed | renamed-parameter | proposed | 97% | correct | 98% |
| renew-days-default | changed-default | not applied | — | not applied | — |
| write-csv-columns-default | changed-default | proposed | 98% | correct | 94% |
| matches-case-sensitive-default | changed-default | proposed | 98% | correct | 81% |
| remove-returns-nothing | — | not applied | — | not applied | — |
| load-drops-create-missing | — | outside the gate | — | correct | 54% |
| parse-query-drops-or | — | not applied | — | not applied | — |
| overdue-drops-grace | — | outside the gate | — | correct | 49% |
| titles-drops-sort | — | outside the gate | — | correct | 55% |
| is-valid-gains-isbn10 | — | outside the gate | — | incorrect | 83% |

**Validation passed all seven it saw, one of them wrong. The gate stopped that
one.** The `is_valid` repair admits ten-digit ISBNs but keeps the advice to
convert them first, which the change made pointless. Its change rewrites a
function body, not a shape the gate admits, so it is flagged. The price is three
correct repairs flagged with it, because a removed capability is never one
narrow shape. **Three proposed, three correct**, where validation alone would
have proposed seven with one wrong.

| Shape | Repairs | Correct | Proposed at ≥ 90% | Proposed and correct |
| --- | --- | --- | --- | --- |
| renamed-parameter | 1 | 1 (100%) | 1 | 1 |
| changed-default | 3 | 2 (67%) | 2 | 2 |
| outside the gate | 6 | 3 (50%) | 0 | 0 |

The rules did all of the work and the threshold none: the model gave the three
repairs inside the gate 97–98%, so any threshold up to 0.97 proposes the same
three. The gate's shapes agree with the manifest's hand-written kinds on all
sixteen cases that change a chunk, though the rules were written from the ADR,
not from the corpus.

Three repairs never reached validation because their quotes did not match the
section — two quoted the whole section plus a newline it does not end with, one
garbled the quote. Text kept is low partly because nine of the ten repairs
quoted the whole section and rewrote it. Telling the repair pass to quote only
the wrong words, and refusing a whole-section quote in code, was tried: seven
of ten still quoted everything, the guard refused all seven, and one repair in
ten came out proposed and correct. That cost $0.0114 and is reverted; commit
`68c98d0` keeps its answers so the numbers can be recomputed.

## On a real repository

The corpus was written by the same hand as the tool, so the same test was run
on code nobody here wrote: [humanize](https://github.com/python-humanize/humanize)
at `392aef7`, its README split into eighteen sections with sixteen name links.
Seven changes break a README example and four are decoys that break nothing.
Each is a patch in `validation/humanize/changes/`, analysed through the command
line the Action calls; `validation/humanize/run.sh` replays it for nothing.

| Change | Should | Did | Repair |
| --- | --- | --- | --- |
| `naturalsize(binary=)` renamed `iec=` | find | found | proposed, 96%, correct |
| `scientific` precision default 2 → 3 | find | found | proposed, 99%, correct |
| `precisedelta` format default `%0.2f` → `%0.1f` | find | found | proposed, 98%, correct |
| `fractional(1.5)` gives `3/2`, not `1 1/2` | find | found | flagged (body), draft correct |
| `naturaldate` gives ISO dates past five months | find | found | flagged (body), draft correct |
| `apnumber` deleted | find | found, a disappearance | flagged (gone) |
| `naturalday` renamed `natural_day` | find | found, a disappearance | flagged (gone) |
| `intcomma` body rewritten, same output | nothing | nothing, cleared | — |
| `intword(format=)` renamed, README never passes it | nothing | nothing, cleared | — |
| `naturalsize` gains `separator=" "` | nothing | nothing, cleared | — |
| a comment in `scientific` reworded | nothing | nothing, never asked | — |

**Seven of seven found, no decoy reported, three repairs proposed and all three
right**, checked by hand against the patched code. Twenty-two model calls cost
$0.0293.

The first run found five, and both misses were the same gap. A deleted function
had no chunk after the change, so nothing was compared. The rename was worse:
`naturaldate`, which calls `naturalday`, changed two lines, so the section that
calls `naturalday` three times was verified against `naturaldate` and rightly
judged accurate *about `naturaldate`* — one section verified, none drifted,
true and misleading. Following deleted chunks turned both into disappearances,
found on replay without a model call, and nothing else in the eleven moved.

Two things went better than on the corpus. Every repair quoted only the lines
it changed; these pages are doctest examples, one claim per line, which likely
explains it more than anything synclint did. And name matching proposed no
wrong link, because humanize's prose names functions in full, as
`humanize.naturalsize(...)`. This ran from the command line, not as an
installed Action; the Action's path from event to `analyse` is exercised by the
live run in the README.

The suite is 228 tests under mypy strict, with no API spend: every test replays
a recorded answer or injects a fake.
