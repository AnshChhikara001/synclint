"""The third seam: a report in, a comment and a pull request of repairs on GitHub out.

`publish` makes no decisions. Which repairs can go into the pull request is
`route`'s to say, and what the comment says is `summary`'s; both read only the
report and the files it names, so both are tested without GitHub. What is left
in `publish` is the I/O between them.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from synclint.analyse import Report, Repair, section_diff
from synclint.git import file_at, run
from synclint.github import GitHubError, Requester
from synclint.sections import split_sections

# Every run looks for a comment that starts with this and edits it, so a pull
# request pushed to ten times carries one summary, not ten. An HTML comment,
# so it renders as nothing.
MARKER = "<!-- synclint -->"

WEB = "https://github.com"


@dataclass(frozen=True)
class PullRequest:
    """The pull request under review, as far as publishing needs it."""

    number: int
    branch: str
    head: str
    from_fork: bool

    @classmethod
    def fetch(cls, github: Requester, repository: str, number: int) -> PullRequest:
        data = github("GET", f"/repos/{repository}/pulls/{number}")
        # A fork deleted after the pull request was opened leaves `repo` null.
        head_repository = (data["head"]["repo"] or {}).get("full_name")
        return cls(
            number=number,
            branch=data["head"]["ref"],
            head=data["head"]["sha"],
            from_fork=head_repository != data["base"]["repo"]["full_name"],
        )


@dataclass(frozen=True)
class Held:
    """A repair that passed every check and still cannot go into the pull request."""

    repair: Repair
    reason: str


@dataclass(frozen=True)
class Routing:
    """Which repairs go into the pull request, and which stay in the comment and why."""

    proposed: tuple[Repair, ...]
    held: tuple[Held, ...]


def route(report: Report, files: Mapping[str, str | None]) -> Routing:
    """Split the report's repairs into those the pull request can carry and the rest.

    `files` holds each documentation file the report names as it reads at the
    head of the pull request, `None` for one that is not there. A repair is
    proposed only if it is the one repair to its section and the section still
    reads there exactly once, as it did when it was analysed, so that there is
    one place for the rewrite to go. Every flag stays in the comment.
    """
    rewrites = Counter(repair.finding.section for repair in report.repairs)
    proposed: list[Repair] = []
    held: list[Held] = []
    for repair in report.repairs:
        text = files.get(path_of(repair.finding.section))
        if rewrites[repair.finding.section] > 1:
            # Each was written from the original section, so applying one
            # leaves nothing for the other to be applied to.
            reason = (
                f"{rewrites[repair.finding.section]} repairs rewrite this section, "
                "each from the original, and only one of them could be applied"
            )
        elif text is None:
            reason = "the file is not there at the head of the pull request"
        elif text.count(repair.original) != 1:
            reason = (
                "the section does not read exactly once at the head of the pull "
                "request as it did when it was analysed"
            )
        else:
            proposed.append(repair)
            continue
        held.append(Held(repair, reason))
    return Routing(tuple(proposed), tuple(held))


def summary(
    report: Report,
    routing: Routing,
    *,
    links: Mapping[str, str],
    pull: str | None = None,
    degraded: str | None = None,
) -> str:
    """The comment for the pull request under review.

    `links` maps a section to where it reads at the head of the pull request.
    `pull` is the pull request carrying `routing.proposed`, or `degraded` says
    why there is none, in which case the repairs are shown here as diffs.

    Every section checked is counted once: accurate, repaired, or flagged. A
    section with any finding a human has to look at is flagged, even if another
    finding against it was repaired.
    """
    # A section naming deleted code was never verified, and has its own list:
    # nothing about it is a judgement, and nothing a model says could clear it.
    vanished = [flag for flag in report.flags if flag.cause == "vanished"]
    flags = [flag for flag in report.flags if flag.cause != "vanished"]
    gone = {flag.finding.section for flag in vanished}
    drifted = {finding.section for finding in report.findings}
    flagged = (
        {flag.finding.section for flag in flags}
        | {held.repair.finding.section for held in routing.held}
        | (gone & set(report.verified))
    )
    repaired = {repair.finding.section for repair in routing.proposed} - flagged
    accurate = [section for section in report.verified if section not in drifted]

    lines = [MARKER]
    if report.verified:
        lines.append(
            f"synclint checked {_count(len(report.verified), 'documentation section')} "
            f"linked to the code this pull request changes: {len(accurate)} accurate, "
            f"{len(repaired)} repaired, {len(flagged)} flagged."
        )
    elif not gone:
        lines.append(
            "synclint found no documentation linked to the code this pull request "
            "changes, so there was nothing to check."
        )
    if gone:
        lines.append(
            f"\n{_count(len(gone), 'section')} {'names' if len(gone) == 1 else 'name'} "
            "code this pull request deletes."
        )
    if report.unchecked:
        lines.append(
            f"\nThe run stopped at its spend ceiling with "
            f"{_count(report.unchecked, 'suspect')} never checked, so this is not "
            "the whole answer."
        )
    if routing.proposed:
        lines.append(
            f"\nThe repairs are in {pull}." if degraded is None
            else f"\nThe repairs are below rather than in a pull request: {degraded}."
        )

    if routing.proposed:
        lines.append("\n### Repaired")
    for repair in routing.proposed:
        lines += [
            "",
            f"{_heading(repair.finding.section, links)} — {repair.finding.explanation}",
            f"A {repair.shape.replace('-', ' ')}, {repair.confidence:.0%} confident.",
        ]
        if degraded is not None:
            lines += _details("The repair", repair.diff)

    if vanished:
        lines.append("\n### Names code this pull request deletes")
    for flag in vanished:
        lines += [
            "",
            f"{_heading(flag.finding.section, links)} — {flag.finding.explanation}",
            f"Not repaired: {flag.reason.rstrip('.')}.",
        ]

    if flags or routing.held:
        lines.append("\n### Flagged")
    for flag in flags:
        lines += [
            "",
            f"{_heading(flag.finding.section, links)} — {flag.finding.explanation}",
            f"Not repaired: {flag.reason.rstrip('.')}.",
        ]
        if flag.attempt is not None:
            lines += _details(
                "The rewrite that was tried",
                section_diff(flag.finding.section, flag.original, flag.attempt),
            )
    for held in routing.held:
        lines += [
            "",
            f"{_heading(held.repair.finding.section, links)} — "
            f"{held.repair.finding.explanation}",
            f"Repaired, but not proposed: {held.reason}.",
            *_details("The repair", held.repair.diff),
        ]

    if accurate:
        lines.append("\n### Accurate\n")
        lines += [f"- {_link(section, links)}" for section in accurate]

    spend = report.spend
    lines.append(
        f"\n<sub>{_count(spend.calls, 'model call')}, ${spend.dollars:.4f}.</sub>"
    )
    if report.index is not None:
        lines.append(f"<sub>{report.index.describe()}</sub>")
    # TODO: GitHub refuses a comment over 65,536 characters. A run that flags
    # dozens of sections with a rewrite each could reach that; nothing trims it.
    return "\n".join(lines) + "\n"


def publish(
    report: Report, root: Path, github: Requester, repository: str, number: int
) -> str:
    """Put `report` on pull request `number` of `repository`, and return the comment's URL.

    `root` is a clone holding the pull request's head commit, which the
    documentation is read from. The pull request of repairs is opened, or
    brought up to date, before the comment is written, so that the comment can
    link to it or say why there is none: the head is on a fork, or the token
    cannot write to the repository. Either way the comment is still written.
    """
    pull = PullRequest.fetch(github, repository, number)
    try:
        run(root, "cat-file", "-e", f"{pull.head}^{{commit}}")
    except subprocess.CalledProcessError:
        raise RuntimeError(
            f"{root} does not hold {pull.head}, the head of #{number}; it needs a "
            "clone with full history"
        ) from None
    # Every section the comment names, verified or not, so that each is linked.
    sections = dict.fromkeys(
        report.verified + tuple(finding.section for finding in report.findings)
    )
    files = {path: _read(root, pull.head, path) for path in map(path_of, sections)}
    routing = route(report, files)

    opened: str | None = None
    degraded: str | None = None
    if routing.proposed and pull.from_fork:
        degraded = (
            "this pull request comes from a fork, and a pull request of repairs can "
            "only target a branch in this repository"
        )
    elif routing.proposed:
        try:
            opened = _open_repairs(github, repository, pull, routing.proposed, files)
        except GitHubError as error:
            # 403 is how GitHub answers a token without write access to the
            # repository's contents. Anything else is a fault, not a permission.
            if error.status != 403:
                raise
            degraded = f"this run's token cannot write to the repository ({error})"

    links = {
        section: link
        for section in sections
        if (link := _section_link(repository, pull.head, section, files)) is not None
    }
    body = summary(report, routing, links=links, pull=opened, degraded=degraded)
    return _upsert(github, repository, number, body)


def path_of(section: str) -> str:
    """The file a section id names."""
    # A section id is its path, then `#` and its headings. A markdown file
    # whose own name holds a `#` would be split in the wrong place.
    return section.partition("#")[0]


def _read(root: Path, revision: str, path: str) -> str | None:
    try:
        return file_at(root, revision, path)
    except subprocess.CalledProcessError:
        return None


def _open_repairs(
    github: Requester,
    repository: str,
    pull: PullRequest,
    proposed: tuple[Repair, ...],
    files: Mapping[str, str | None],
) -> str:
    """Commit the repairs on top of the pull request's head, and open a pull request of them.

    One commit on one branch per pull request under review, made through the
    API rather than by pushing, so that the token is the only credential it
    needs. Returns the URL of the pull request of repairs.
    """
    rewritten: dict[str, str] = {}
    for repair in proposed:
        path = path_of(repair.finding.section)
        text = rewritten[path] if path in rewritten else files[path]
        assert text is not None, "route proposes a repair only to a file that is there"
        rewritten[path] = text.replace(repair.original, repair.repaired, 1)

    head = github("GET", f"/repos/{repository}/git/commits/{pull.head}")
    tree = github(
        "POST",
        f"/repos/{repository}/git/trees",
        {
            "base_tree": head["tree"]["sha"],
            # Markdown is never executable, and one that somehow was would
            # show the mode change in the repair's diff rather than lose it.
            "tree": [
                {"path": path, "mode": "100644", "type": "blob", "content": text}
                for path, text in rewritten.items()
            ],
        },
    )
    commit = github(
        "POST",
        f"/repos/{repository}/git/commits",
        {
            "message": f"repair the documentation #{pull.number} made inaccurate",
            "tree": tree["sha"],
            "parents": [pull.head],
        },
    )

    branch = f"synclint/repairs-{pull.number}"
    try:
        github(
            "POST",
            f"/repos/{repository}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
    except GitHubError as error:
        if error.status != 422:
            raise
        # 422 is a branch that already exists: an earlier push to the same
        # pull request made it. The repairs are recomputed against the new
        # head, so the branch is replaced rather than added to.
        # TODO: a human who amended the repair branch loses the amendment here.
        github(
            "PATCH",
            f"/repos/{repository}/git/refs/heads/{branch}",
            {"sha": commit["sha"], "force": True},
        )

    body = _pull_body(pull, proposed)
    owner = repository.partition("/")[0]
    existing = github(
        "GET", f"/repos/{repository}/pulls?head={owner}:{branch}&state=open"
    )
    if existing:
        number = existing[0]["number"]
        github("PATCH", f"/repos/{repository}/pulls/{number}", {"body": body})
        url: str = existing[0]["html_url"]
        return url
    opened = github(
        "POST",
        f"/repos/{repository}/pulls",
        {
            "title": f"Documentation repairs for #{pull.number}",
            "head": branch,
            "base": pull.branch,
            "body": body,
        },
    )
    url = opened["html_url"]
    return url


def _pull_body(pull: PullRequest, proposed: tuple[Repair, ...]) -> str:
    lines = [
        f"Documentation that #{pull.number} made inaccurate, rewritten. Merge it "
        f"into `{pull.branch}`, amend it, or close it.",
        "",
    ]
    lines += [
        f"- `{repair.finding.section}` — {repair.finding.explanation}"
        for repair in proposed
    ]
    return "\n".join(lines) + "\n"


def _upsert(github: Requester, repository: str, number: int, body: str) -> str:
    """Edit this pull request's synclint comment if it has one, or write it."""
    page = 1
    while comments := github(
        "GET", f"/repos/{repository}/issues/{number}/comments?per_page=100&page={page}"
    ):
        for comment in comments:
            if comment["body"].startswith(MARKER):
                edited = github(
                    "PATCH",
                    f"/repos/{repository}/issues/comments/{comment['id']}",
                    {"body": body},
                )
                url: str = edited["html_url"]
                return url
        page += 1
    written = github("POST", f"/repos/{repository}/issues/{number}/comments", {"body": body})
    url = written["html_url"]
    return url


