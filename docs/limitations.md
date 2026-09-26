# Limitations

Every known limitation, most important first. The README carries the short list.

- **Python only.** Chunks come from the standard library's `ast` (ADR-0001); a
  change in any other language raises nothing.
- **Docstrings are not checked** (ADR-0004). Documentation means markdown under
  the configured globs. A docstring is part of the chunk it documents, and
  drift between a thing and a part of itself is a different problem. This is a
  scope boundary, not a gap to close.
- **Pull requests from forks and from Dependabot go unreviewed.** GitHub gives
  their `pull_request` runs no secrets, so there is no key; the Action warns and
  exits cleanly rather than failing their checks. `pull_request_target` would
  reach them, with repairs degraded to diffs in the comment, and is not the
  documented trigger because the workflow around the
  Action would then be one careless step from running a stranger's code with
  the repository's key (ADR-0006).
- **Two in five planted cases go unfound**, and a renamed parameter, the easiest
  drift there is, is found one time in five. Whether reasoning effort or the
  prompt is to blame is untested.
- **Name matching is permissive.** It costs nothing on the corpus and humanize,
  whose pages name what they document, but pointed at this repository every
  link it proposes is wrong — `id`, `write`, `spend` and `git` all match as
  ordinary English. The 100% is precision over findings on well-behaved pages,
  not a claim about links in general.
- **Embedding links are costly for what they add**: one planted pair for 20
  more suspects per run, on one small corpus with one embedding model. The
  threshold was set on this corpus and may not transfer.
- **A default declared in a constructor is unreachable by name.** Embedding
  links reach the corpus's one example; whether they reach it in general is
  unmeasured. A chunk the change adds is invisible too: only code that existed before the change is
  compared, so a page claiming to list everything is not caught falling behind.
- **A disappearance is only as good as the name link behind it.** It is never
  put to a model, so a deleted method called `write` would flag every section
  using the word. A move is followed only when it is unambiguous and keeps the
  qualified name: a function renamed, or moved into a class, is reported gone.
- **Validation grades the same model's work.** It refused none of the seven
  repairs it saw, one of them wrong. The gate caught that one, but cannot catch
  a wrong repair of an eligible shape, and the ground truth that judges repairs
  is hand-written for seventeen sections.
- **The calibration table is four repairs deep inside the gate**, and the
  model's confidence did not vary enough to test the threshold at all.
- **The gate is narrow on purpose.** Every removed capability is flagged even
  when the repair is right, and a flagged finding still pays for a repair and
  its validation so the reviewer has a draft.
- **A repair rewrites the whole section.** At low reasoning effort the model
  will not quote small even when told to, so byte-identity outside the edits
  holds only in name; text kept measures the damage and nothing bounds it.
  Quotes must also match exactly, and three of ten repairs were lost to that.
- **Sections are read at the base.** A pull request that changes a function and
  fixes its documentation together has the section checked as it read before
  the fix, and can be told to fix what it already fixed.
- **The committed index goes out of date more often than it needs to.** Any
  Python or documentation file that differs since it was built counts, whether
  or not the difference touches a chunk or a section, so the fallback runs more
  often than it has to, and the fallback links by name only. The
  rebuild workflow assumes the default globs, pushes to main (a protection rule
  that refuses its token leaves the index behind), and loses a race with a push
  that lands while it runs. An index does not record which synclint built it.
  The fallback reads the base through `git archive`, so `export-ignore` files
  and submodules are missing from it.
- **The repair branch is rebuilt on every push**, so a human's amendment to
  `synclint/repairs-N` is overwritten, and a repair pull request stays open
  after a push that leaves nothing to repair. It also triggers the workflow
  itself, which GitHub holds for approval.
- **The image is built on every run**, not pulled from a registry: 95MB
  compressed, about 12 seconds cold on a laptop.
