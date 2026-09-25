# The documented trigger is pull_request, and forks go unreviewed

A pull request opened from a fork gets a `pull_request` run with a read-only token and none of the repository's secrets. synclint needs a secret — the model provider's key — so on a fork it cannot ask the model anything, let alone comment.

`pull_request_target` would get around that: it runs in the base repository's context, with its secrets and a write token, whoever opened the pull request. It is the usual answer, and it is safe only for a workflow that never executes the code it checks out. synclint arguably qualifies — it parses Python with `ast` and never imports or runs it — but that argument has to hold for every step of every workflow a user writes around the Action, not just for synclint, and a user who adds `pip install -e .` before it has handed a stranger's `setup.py` the repository's API key and write access. The documented workflow would be one edit from that.

We document `pull_request`. On a fork, the Action finds no key, prints a warning and exits cleanly, so outside contributors do not see a red check for something they cannot fix. The same missing key on the repository's own branch fails the run, because there it is a misconfiguration.

The cost is that the pull requests most likely to change documentation without knowing it — from people outside the project — are exactly the ones synclint does not review. A maintainer who accepts the risk can still run it under `pull_request_target`; `publish` already degrades a fork's repairs to diffs in the comment, since it cannot push to the fork's branch anyway.
