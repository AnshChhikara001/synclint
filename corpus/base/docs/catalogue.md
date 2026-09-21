# Catalogue

A `Catalogue` holds every book you own, keyed by its normalised ISBN. Create one
empty, or from any iterable of books. Everything the catalogue can do is on this
page: adding, removing, finding, and counting.

## Adding books

`Catalogue.add(book)` records a book. Its ISBN is normalised first, so hyphens
and spacing do not matter. Adding a book whose ISBN is already held raises
`DuplicateISBN` — a catalogue never holds the same ISBN twice.

## Removing books

`Catalogue.remove(isbn)` drops a book and returns the `Book` it removed, so you
can put it straight onto a shelf or hand it to someone. An ISBN the catalogue
does not hold raises `KeyError`.

## Finding books

`Catalogue.find(query)` returns the books matching a query string, in the order
they were added. At most twenty come back; pass `limit` for more or fewer.

```python
catalogue.find("herbert", limit=3)
```

## Recent additions

`Catalogue.recent(since)` lists the books published in or after a given year,
newest first. Five come back unless you ask for more.

## Counting

`Catalogue.count()` is how many books are held.
