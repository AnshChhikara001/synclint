"""Turning what people type into the key a catalogue is indexed by.

Everything entering a catalogue comes through `normalise` first, so that the
same book typed two ways lands on one key rather than two.
"""

from bookshelf.errors import InvalidISBN

# Spacing and hyphens are the only punctuation an ISBN is ever written with,
# and neither carries information: 978-0-441-01359-3 is one key, not five.
_NOISE = str.maketrans("", "", " -")


def normalise(raw, strict=False):
    """Strip spacing and hyphens from an ISBN and upper-case a trailing X.

    With `strict`, an ISBN that does not validate raises `InvalidISBN` rather
    than coming back as it went in. The error carries the ISBN as it was given
    rather than as it was cleaned, so that the caller recognises what they
    passed.
    """
    cleaned = raw.translate(_NOISE).upper()
    if strict and not is_valid(cleaned):
        raise InvalidISBN(raw)
    return cleaned


def is_valid(isbn):
    """Whether a normalised ISBN is a well-formed thirteen-digit ISBN."""
    if len(isbn) != 13 or not isbn.isdigit():
        return False
    # ISBN-13 weights the first twelve digits alternately by one and three.
    # The check digit is whatever brings that weighted sum up to a multiple
    # of ten, which is why the comparison below reads as it does.
    weighted = sum(
        int(digit) * (1 if position % 2 == 0 else 3)
        for position, digit in enumerate(isbn[:12])
    )
    return (10 - weighted % 10) % 10 == int(isbn[12])
