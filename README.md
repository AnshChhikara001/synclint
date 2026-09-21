# synclint

A GitHub Action that detects when a code change has made documentation inaccurate,
repairs what it can, and flags the rest for human review.

Documentation goes wrong silently. A parameter is renamed, a default changes, a
capability is removed, and a paragraph somewhere in the repository is now a lie.
Nothing fails and no test goes red. synclint runs on the pull request, while the
author still has the context that makes fixing it a two-minute job.

Under construction. The accuracy numbers this README will eventually carry do not
exist yet; see `CONTEXT.md` for the vocabulary and `docs/adr/` for the decisions
taken so far.

They will come from `corpus/`: a small library with documentation, carrying twenty
deliberately planted drift cases and the ground truth for each one. It has a README
of its own covering how it is built, what is in it, and what synclint cannot yet
find in it.
