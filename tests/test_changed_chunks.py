from textwrap import dedent

from synclint.changes import ChunkChange, compare, is_test_file


def source(text: str) -> str:
    return dedent(text).lstrip()


def changed_chunks(before: str, after: str, path: str) -> list[ChunkChange]:
    """What changed in one file edited in place."""
    return list(compare({path: before}, {path: after}).changed)


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


def test_a_file_that_stops_parsing_is_neither_changed_nor_vanished() -> None:
    diff = compare({"src/http.py": "def fetch(url):\n    return url\n"}, {"src/http.py": "def fetch(:"})

    assert diff.changed == ()
    assert diff.vanished == ()


RETRY = source(
    """
    def backoff(attempt):
        return 2 ** attempt
    """
)


def test_a_chunk_deleted_from_its_file_has_vanished() -> None:
    diff = compare({"src/http.py": RETRY}, {"src/http.py": "PAUSE = 1\n"})

    assert [chunk.chunk for chunk in diff.vanished] == ["src/http.py::backoff"]
    assert diff.vanished[0].before.startswith("def backoff(attempt)")
    assert diff.changed == ()
    assert diff.moved == ()


def test_a_chunk_moved_to_another_file_is_followed_and_compared_there() -> None:
    diff = compare(
        {"src/http.py": RETRY},
        {"src/http.py": "PAUSE = 1\n", "src/wait.py": RETRY.replace("2 **", "3 **")},
    )

    assert diff.vanished == ()
    assert diff.moved == (("src/http.py::backoff", "src/wait.py::backoff"),)
    (change,) = diff.changed
    assert change.chunk == "src/http.py::backoff"
    assert "3 ** attempt" in change.after


def test_a_renamed_file_is_compared_across_the_rename() -> None:
    diff = compare(
        {"src/http.py": RETRY}, {"src/wait.py": RETRY}, {"src/http.py": "src/wait.py"}
    )

    assert diff.changed == ()
    assert diff.vanished == ()
    assert diff.moved == (("src/http.py::backoff", "src/wait.py::backoff"),)


def test_a_move_with_two_possible_destinations_is_not_guessed_at() -> None:
    diff = compare(
        {"src/http.py": RETRY},
        {"src/http.py": "PAUSE = 1\n", "src/wait.py": RETRY, "src/sleep.py": RETRY},
    )

    assert [chunk.chunk for chunk in diff.vanished] == ["src/http.py::backoff"]
    assert diff.moved == ()


def test_two_chunks_of_one_name_gone_are_not_both_followed_to_one() -> None:
    diff = compare(
        {"src/http.py": RETRY, "src/ftp.py": RETRY},
        {"src/http.py": "", "src/ftp.py": "", "src/wait.py": RETRY},
    )

    assert sorted(chunk.chunk for chunk in diff.vanished) == [
        "src/ftp.py::backoff",
        "src/http.py::backoff",
    ]


def test_recognises_the_files_a_change_to_which_cannot_reach_documentation() -> None:
    assert is_test_file("tests/test_http.py")
    assert is_test_file("src/synclint/http_test.py")
    assert is_test_file("conftest.py")

    assert not is_test_file("src/http.py")
    assert not is_test_file("src/testing.py")
    assert not is_test_file("src/latest/api.py")


def test_a_chunk_gone_while_a_file_the_change_added_will_not_parse_is_not_called_vanished() -> None:
    # It may well have moved there; nothing can say it did not.
    diff = compare({"src/http.py": RETRY}, {"src/http.py": "", "src/wait.py": "def backoff(:"})

    assert diff.vanished == ()
