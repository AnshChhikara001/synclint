"""Everything bookshelf raises."""


class BookshelfError(Exception):
    """Base class for every error this package raises."""


class DuplicateISBN(BookshelfError):
    """A catalogue already holds a book with that ISBN."""


class InvalidISBN(BookshelfError):
    """A string meant to be an ISBN is not a well-formed one."""


class ShelfFull(BookshelfError):
    """A shelf has no room left on it."""
