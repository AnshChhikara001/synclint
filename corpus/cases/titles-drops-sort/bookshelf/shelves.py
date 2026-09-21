"""Shelves: named groups of books, each with a limit."""

from bookshelf.errors import ShelfFull


class Shelf:
    """A named group of books with a limit on how many it holds."""

    def __init__(self, name, capacity=50):
        self.name = name
        self.capacity = capacity
        self._books = []

    def add(self, book):
        """Put a book on the shelf. Raises `ShelfFull` once it is at capacity."""
        if len(self._books) >= self.capacity:
            raise ShelfFull(self.name)
        self._books.append(book)

    def titles(self):
        """The titles on the shelf, alphabetically."""
        return sorted(book.title for book in self._books)
