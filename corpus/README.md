# The fixture corpus

Every accuracy number synclint publishes comes from here. The corpus is a small
Python library — `bookshelf`, eleven modules and nine markdown pages — with
twenty deliberately planted drift cases against it.

## Layout

    manifest.toml     ground truth: one entry per case
    base/             the library and its documentation, before anything drifted
    cases/<id>/       the files that case rewrites, in full

Nothing here is a git repository. `build_corpus` materialises one: `base/`
becomes the `base` branch, and each case becomes a single commit on a branch of
its own, `case/<id>`, off that base. The cases fan out rather than stack, so one
index built at `base` serves all twenty and each case is a diff a run can be
pointed at directly:

    python -m synclint corpus corpus --build-to /tmp/bookshelf
    python -m synclint analyse /tmp/bookshelf --base base --head case/find-query-renamed

## The cases

Five of each of four kinds:

| Kind | What it is |
|---|---|
| `renamed-parameter` | a documented parameter is called something else now |
| `changed-default` | a documented default value is different |
| `removed-capability` | something the documentation promises is gone |
| `undocumented-feature` | the code gained something the documentation never mentions |

Every case changes code only. A planted case that edited a markdown file would
have repaired the drift it was meant to plant, so the checker refuses one.

## Checking it

    python -m synclint corpus corpus

This rebuilds the corpus, checks the manifest against it, and prints how many
cases synclint currently *reaches* — the expected section and the expected chunk
meeting as a suspect, which is the most a run can get right before the model is
asked anything. It exits non-zero only on a problem with the corpus itself.

## What the manifest is not

The manifest is ground truth, written by hand, and deliberately independent of
what synclint can detect today. Two of the twenty are out of reach as things
stand:

- `shelf-capacity-default` — the default lives in `Shelf.__init__`, but the
  prose documenting it names the class. Name matching links the section to
  `Shelf`, and `Shelf` itself did not change.
- `catalogue-gains-merge` — a wholly new method. `changed_chunks` reports chunks
  that exist on both sides of a diff, so an added one is invisible, and nothing
  links to a name the documentation has never used.

Both are real drift, correctly recorded. They are here to be measured, and the
right response is to move the number, not the manifest.

## Known wart

`build_index` walks the filesystem for `*.py`, so pointing synclint at its own
repository indexes `corpus/base` and every overlay under `corpus/cases` as
synclint source. It costs nothing in the accuracy harness, which builds the
corpus somewhere else, but it makes self-analysis noisier than it should be.
