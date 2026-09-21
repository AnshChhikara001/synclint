"""Writing books back out."""

import csv
import json


def write_json(books, path, indent=2):
    """Write books to a JSON file as a list of objects."""
    payload = [
        {"title": book.title, "author": book.author, "isbn": book.isbn, "year": book.year}
        for book in books
    ]
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=indent)


def write_csv(books, path, columns=("title", "author", "isbn")):
    """Write books to a CSV file, a header row and one row each."""
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for book in books:
            writer.writerow([getattr(book, column) for column in columns])
