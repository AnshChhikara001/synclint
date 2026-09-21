"""Query parsing and matching, which is all the searching a private library needs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    """A parsed query: terms that must all appear, and terms of which one must."""

    required: tuple
    alternatives: tuple


def parse_query(text):
    """Split a query string into terms, every one of which is required."""
    return Query(required=tuple(text.split()), alternatives=())


def matches(book, query, case_sensitive=False):
    """Whether one book satisfies a parsed query."""
    haystack = f"{book.title} {book.author}"
    if not case_sensitive:
        haystack = haystack.lower()

    def present(term):
        return (term if case_sensitive else term.lower()) in haystack

    if not all(present(term) for term in query.required):
        return False
    return not query.alternatives or any(present(term) for term in query.alternatives)
