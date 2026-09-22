# The fixture corpus

Every accuracy number synclint publishes comes from here. The corpus is a small
Python library — `bookshelf`, eleven modules, nine markdown pages and a couple
of test modules — with twenty deliberately planted drift cases against it and
ten decoys that must produce nothing at all.

## Layout

    manifest.toml     ground truth: one entry per case and per decoy
    base/             the library and its documentation, before anything drifted
    cases/<id>/       the files that case rewrites, in full
    decoys/<id>/      the files that decoy rewrites, in full

Nothing here is a git repository. `build_corpus` materialises one: `base/`
becomes the `base` branch, and each case and each decoy becomes a single commit
on a branch of its own — `case/<id>` or `decoy/<id>` — off that base. They fan
out rather than stack, so one index built at `base` serves all thirty, each is a
diff a run can be pointed at directly, and a decoy's diff holds nothing but the
decoy:

    python -m synclint corpus corpus --build-to /tmp/bookshelf
    python -m synclint analyse /tmp/bookshelf --base base --head case/find-query-renamed
    git -C /tmp/bookshelf diff base decoy/queries-reflowed

## The cases

Five of each of four kinds:

| Kind | What it is |
|---|---|
| `renamed-parameter` | a documented parameter is called something else now |
| `changed-default` | a documented default value is different |
| `removed-capability` | something the documentation promises is gone |
| `undocumented-feature` | the code gained something the documentation never mentions |

Every case changes code only. A planted case that edited a markdown file would
have repaired the drift it was meant to plant, so the audit refuses one.

## The decoys

Ten changes that must produce no finding. Without them there is no false
positive rate, and a precision figure computed without negative cases is
meaningless. Each one is a change a reviewer would recognise as real work.

| Kind | How many | What it is |
|---|---|---|
| `internal-refactor` | 4 | the body rewritten, behaviour and signature untouched |
| `comment-edit` | 2 | comments and docstrings, no code |
| `formatting` | 2 | wrapped to a shorter line length |
| `test-only` | 2 | a test module added, or an existing one extended |

Only the four refactors reach the model. The other six cannot produce a finding
whatever a model would have said about them: comments and layout do not survive
a parse, docstrings are stripped before the comparison (ADR-0004), and test
files are dropped whole. Six decoys measure the design, then, and four measure
the judgement — and the four are where a precision figure is actually earned.

A kind is a claim about the change, and the audit holds the decoy to as much of
it as it can see. An `internal-refactor` that changes no chunk is a no-op
wearing a label; a `formatting` decoy that changes one is mislabelled; a
`test-only` decoy that edits the library is neither. Any of the three would make
a rate measured over it mean nothing, so each is a fault.

What the audit cannot see is the rest of the claim. A chunk is a function or a
class, so a decoy that rewrote a module-level statement would clear every check;
and nothing here proves a refactor preserved behaviour, only that it changed
something. Those rest on the fixture's own tests, which pass on every decoy
branch — though they do not cover every module — and on reading the diff.

### What the decoys do not cover

The most realistic decoy of all is missing: a change that updates the prose
alongside the code, which is what a careful author does and the likeliest source
of a false positive in real use. One index is built, at the base, so the section
put to the model would be the stale one — the false positive would be the
harness's rather than the model's. Measuring it needs the index built at the
revision under test, which is #11's question. Until then a decoy that edits
documentation is a fault rather than a measurement.

## Auditing it

    python -m synclint corpus corpus

This rebuilds the corpus, audits the manifest against it, and prints two
measurements: how many cases synclint currently *reaches* — the expected section
and the expected chunk meeting as a suspect, which is the most a run can get
right before the model is asked anything — and how many decoys reach the model
at all, which is where a false positive is still possible. It exits non-zero
only on a fault in the corpus itself.

Measurement and fault are kept apart deliberately. A fault means the corpus is
wrong: a case that changes nothing, edits documentation, names a section or a
chunk that is not there, or names a chunk its own commit does not change; or a
decoy whose commit is not the kind of change the manifest calls it. Anything
with a fault is not measured at all, so *unreachable* means one thing only —
drift correctly recorded that synclint cannot yet get to.

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
repository indexes `corpus/base` and every overlay under `corpus/cases` and
`corpus/decoys` as synclint source. It costs nothing in the accuracy harness,
which builds the corpus somewhere else, but it makes self-analysis noisier than
it should be.
