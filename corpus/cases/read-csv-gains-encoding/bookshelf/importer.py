"""Reading books in from CSV."""

import csv

from bookshelf.book import Book
from bookshelf.isbn import normalise


def read_csv(path, delimiter=",", skip_invalid=True, encoding="utf-8"):
    """Read books from a CSV file with title, author, isbn and year columns."""
    with open(path, newline="", encoding=encoding) as handle:
        return read_rows(csv.DictReader(handle, delimiter=delimiter), skip_invalid)


def read_rows(rows, skip_invalid=True):
    """Turn already-parsed CSV rows into books."""
    books = []
    for row in rows:
        try:
            books.append(_book(row))
        except (KeyError, ValueError):
            if not skip_invalid:
                raise
    return books


def _book(row):
    year = row.get("year")
    return Book(
        title=row["title"],
        author=row["author"],
        isbn=normalise(row["isbn"]),
        year=int(year) if year else None,
    )
