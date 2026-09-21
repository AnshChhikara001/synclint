"""Query parsing and matching, which is all the searching a private library needs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    """A parsed query: terms that must all appear, and terms of which one must."""

    required: tuple
    alternatives: tuple


def parse_query(text):
    """Split a query string into required terms and OR alternatives.

    `dune OR duna` gives two alternatives. Every other word is required.
    """
    words = text.split()
    required = []
    alternatives = []
    position = 0
    while position < len(words):
        if position + 2 < len(words) and words[position + 1] == "OR":
            alternatives.extend([words[position], words[position + 2]])
            position += 3
        else:
            required.append(words[position])
            position += 1
    return Query(required=tuple(required), alternatives=tuple(alternatives))


def matches(book, query, case_sensitive=True):
    """Whether one book satisfies a parsed query."""
    haystack = f"{book.title} {book.author}"
    if not case_sensitive:
        haystack = haystack.lower()

    def present(term):
        return (term if case_sensitive else term.lower()) in haystack

    if not all(present(term) for term in query.required):
        return False
    return not query.alternatives or any(present(term) for term in query.alternatives)
