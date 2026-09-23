# synclint

A GitHub Action that detects when a code change has made documentation inaccurate,
repairs what it can, and flags the rest for human review.

Documentation goes wrong silently. A parameter is renamed, a default changes, a
capability is removed, and a paragraph somewhere in the repository is now a lie.
Nothing fails and no test goes red. synclint runs on the pull request, while the
author still has the context that makes fixing it a two-minute job.

## How it works

Three operations with no hidden coupling. `build_index` walks a repository and
records its chunks of code, its sections of prose, and the links between them.
`analyse` takes that index and two git revisions, finds the sections linked to
chunks the change touched, and asks a model whether each is still accurate.
`publish` writes the result to GitHub. Neither of the first two needs network
access to GitHub, so every judgement the tool makes is reachable offline.

## What works today

- **`build_index`** — chunks from `ast` (signature, docstring, decorators, never
  bodies), sections from markdown split at every heading, links proposed by name
  matching and, optionally, by embedding similarity above a configurable
  threshold — one numpy matrix product, no vector database (ADR-0002). Each link
  records which mechanism proposed it. Renders to JSON and reads back.
- **`analyse`** — the modified files at two revisions, reduced to the chunks that
  actually changed, reduced again to the sections linked to them, each put to the
  model. Comments, formatting and docstring edits do not survive a parse and so
  cannot produce a finding. Sections checked and found accurate are reported too,
  so silence about a section means it was never in question.
- **Spend control** — every model response cached on disk by prompt, a ledger of
  tokens and dollars, and a ceiling checked before each call rather than after.
- **Fixture corpus** — `corpus/` holds a small library with documentation,
  seventeen deliberately planted drift cases and thirteen decoys that must
  produce nothing, with ground truth for each. See its own README.
- **Accuracy harness** — `python -m synclint score corpus` runs every case and
  every decoy through `analyse` and matches what came back against the ground
  truth. It replays recorded answers and cannot make a call, so the numbers
  below cost nothing to reproduce and do not move between runs.

Not yet built: the repair and validation passes, rules-gated confidence, the
`publish` step, and the Action itself.

## Measured so far

Against the fixture corpus, with gpt-5.4-mini at low reasoning effort. Recording
the 31 answers cost $0.0217; replaying them costs nothing and gives the same
figures every time:

    python -m synclint score corpus

| Drift kind | Planted | Found | Recall |
| --- | --- | --- | --- |
| renamed-parameter | 5 | 1 | 20% |
| changed-default | 5 | 3 | 60% |
| removed-capability | 5 | 5 | 100% |
| contradicted-claim | 2 | 1 | 50% |
| All | 17 | 10 | 59% |

**Precision 100%** (10 true positives, 0 false positives). **Recall 59%.** Of
the thirteen decoys, six raise no suspect and cannot produce a finding at all;
the seven that do reach the model were all cleared by it.

Precision is perfect because the model is conservative, and that conservatism is
where the missing recall goes. Two of the seven misses never reach the model,
and the other five are suspects it saw and cleared:

- **Four of the five `renamed-parameter` cases.** `find(query)` documented,
  `find(text)` shipped, and the model judged that a reader following the page is
  not misled. Anyone calling it by keyword gets a `TypeError`. This is the one
  that should be fixed and the number that will say whether it was.
- **One `changed-default` case**, where `read_csv` stopped skipping invalid rows
  and started raising on them.
- **Two never reach the model at all** — a default that lives in `__init__`
  while the prose names the class, and a wholly new method. Both are recorded
  in the manifest as the gaps in the tool that they are.

The first run of this harness scored 50%, not 59%, and the difference is not an
improvement to the tool: it is the corpus admitting it was wrong. Five cases
asserted that code gaining an undocumented parameter is drift, which the
verification prompt denies in as many words and the glossary settles against —
drift is a section that is *inaccurate*, and a page that never mentioned the new
parameter is merely silent. Three of those five are decoys now, and the two
whose pages make a claim the change falsifies stayed. `corpus/README.md` has
the argument in full. Nothing about the tool's behaviour changed, and no answer
was re-asked: the same recorded run is simply scored against ground truth that
is no longer arguing with itself.

### What embedding links buy

The same harness measures link recall — how many planted section-chunk pairs
the index links at all — by name matching alone and with embeddings
(text-embedding-3-small) added. The vectors are committed too, so this also
replays for free:

| Links | Pairs linked | Planted pairs linked | Link recall | Suspects on cases | Suspects on decoys |
| --- | --- | --- | --- | --- | --- |
| name | 48 | 15 of 17 | 88% | 20 | 11 |
| name + embedding ≥ 0.55 | 84 | 16 of 17 | 94% | 36 | 15 |

**The delta is one case**, and it costs 36 more links and 20 more questions to
the model per corpus run, four of them on decoys. The pair gained is
`shelf-capacity-default`: the prose describes a constructor default and names
the class, and the embedding puts it next to `Shelf.__init__` (0.597) where
name matching cannot. The other miss, a newly added method, has no chunk at the base
for anything to link to.

The threshold sweep says the trade is lumpy rather than smooth — at 0.6 and above
the embeddings add links but not that case, and below 0.55 they add only suspects:

| Threshold | Pairs linked | Link recall | Suspects on cases | Suspects on decoys |
| --- | --- | --- | --- | --- |
| 0.65 | 55 | 88% | 22 | 11 |
| 0.60 | 61 | 88% | 24 | 12 |
| 0.55 | 84 | 94% | 36 | 15 |
| 0.50 | 124 | 94% | 48 | 23 |
| 0.45 | 164 | 94% | 60 | 26 |

0.55 is the 95th percentile of similarity over every section-chunk pair in the
corpus, chosen from that distribution rather than from the cases. Embedding the
corpus's 75 sections and chunks cost 2,400 tokens, **$0.000048**. On a corpus
whose pages name what they document, name matching was already doing nearly all
the work; embeddings are a narrow fix for one shape of miss, not a general
improvement.

Findings are still scored over name links. The extra suspects have no recorded
answers yet, so whether the model then finds the shelf case is unmeasured.

106 tests, mypy strict, no API spend in the suite — every test replays a recorded
answer or injects a fake.

## Limitations

- **Two in five planted cases still go unfound.** Nothing here is tuned: the
  figures above are the first measurement, taken at low reasoning effort on the
  cheapest model, and a renamed parameter — the easiest drift there is — is
  found one time in five. Whether the effort or the prompt is to blame is a
  question the harness can now answer for two cents.
- **Name matching is permissive.** It costs nothing on the corpus, whose pages
  are short and name what they document, but pointed at this repository every
  link it proposes is wrong — `id`, `write`, `spend` and `git` all match as
  ordinary English words in prose. The 100% above is precision over findings on
  a well-behaved corpus, and it is not a claim about links in general.
- **Embedding links are costly for what they add.** One planted pair gained for
  20 more suspects per corpus run, on one small corpus with one embedding model.
  The threshold was set on this corpus and may not transfer.
- **A default declared in a constructor is unreachable by name.** The default
  lives in `__init__` while the prose names the class. Embedding links reach the
  corpus's one example; whether they reach it in general is unmeasured.
- **A chunk the change adds is invisible.** Chunk comparison reports only chunks
  present on both sides of a diff, so a newly added function the documentation
  never mentions goes unreported.
- **A pull request from a fork cannot be written to**, so repairs degrade to a
  comment. Deliberate, and stated in the comment rather than failing quietly.

`CONTEXT.md` is the glossary; `docs/adr/` holds the decisions and what was
rejected.
