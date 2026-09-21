# Searching

A query is a string. `Catalogue.find` parses it and matches it against each
book's title and author.

## Query syntax

`parse_query(text)` splits a query into required terms and alternatives. Terms
are required by default, and `dune OR duna` makes those two either-or. Matching
is substring matching, not whole words.

## Matching

`matches(book, query)` answers whether one book satisfies a parsed query. It
ignores case, so `dune` finds *Dune*; pass `case_sensitive=True` when that
matters.
