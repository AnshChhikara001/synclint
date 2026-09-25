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
- **Repair and validation** — each finding is rewritten into its section, then
  a second model pass gates the rewrite. The model answers with quoted spans and
  their replacements, and synclint applies them itself, so prose outside the
  quotes is byte-identical by construction. In practice the model quotes the
  whole section nine times in ten, so that guarantee covers little, and how much
  a repair left alone is measured instead (below). A quote the section does not
  contain exactly once, or a rewrite validation refuses, becomes a flag with the
  reason. Repairs print as a diff against the section.
- **Rules-gated confidence** (ADR-0003) — a repair is proposed only if its change
  has one of two shapes, read off the syntax trees: one parameter renamed
  everywhere it is used, or one default value changed, with nothing else
  touched. Anything else is flagged however confident the model is. Inside the
  gate the model is asked how likely the repair is exactly right, and must reach
  `--confidence-threshold` (default 0.9, set before any answer was recorded).
- **Spend control** — every model response cached on disk by prompt, a ledger of
  tokens and dollars, and a ceiling checked before each call rather than after.
- **Fixture corpus** — `corpus/` holds a small library with documentation,
  seventeen deliberately planted drift cases and thirteen decoys that must
  produce nothing, with ground truth for each. See its own README.
- **`publish`** — `analyse --pull-request N` writes the report to the pull
  request. One summary comment, found by a hidden marker and edited in place on
  every push: sections checked, how many are accurate, repaired and flagged,
  each linked to the lines it reads on, each flag with its reason and the
  rewrite that was tried. Repairs go out as one commit on
  `synclint/repairs-N`, in a pull request targeting the branch under review.
  Two repairs to one section, or a section that no longer reads as it did when
  analysed, stay in the comment instead. `publish` decides nothing itself: the
  routing is a function of the report, tested without GitHub. Stdlib `urllib`,
  no GitHub SDK.
- **Accuracy harness** — `python -m synclint score corpus` runs every case and
  every decoy through `analyse` and matches what came back against the ground
  truth. It replays recorded answers and cannot make a call, so the numbers
  below cost nothing to reproduce and do not move between runs.

- **The Action** — `action.yml` and a Dockerfile. The container's entry point
  reads the `pull_request` event, diffs from where the branch forked rather than
  from the base branch's tip, and hands the rest to `analyse --pull-request`, so
  the Action and the command line cannot disagree. The image is 95MB
  compressed, installed from the lockfile, and built fresh on each run (about
  12 seconds cold on a laptop).

Not yet: a run against a live pull request. The container has been run
end to end locally, through `analyse` to GitHub refusing a fake token; a real
comment on a real pull request is still to come.

## Using it

    name: synclint
    on: pull_request
    permissions:
      contents: write        # the branch of repairs
      pull-requests: write   # the comment, and the pull request of repairs
    jobs:
      synclint:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v5
            with:
              fetch-depth: 0   # both revisions; a shallow clone is refused
          - uses: AnshChhikara001/synclint@main
            with:
              api-key: ${{ secrets.OPENAI_API_KEY }}

The other inputs — `documentation-glob`, `model`, `confidence-threshold`,
`ceiling` — default to the command line's defaults; `action.yml` describes each.
The key reaches the container as an environment variable, never as an argument,
because the runner prints a container's arguments.

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

### What repairs look like

Every found case is repaired and validated, and the manifest carries ground
truth for each repair — text a correct one must say and text it must no longer
say — so validation, itself a model's judgement, is scored against something
that is not. Recording the 17 repair and validation answers cost **$0.0139**.

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
function body, which is not a shape the gate admits, so it is flagged. The
price: three correct repairs are flagged with it, because a removed capability
is never one narrow shape. **Three proposed, three correct**, where validation
alone proposed seven with one wrong.

| Shape | Repairs | Correct | Proposed at ≥ 90% | Proposed and correct |
| --- | --- | --- | --- | --- |
| renamed-parameter | 1 | 1 (100%) | 1 | 1 |
| changed-default | 3 | 2 (67%) | 2 | 2 |
| outside the gate | 6 | 3 (50%) | 0 | 0 |

The rules did all of the work and the threshold none. The model gave the three
repairs inside the gate 97–98%, so any threshold up to 0.97 proposes the same
three. The gate's shapes agree with the manifest's hand-written kinds on all
sixteen cases that change a chunk, though the rules were written from the ADR,
not from the corpus. Recording the three confidence answers cost **$0.0022**.

Three repairs never reached validation because their quotes did not match the
section. Two quoted the whole section plus a newline it does not end with, and
one garbled the quote itself. Text kept is lowest where a capability was
removed, where the right repair deletes a clause, but it is also low because
nine of the ten repairs quoted the whole section and rewrote it.

That last point was tested, and the idea failed. Telling the repair pass to
quote only the wrong words, and refusing a whole-section quote in code, should
have shrunk the quotes. It did not: seven of ten still quoted everything, the
guard refused all seven, and one repair in ten came out proposed and correct.
That cost $0.0114 and is reverted. Commit `68c98d0` keeps its answers, so the
numbers can be recomputed.

180 tests, mypy strict, no API spend in the suite — every test replays a recorded
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
- **Validation grades the same model's work.** It refused none of the seven
  repairs it saw, one of them wrong. The gate caught that one here, but the
  gate cannot catch a wrong repair of an eligible shape, and the ground truth
  that judges it is hand-written for seventeen sections.
- **The calibration table is four repairs deep inside the gate.** Nothing about
  whether 90% from the model means 90% right can be read from it yet, and the
  model's confidence did not vary enough to test the threshold at all.
- **The gate is narrow on purpose.** Every removed capability is flagged, even
  when the repair is right, and a flagged finding outside the gate still pays
  for a repair and its validation so the reviewer has a rewrite to start from.
- **A repair rewrites the whole section.** The model will not quote small at
  low reasoning effort, even when told to, so byte-identity outside the edits
  holds only in name. Text kept measures the damage; nothing bounds it.
- **Quotes must match exactly.** Three of ten repairs were lost to that, two to
  a trailing newline.
- **Pull requests from forks go unreviewed.** GitHub gives a fork's
  `pull_request` run no secrets, so there is no key; the Action warns and exits
  cleanly rather than failing a contributor's checks. `pull_request_target`
  would reach them, and is not the documented trigger because the workflow
  around the Action would then be one careless step from running a stranger's
  code with the repository's key (ADR-0006). Under it, repairs degrade to
  diffs in the comment, as they do for a token GitHub answers 403.
- **The image is built on every run**, not pulled from a registry: 95MB
  compressed and 436MB unpacked, of which git's layer is 92MB and numpy 68MB.
- **The repair branch is rebuilt on every push.** Repairs are recomputed
  against the new head, so a human's amendment to `synclint/repairs-N` is
  overwritten, and a repair pull request stays open after a push that leaves
  nothing to repair.

`CONTEXT.md` is the glossary; `docs/adr/` holds the decisions and what was
rejected.
