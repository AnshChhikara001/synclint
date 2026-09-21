from textwrap import dedent

import pytest

from synclint.changes import changed_chunks, is_test_file


def source(text: str) -> str:
    return dedent(text).lstrip()


def test_reports_a_chunk_whose_body_changed() -> None:
    before = source(
        """
        def retries(limit=3):
            return limit
        """
    )
    after = source(
        """
        def retries(limit=5):
            return limit
        """
    )

    changes = changed_chunks(before, after, "src/http.py")

    assert [change.chunk for change in changes] == ["src/http.py::retries"]


def test_reformatting_and_editing_comments_changes_nothing() -> None:
    before = source(
        """
        def retries(limit=3):
            # Three is what the old client used.
            return limit
        """
    )
    after = source(
        """
        def retries(
            limit=3,
        ):
            # Three, because the server gives up after four.
            return limit
        """
    )

    assert changed_chunks(before, after, "src/http.py") == []


def test_editing_only_a_docstring_changes_nothing() -> None:
    before = source(
        '''
        class Client:
            """Talks to the server."""

            def retries(self, limit=3):
                """How many times to retry."""
                return limit
        '''
    )
    after = source(
        '''
        class Client:
            """Talks to the server over HTTP."""

            def retries(self, limit=3):
                """The number of attempts made before giving up."""
                return limit
        '''
    )

    assert changed_chunks(before, after, "src/http.py") == []


def test_carries_both_sides_of_a_changed_signature() -> None:
    before = source(
        """
        def fetch(url, retries=3):
            return url
        """
    )
    after = source(
        """
        def fetch(url, attempts=3):
            return url
        """
    )

    (change,) = changed_chunks(before, after, "src/http.py")

    assert change.before == "def fetch(url, retries=3):\n    return url"
    assert change.after == "def fetch(url, attempts=3):\n    return url"


def test_a_changed_method_does_not_drag_in_the_class_that_holds_it() -> None:
    before = source(
        """
        class Client:
            def retries(self):
                return 3

            def timeout(self):
                return SECONDS
        """
    )
    after = source(
        """
        class Client:
            def retries(self):
                return 5

            def timeout(self):
                return SECONDS
        """
    )

    changes = changed_chunks(before, after, "src/http.py")

    assert [change.chunk for change in changes] == ["src/http.py::Client.retries"]


def test_a_changed_class_carries_its_own_source_and_not_its_methods() -> None:
    before = source(
        """
        class Client(Base):
            TIMEOUT = 30

            def fetch(self):
                return SECONDS_BETWEEN_ATTEMPTS
        """
    )
    after = before.replace("TIMEOUT = 30", "TIMEOUT = 60")

    (change,) = changed_chunks(before, after, "src/http.py")

    assert change.chunk == "src/http.py::Client"
    assert change.after == "class Client(Base):\n    TIMEOUT = 60"
    assert "SECONDS_BETWEEN_ATTEMPTS" not in change.after


def test_refuses_source_that_does_not_parse() -> None:
    with pytest.raises(SyntaxError):
        changed_chunks("def fetch(:", "def fetch(:", "src/http.py")


def test_recognises_the_files_a_change_to_which_cannot_reach_documentation() -> None:
    assert is_test_file("tests/test_http.py")
    assert is_test_file("src/synclint/http_test.py")
    assert is_test_file("conftest.py")

    assert not is_test_file("src/http.py")
    assert not is_test_file("src/testing.py")
    assert not is_test_file("src/latest/api.py")
