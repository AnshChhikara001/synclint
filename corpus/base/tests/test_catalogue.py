import pytest

from bookshelf import Book, Catalogue
from bookshelf.errors import DuplicateISBN

DUNE = Book(title="Dune", author="Frank Herbert", isbn="978-0-441-01359-3", year=1965)
NEUROMANCER = Book(
    title="Neuromancer", author="William Gibson", isbn="9780441569595", year=1984
)


def test_a_book_is_held_under_its_normalised_isbn():
    catalogue = Catalogue([DUNE])

    assert catalogue.remove("9780441013593") == DUNE


def test_the_same_isbn_cannot_be_added_twice():
    catalogue = Catalogue([DUNE])

    with pytest.raises(DuplicateISBN):
        catalogue.add(DUNE)


def test_matches_come_back_in_the_order_they_were_added():
    catalogue = Catalogue([DUNE, NEUROMANCER])

    assert catalogue.find("") == [DUNE, NEUROMANCER]
    assert catalogue.find("gibson") == [NEUROMANCER]


def test_no_more_than_the_limit_comes_back():
    catalogue = Catalogue([DUNE, NEUROMANCER])

    assert catalogue.find("", 1) == [DUNE]


def test_the_newest_book_is_listed_first():
    catalogue = Catalogue([DUNE, NEUROMANCER])

    assert catalogue.recent(1960) == [NEUROMANCER, DUNE]
    assert catalogue.recent(1970) == [NEUROMANCER]
