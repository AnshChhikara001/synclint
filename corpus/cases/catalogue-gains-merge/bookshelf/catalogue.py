"""The catalogue: every book you own, keyed by ISBN."""

from bookshelf.errors import DuplicateISBN
from bookshelf.isbn import normalise
from bookshelf.search import matches, parse_query


class Catalogue:
    """Every book you own, indexed by its normalised ISBN."""

    def __init__(self, books=()):
        self._books = {}
        for book in books:
            self.add(book)

    def add(self, book):
        """Record a book. Raises `DuplicateISBN` if its ISBN is already held."""
        isbn = normalise(book.isbn)
        if isbn in self._books:
            raise DuplicateISBN(isbn)
        self._books[isbn] = book

    def remove(self, isbn):
        """Drop a book from the catalogue and return it."""
        return self._books.pop(normalise(isbn))

    def find(self, query, limit=20):
        """The books matching `query`, in the order they were added."""
        parsed = parse_query(query)
        found = [book for book in self._books.values() if matches(book, parsed)]
        return found[:limit]

    def recent(self, since, limit=5):
        """The books published in or after `since`, newest first."""
        published = [
            book
            for book in self._books.values()
            if book.year is not None and book.year >= since
        ]
        return sorted(published, key=lambda book: book.year, reverse=True)[:limit]

    def count(self):
        """How many books the catalogue holds."""
        return len(self._books)

    def merge(self, other):
        """Add every book from another catalogue this one does not already hold."""
        for isbn, book in other.entries().items():
            self._books.setdefault(isbn, book)

    def entries(self):
        """Every book held, keyed by normalised ISBN."""
        return dict(self._books)
