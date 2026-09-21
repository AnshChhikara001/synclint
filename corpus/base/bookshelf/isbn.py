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
    """Whether a normalised ISBN is a well-formed thirteen-digit ISBN."""
    if len(isbn) != 13 or not isbn.isdigit():
        return False
    weighted = sum(
        int(digit) * (1 if position % 2 == 0 else 3)
        for position, digit in enumerate(isbn[:12])
    )
    return (10 - weighted % 10) % 10 == int(isbn[12])
