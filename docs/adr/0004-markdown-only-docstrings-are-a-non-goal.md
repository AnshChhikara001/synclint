# Documentation means markdown files; docstrings are a non-goal

synclint checks prose documentation — the README and markdown under a configurable glob. It deliberately does not check docstrings or inline comments.

A docstring lives inside the chunk being diffed, so a change to a function is also a change to its docstring. Detecting drift between a thing and a part of itself requires a different model of what changed, and mixing the two would blur the product: synclint is about documentation that lives apart from the code and therefore drifts silently.

This is a scope boundary, not a limitation we intend to remove.
