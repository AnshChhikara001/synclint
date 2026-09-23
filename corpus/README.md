# The fixture corpus

Every accuracy number synclint publishes comes from here. The corpus is a small
Python library — `bookshelf`, eleven modules, nine markdown pages and a couple
of test modules — with seventeen deliberately planted drift cases against it and
thirteen decoys that must produce nothing at all.

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

Four kinds, five of each of the first three:

| Kind | How many | What it is |
|---|---|---|
| `renamed-parameter` | 5 | a documented parameter is called something else now |
| `changed-default` | 5 | a documented default value is different |
| `removed-capability` | 5 | something the documentation promises is gone |
| `contradicted-claim` | 2 | the page states something the code now falsifies |

Every case changes code only. A planted case that edited a markdown file would
have repaired the drift it was meant to plant, so the audit refuses one.

`contradicted-claim` is the odd one at two, and the reason is below.

## The decoys

Ten changes that must produce no finding. Without them there is no false
positive rate, and a precision figure computed without negative cases is
meaningless. Each one is a change a reviewer would recognise as real work.

| Kind | How many | What it is |
|---|---|---|
| `internal-refactor` | 4 | the body rewritten, behaviour and signature untouched |
| `added-parameter` | 3 | a documented function gains an optional parameter the page never mentions |
| `comment-edit` | 2 | comments and docstrings, no code |
| `formatting` | 2 | wrapped to a shorter line length |
| `test-only` | 2 | a test module added, or an existing one extended |

Seven reach the model. The other six cannot produce a finding whatever a model
would have said about them: comments and layout do not survive a parse,
docstrings are stripped before the comparison (ADR-0004), and test files are
dropped whole. Six decoys measure the design, then, and seven measure the
judgement — and the seven are where a precision figure is actually earned.

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

## Scoring it

    python -m synclint score corpus

Runs every case and every decoy branch through `analyse` and matches what came
back against the manifest. The finding a case planted is a true positive; a case
that produced nothing is a false negative; every other finding is a false
positive, whether it landed on a decoy branch, where nothing at all should be
reported, or beside the planted one on a case branch. Precision and recall
follow, broken down by drift kind, as markdown that goes straight into the
README.

    answers/          one recorded model answer per question the corpus asks

Scoring replays those and cannot do anything else. The client it runs on has no
provider behind it and a zero ceiling against a zero price, so a question with
no recorded answer comes back as a gap rather than as a call — and a run missing
any answer prints the gaps and no numbers at all. Numbers computed over whichever
branches happened to have an answer on disk would be a different measurement
every time and would not say so on the page.

A case branch is held to the one finding the manifest planted, so a second
finding on it counts against precision even if a reader would call it fair. That
is stricter than measuring false positives on the decoys alone, and deliberately
so: the manifest says what each case should invalidate, and a tool that reports
three sections to get one right is not precise. It can only push the number
down, never up.

    embeddings/       one recorded vector per section and chunk the base indexes

The same run measures link recall: how many planted section-chunk pairs the
index built at the base links at all, by name matching alone and with embedding
similarity added, beside how many suspects each set of links raises across the
case and the decoy branches. A link that reaches a case is worth something; one
that only raises suspects costs a model call and, on a decoy, a chance of a
false positive. The table shows both so a recall gain cannot hide its price.
`--threshold` sets the similarity embedding links need.

Recording is the one pass that costs money, and it is the flag that asks for it:

    OPENAI_API_KEY=... python -m synclint score corpus --record

Recording also embeds whatever texts have no recorded vector. Answers are keyed
on the question, the schema and the model name, so a figure recorded against one
model cannot be republished as another's, and re-recording after a change to the
prompt or the fixture asks only for what actually moved.
The directory is committed: an accuracy figure nobody else can recompute is a
claim rather than a measurement.

## What drift is not

Three of the `added-parameter` decoys were planted as drift cases. They were
wrong, and the accuracy harness is what proved it.

`undocumented-feature` used to be a kind: five cases asserting that code gaining
something the documentation never mentions is drift. The verification prompt
asserts the opposite in as many words — *a section that never mentioned the
thing that changed has not drifted* — and the glossary is on the prompt's side:
drift is a section describing its chunk **inaccurately**. A page that never
mentioned `encoding` is not inaccurate about `encoding`. It is silent, and
silence is incompleteness. synclint reports what is wrong, not what is missing.

The first scored run put the disagreement on the page: four of the five cases
were cleared by the model, exactly as its instructions told it to. So the kind
split on the evidence rather than the label:

- `find-gains-author-filter`, `read-csv-gains-encoding` and
  `save-gains-compression` are omissions. Their pages say nothing about the new
  parameter and nothing they do say became false. They are `added-parameter`
  decoys now, and reporting one is a false positive.
- `is-valid-gains-isbn10` and `catalogue-gains-merge` are not omissions. One
  page says a ten-digit ISBN "is always rejected"; the other says "everything
  the catalogue can do is on this page". Both claims are false once the change
  lands. They stayed as cases under `contradicted-claim`.

The three that moved kept their overlays and their commits byte for byte, so
every recorded answer still replays. Nothing was re-asked, and nothing about the
measurement moved except what it was being compared against — which is the
point: the ground truth changed because it was wrong, and the tool's behaviour
did not change at all.

`contradicted-claim` holds two cases where the others hold five. Planting three
more needs a recording pass, so it is short and says so rather than being
padded with cases the corpus does not have.

## What the manifest is not

The manifest is ground truth, written by hand, and deliberately independent of
what synclint can detect today. Two of the seventeen are out of reach as things
stand:

- `shelf-capacity-default` — the default lives in `Shelf.__init__`, but the
  prose documenting it names the class. Name matching links the section to
  `Shelf`, and `Shelf` itself did not change. Embedding links reach it at the
  default threshold, but findings are still scored over name links alone.
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
