"""Reading a saved catalogue back."""

import json
from pathlib import Path

from bookshelf.book import Book
from bookshelf.catalogue import Catalogue


def load(path, create_missing=False):
    """Read a catalogue back from `path`."""
    source = Path(path)
    if not source.exists():
        if create_missing:
            return Catalogue()
        raise FileNotFoundError(path)
    return Catalogue(Book(**entry) for entry in json.loads(source.read_text()))
