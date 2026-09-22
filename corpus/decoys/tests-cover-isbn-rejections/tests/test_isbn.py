import pytest

from bookshelf.errors import InvalidISBN
from bookshelf.isbn import is_valid, normalise


def test_spacing_and_hyphens_do_not_survive_normalising():
    assert normalise("978-0-441-01359-3") == "9780441013593"
    assert normalise("978 0 441 01359 3") == "9780441013593"


def test_a_trailing_x_comes_back_upper_cased():
    assert normalise("80-85955-9x") == "80859559X"


def test_strict_normalising_refuses_what_does_not_validate():
    with pytest.raises(InvalidISBN):
        normalise("978-0-441-01359-4", strict=True)


def test_a_thirteen_digit_isbn_is_checked_against_its_check_digit():
    assert is_valid("9780441013593")
    assert not is_valid("9780441013594")


def test_another_publisher_validates_the_same_way():
    assert is_valid("9780441569595")


def test_a_ten_digit_isbn_is_never_valid():
    assert not is_valid("0441013597")


def test_anything_that_is_not_thirteen_digits_is_rejected():
    assert not is_valid("")
    assert not is_valid("97804410135931")


def test_a_letter_where_a_digit_should_be_is_rejected():
    assert not is_valid("97804410135X3")
