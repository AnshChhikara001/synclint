"""The GitHub REST API, reached with the standard library.

One request shape and one error type make this shorter than the thirty lines
that would justify a dependency, and unlike the model provider (ADR-0005)
nothing here needs retries or structured-output plumbing: a failed publish is
re-run by pushing again.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

API = "https://api.github.com"


class GitHubError(RuntimeError):
    """GitHub refused a request. `status` is the HTTP status it answered with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub answered {status}: {message}")
        self.status = status


class Requester(Protocol):
    """One call to the API. This is the injection point: tests supply their own."""

    def __call__(self, method: str, path: str, body: object = None) -> Any: ...


class GitHub:
    """Calls the REST API as the holder of `token`."""

    def __init__(self, token: str, *, api: str = API) -> None:
        self._token = token
        self._api = api

    def __call__(self, method: str, path: str, body: object = None) -> Any:
        request = urllib.request.Request(
            self._api + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            # GitHub explains itself in JSON; a proxy in front of it may not.
            text = error.read().decode("utf-8", errors="replace")
            detail = json.loads(text)["message"] if text.startswith("{") else error.reason
            raise GitHubError(error.code, detail) from None
        return json.loads(payload) if payload else None
