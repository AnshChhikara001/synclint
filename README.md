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

Not yet built: the repair and validation passes, rules-gated confidence, the
`publish` step, and the Action itself.

## Measured so far

70 tests, mypy strict, no API spend — every test replays a recorded answer or
injects a fake. Of the twenty planted cases, **18 reach the model**: their
expected section and chunk meet as a suspect, which is the ceiling on what a run
could find before the model is asked anything.

Of the ten decoys, **6 cannot produce a finding at all** — comments, formatting
and test-only changes do not survive a parse — and the remaining four internal
refactors reach the model, where only its judgement stands between them and a
false positive.

There are no accuracy numbers yet. Precision and recall need the harness that
scores the corpus, and this README will carry those figures rather than an
estimate of them.

## Limitations

- **Name matching is permissive and the false positive rate is bad.** Pointed at
  this repository, every link it proposes is wrong — `id`, `write`, `spend` and
  `git` all match as ordinary English words in prose. Embedding links are meant to
  help; how much is a number this project owes, not a claim it makes.
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