def _section_link(
    repository: str, head: str, section: str, files: Mapping[str, str | None]
) -> str | None:
    """Where `section` reads at `head`, as lines of the file's source."""
    path = path_of(section)
    text = files.get(path)
    if text is None:
        return None
    url = f"{WEB}/{repository}/blob/{head}/{path}"
    # Lines of the source rather than a heading's anchor. GitHub numbers
    # repeated headings in a file, and a section id does not say which repeat
    # it is; the lines say exactly where its prose is.
    prose = next(
        (found.text for found in split_sections(text, path) if found.id == section), None
    )
    start = text.find(prose) if prose is not None else -1
    if prose is None or start < 0:
        return url
    first = text.count("\n", 0, start) + 1
    last = first + prose.count("\n")
    return f"{url}?plain=1#L{first}-L{last}"


def _heading(section: str, links: Mapping[str, str]) -> str:
    return f"**{_link(section, links)}**"


def _link(section: str, links: Mapping[str, str]) -> str:
    return f"[{section}]({links[section]})" if section in links else f"`{section}`"


def _details(title: str, diff: str) -> list[str]:
    # A section that quotes code carries its own fences, so this one has to be
    # longer than any run of backticks inside it.
    longest = max((len(run) for run in re.findall("`+", diff)), default=0)
    fence = "`" * max(3, longest + 1)
    return [
        "",
        f"<details><summary>{title}</summary>",
        "",
        f"{fence}diff",
        diff.rstrip("\n"),
        fence,
        "",
        "</details>",
    ]


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
