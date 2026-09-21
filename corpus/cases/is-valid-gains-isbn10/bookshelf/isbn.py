"""Turning what people type into the key a catalogue is indexed by."""

from bookshelf.errors import InvalidISBN

_NOISE = str.maketrans("", "", " -")


def normalise(raw, strict=False):
    """Strip spacing and hyphens from an ISBN and upper-case a trailing X.

    With `strict`, an ISBN that does not validate raises `InvalidISBN` rather
    than coming back as it went in.
    """
    cleaned = raw.translate(_NOISE).upper()
    if strict and not is_valid(cleaned):
        raise InvalidISBN(raw)
    return cleaned


def is_valid(isbn):
    """Whether a normalised ISBN is well formed, in ten digits or thirteen."""
    if len(isbn) == 10:
        return _valid_isbn10(isbn)
    if len(isbn) != 13 or not isbn.isdigit():
        return False
    weighted = sum(
        int(digit) * (1 if position % 2 == 0 else 3)
        for position, digit in enumerate(isbn[:12])
    )
    return (10 - weighted % 10) % 10 == int(isbn[12])


def _valid_isbn10(isbn):
    if not (isbn[:9].isdigit() and (isbn[9].isdigit() or isbn[9] == "X")):
        return False
    weighted = sum(
        (10 - position) * int(digit) for position, digit in enumerate(isbn[:9])
    )
    check = 10 if isbn[9] == "X" else int(isbn[9])
    return (weighted + check) % 11 == 0
