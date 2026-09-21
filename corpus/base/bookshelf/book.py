"""The one thing a bookshelf is made of."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Book:
    """A book you own."""

    title: str
    author: str
    isbn: str
    year: int | None = None

    def display_title(self, include_author=True):
        """The book as it should read in a listing."""
        if include_author:
            return f"{self.title} by {self.author}"
        return self.title
