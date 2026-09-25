"""Saving a catalogue to disk and reading it back."""

import json
from pathlib import Path

# Loading grew its own module; it is still imported from here.
from bookshelf.loading import load

__all__ = ["save", "load"]


def save(catalogue, path, backup=True):
    """Write a catalogue to `path` as JSON."""
    destination = Path(path)
    if backup and destination.exists():
        backup_path = destination.with_suffix(destination.suffix + ".bak")
        backup_path.write_bytes(destination.read_bytes())
    payload = [_entry(book) for book in catalogue.find("")]
    destination.write_text(json.dumps(payload, indent=2))


def _entry(book):
    return {
        "title": book.title,
        "author": book.author,
        "isbn": book.isbn,
        "year": book.year,
    }
