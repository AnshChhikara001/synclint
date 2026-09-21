# bookshelf

A small library for keeping track of the books you own: what you have, which
shelf it is on, and who currently has it. Nothing outside the standard library
is required.

## Installing

```
pip install bookshelf
```

## A quick tour

Build a `Catalogue`, put a `Book` in it, and ask it questions.

```python
from bookshelf import Book, Catalogue

catalogue = Catalogue()
catalogue.add(Book(title="Dune", author="Frank Herbert", isbn="9780441013593"))
catalogue.find("dune")
```

Every entry is keyed by its ISBN, so `add` refuses a book you already own.

## Where to go next

The rest of the documentation is in `docs/`: the catalogue, ISBNs, importing and
exporting, searching, shelves, loans, and storage.
