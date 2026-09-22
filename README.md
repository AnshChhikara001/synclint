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
  matching. Renders to JSON and reads back.
- **`analyse`** — the modified files at two revisions, reduced to the chunks that
  actually changed, reduced again to the sections linked to them, each put to the
  model. Comments, formatting and docstring edits do not survive a parse and so
  cannot produce a finding. Sections checked and found accurate are reported too,
  so silence about a section means it was never in question.
- **Spend control** — every model response cached on disk by prompt, a ledger of
  tokens and dollars, and a ceiling checked before each call rather than after.
- **Fixture corpus** — `corpus/` holds a small library with documentation,
  twenty deliberately planted drift cases and ten decoys that must produce
  nothing, with ground truth for each. See its own README.
- **Accuracy harness** — `python -m synclint score corpus` runs every case and
  every decoy through `analyse` and matches what came back against the ground
  truth. It replays recorded answers and cannot make a call, so the numbers
  below cost nothing to reproduce and do not move between runs.

Not yet built: the repair and validation passes, rules-gated confidence, the
`publish` step, and the Action itself.

## Measured so far

Against the fixture corpus, with gpt-5.4-mini at low reasoning effort. Recording
the 31 answers cost $0.0217; replaying them costs nothing and gives the same
figures every time.

| Drift kind | Planted | Found | Recall |
| --- | --- | --- | --- |
| renamed-parameter | 5 | 1 | 20% |
| changed-default | 5 | 3 | 60% |
| removed-capability | 5 | 5 | 100% |
| undocumented-feature | 5 | 1 | 20% |
| All | 20 | 10 | 50% |

**Precision 100%** (10 true positives, 0 false positives). **Recall 50%.** Of
the ten decoys, six raise no suspect and cannot produce a finding at all; the
four internal refactors that do reach the model were all cleared by it.

Precision is perfect because the model is conservative, and that conservatism is
where half the recall goes. Two of the ten misses never reach the model, and the
other eight are suspects it saw and cleared:

- **Four of the five `renamed-parameter` cases.** `find(query)` documented,
  `find(text)` shipped, and the model judged that a reader following the page is
  not misled. Anyone calling it by keyword gets a `TypeError`.
- **Three of the five `undocumented-feature` cases**, because the verification
  prompt tells it to clear them: *a section that never mentioned the thing that
  changed has not drifted*. The prompt and the corpus disagree about whether a
  documented function quietly gaining a parameter is drift. One of them is
  wrong, and this is the number that says so.
- **One `changed-default` case**, where `read_csv` stopped skipping invalid rows
  and started raising on them.
- **Two never reach the model at all** — a default that lives in `__init__`
  while the prose names the class, and a wholly new method. Both are recorded
  in the manifest as the gaps in the tool that they are.

88 tests, mypy strict, no API spend in the suite — every test replays a recorded
answer or injects a fake.

## Limitations

- **Recall is half, and the prompt is the reason for most of it.** Nothing here
  is tuned yet: the figures above are the first measurement, taken at low
  reasoning effort on the cheapest model. Whether either is set too mean is now
  a question the harness can answer for two cents.
- **Name matching is permissive.** It costs nothing on the corpus, whose pages
  are short and name what they document, but pointed at this repository every
  link it proposes is wrong — `id`, `write`, `spend` and `git` all match as
  ordinary English words in prose. The 100% above is precision over findings on
  a well-behaved corpus, and it is not a claim about links in general.
  Embedding links are meant to help; how much is a number this project owes.
- **A default declared in a constructor is unreachable.** The default lives in
  `__init__` while the prose names the class, so nothing links the two. Every
  constructor default in every repository has this shape.
- **A chunk the change adds is invisible.** Chunk comparison reports only chunks
  present on both sides of a diff, so a newly added function the documentation
  never mentions goes unreported.
- **A pull request from a fork cannot be written to**, so repairs degrade to a
  comment. Deliberate, and stated in the comment rather than failing quietly.

`CONTEXT.md` is the glossary; `docs/adr/` holds the decisions and what was
rejected.
