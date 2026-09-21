# ISBNs

Books are keyed by ISBN, so everything that enters the catalogue goes through
`bookshelf.isbn` on the way in.

## Normalising

`normalise(raw)` removes spacing and hyphens and upper-cases a trailing `X`, so
that `978-0-441-01359-3` and `9780441013593` are the same key. Pass
`strict=True` to raise `InvalidISBN` rather than return something malformed.

## Validating

`is_valid(isbn)` checks a normalised ISBN against its check digit. Only
thirteen-digit ISBNs are accepted — a ten-digit ISBN is always rejected, so
convert older ISBNs to thirteen digits before storing them.
