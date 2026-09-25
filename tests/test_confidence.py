from textwrap import dedent

from synclint.changes import ChunkChange, compare
from synclint.confidence import SHAPES, outside_reason, shape_of


def change(before: str, after: str) -> ChunkChange:
    (only,) = compare({"lib.py": dedent(before)}, {"lib.py": dedent(after)}).changed
    return only


def test_the_eligible_shapes_are_a_fixed_list_that_can_be_read_off() -> None:
    assert [shape.name for shape in SHAPES] == ["renamed-parameter", "changed-default"]


def test_a_parameter_renamed_everywhere_it_is_used_is_a_renamed_parameter() -> None:
    renamed = change(
        """
        def find(query, limit=20):
            return [book for book in books if query in book][:limit]
        """,
        """
        def find(text, limit=20):
            return [book for book in books if text in book][:limit]
        """,
    )

    shape = shape_of(renamed)

    assert shape is not None and shape.name == "renamed-parameter"


def test_a_renamed_keyword_only_parameter_is_a_renamed_parameter() -> None:
    renamed = change(
        "def lend(book, *, borrower):\n    return (book, borrower)\n",
        "def lend(book, *, reader):\n    return (book, reader)\n",
    )

    shape = shape_of(renamed)

    assert shape is not None and shape.name == "renamed-parameter"


def test_a_rename_that_also_changes_what_the_body_does_is_outside_the_gate() -> None:
    renamed = change(
        "def find(query):\n    return query\n",
        "def find(text):\n    return text.lower()\n",
    )

    assert shape_of(renamed) is None


def test_a_rename_onto_a_name_the_body_already_used_is_outside_the_gate() -> None:
    # Renaming `query` to `text` in the body would conflate two variables, so
    # the rename rule cannot say the body is otherwise unchanged.
    renamed = change(
        "def find(query):\n    text = 1\n    return query + text\n",
        "def find(text):\n    text = 1\n    return text + text\n",
    )

    assert shape_of(renamed) is None


def test_two_parameters_renamed_at_once_is_outside_the_gate() -> None:
    renamed = change(
        "def write_json(books, path):\n    return books, path\n",
        "def write_json(items, destination):\n    return items, destination\n",
    )

    assert shape_of(renamed) is None


def test_one_default_value_changed_is_a_changed_default() -> None:
    changed = change(
        "def renew(loan, days=14):\n    return loan + days\n",
        "def renew(loan, days=7):\n    return loan + days\n",
    )

    shape = shape_of(changed)

    assert shape is not None and shape.name == "changed-default"


def test_a_changed_keyword_only_default_is_a_changed_default() -> None:
    changed = change(
        "def matches(text, *, case_sensitive=False):\n    return text\n",
        "def matches(text, *, case_sensitive=True):\n    return text\n",
    )

    shape = shape_of(changed)

    assert shape is not None and shape.name == "changed-default"


def test_a_default_changed_alongside_the_body_is_outside_the_gate() -> None:
    changed = change(
        "def renew(loan, days=14):\n    return loan + days\n",
        "def renew(loan, days=7):\n    return loan - days\n",
    )

    assert shape_of(changed) is None


def test_two_defaults_changed_at_once_is_outside_the_gate() -> None:
    changed = change(
        "def page(start=0, size=10):\n    return start, size\n",
        "def page(start=1, size=20):\n    return start, size\n",
    )

    assert shape_of(changed) is None


def test_a_default_added_to_a_parameter_that_had_none_is_outside_the_gate() -> None:
    changed = change(
        "def renew(loan, days):\n    return loan + days\n",
        "def renew(loan, days=7):\n    return loan + days\n",
    )

    assert shape_of(changed) is None


def test_a_removed_parameter_is_outside_the_gate() -> None:
    changed = change(
        "def overdue(loan, grace=0):\n    return loan > grace\n",
        "def overdue(loan):\n    return loan > 0\n",
    )

    assert shape_of(changed) is None


def test_a_class_is_outside_the_gate_whatever_changed() -> None:
    changed = change(
        "class Shelf:\n    capacity = 50\n",
        "class Shelf:\n    capacity = 25\n",
    )

    assert shape_of(changed) is None


def test_a_method_is_judged_by_the_same_rules_as_a_function() -> None:
    changed = change(
        """
        class Shelf:
            def __init__(self, capacity=50):
                self.capacity = capacity
        """,
        """
        class Shelf:
            def __init__(self, capacity=25):
                self.capacity = capacity
        """,
    )

    shape = shape_of(changed)

    assert shape is not None and shape.name == "changed-default"


def test_outside_the_gate_says_what_the_change_touched() -> None:
    changed = change(
        "def renew(loan, days=14):\n    return loan + days\n",
        "def renew(loan, days=7):\n    return loan - days\n",
    )

    reason = outside_reason(changed)

    assert "renamed parameter" in reason and "changed default" in reason
    assert "parameters and body" in reason


def test_outside_the_gate_says_when_the_chunk_is_a_class() -> None:
    changed = change("class Shelf:\n    capacity = 50\n", "class Shelf:\n    capacity = 25\n")

    assert "class" in outside_reason(changed)


def test_a_change_to_type_parameters_alone_is_outside_the_gate_and_says_so() -> None:
    changed = change("def first(items):\n    return items[0]\n", "def first[T](items):\n    return items[0]\n")

    assert shape_of(changed) is None
    assert "type parameters" in outside_reason(changed)
