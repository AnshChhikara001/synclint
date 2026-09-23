"""Saving a catalogue to disk and reading it back."""

import gzip
import json
from pathlib import Path

from bookshelf.book import Book
from bookshelf.catalogue import Catalogue


def save(catalogue, path, backup=True, compress=False):
    """Write a catalogue to `path` as JSON."""
    destination = Path(path)
    if backup and destination.exists():
        backup_path = destination.with_suffix(destination.suffix + ".bak")
        backup_path.write_bytes(destination.read_bytes())
    payload = [_entry(book) for book in catalogue.find("")]
    document = json.dumps(payload, indent=2)
    if compress:
        destination.write_bytes(gzip.compress(document.encode("utf-8")))
    else:
        destination.write_text(document)


def load(path, create_missing=False):
    """Read a catalogue back from `path`."""
    source = Path(path)
    if not source.exists():
        if create_missing:
            return Catalogue()
        raise FileNotFoundError(path)
    return Catalogue(Book(**entry) for entry in json.loads(source.read_text()))


def _entry(book):
    return {
        "title": book.title,
        "author": book.author,
        "isbn": book.isbn,
        "year": book.year,
    }
