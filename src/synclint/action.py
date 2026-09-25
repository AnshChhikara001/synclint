"""The GitHub Action's entry point: the event GitHub ran it for, turned into an `analyse` command line.

Everything the Action does past this point is `analyse --pull-request`, so
that the Action and the command line cannot come to disagree. What is here is
only what the command line cannot know: which pull request, which revisions,
and what to do when the run has no key.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from synclint.__main__ import main as analyse_main
from synclint.git import run


@dataclass(frozen=True)
class Inputs:
    """The Action's inputs as action.yml passes them: strings, empty when not set."""

    model: str = ""
    documentation_glob: str = ""
    confidence_threshold: str = ""
    ceiling: str = ""


def command(event: Mapping[str, Any], root: Path, inputs: Inputs) -> list[str]:
    """The `analyse` command line for the pull request `event` describes.

    The base is where the branch forked, not the base branch's tip: the event
    names the tip, and diffing against it would report every change merged to
    the base since as though the pull request had reverted it.

    An empty input is left off, so the command line's own default applies. A
    documentation glob input holds one glob per line.

    Recorded answers go to the temporary directory rather than the default,
    which is relative and would land in the checkout: a container is thrown
    away after one run, so there is nothing to keep them for.
    """
    pull = event["pull_request"]
    head = pull["head"]["sha"]
    try:
        base = run(root, "merge-base", pull["base"]["sha"], head).strip()
    except subprocess.CalledProcessError:
        raise SystemExit(
            f"{root} does not hold both sides of #{pull['number']}. Check it out "
            "with full history: actions/checkout with fetch-depth: 0"
        ) from None
    argv = [
        "analyse",
        str(root),
        *("--base", base),
        *("--head", head),
        *("--pull-request", str(pull["number"])),
        *("--cache", str(Path(tempfile.gettempdir()) / "synclint")),
    ]
    for field in fields(inputs):
        flag = "--" + field.name.replace("_", "-")
        for value in getattr(inputs, field.name).split("\n"):
            if value.strip():
                argv += [flag, value.strip()]
    return argv


def main(argv: Sequence[str] | None = None) -> None:
    """Run synclint on the pull request GitHub started this container for.

    The API key arrives as OPENAI_API_KEY, set by action.yml from the
    `api-key` input, and is read from there by the provider's SDK. It is never
    an argument, because the runner prints a container's arguments in the log.
    """
    parser = argparse.ArgumentParser(prog="synclint-action")
    for field in fields(Inputs):
        parser.add_argument("--" + field.name.replace("_", "-"), default="")
    inputs = Inputs(**vars(parser.parse_args(argv)))

    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    if "pull_request" not in event:
        raise SystemExit(
            "synclint reviews pull requests; run it on the pull_request event"
        )
    if not os.environ.get("OPENAI_API_KEY"):
        pull = event["pull_request"]
        # GitHub withholds Actions secrets from a pull request opened from a
        # fork, and from one Dependabot opened, so a missing key there is
        # expected rather than a misconfiguration, and failing those checks
        # for it would be noise.
        head_repository = (pull["head"]["repo"] or {}).get("full_name")
        from_fork = head_repository != pull["base"]["repo"]["full_name"]
        if from_fork or pull["user"]["login"] == "dependabot[bot]":
            print(
                "::warning::synclint skipped: pull requests from forks and from "
                "Dependabot are not given Actions secrets, so there is no api-key"
            )
            return
        raise SystemExit(
            "no api-key: pass one to the Action from a repository secret, "
            "api-key: ${{ secrets.OPENAI_API_KEY }}"
        )
    analyse_main(command(event, Path(os.environ["GITHUB_WORKSPACE"]), inputs))


if __name__ == "__main__":
    main()
