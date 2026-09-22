"""Who has what, and when it is due back.

Loans are values rather than rows in the catalogue: nothing here changes a
`Book`, and a renewal is a new `Loan` rather than an edit to the old one.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from bookshelf.book import Book


@dataclass(frozen=True)
class Loan:
    """A book that is out, who has it, and when it is due back."""

    book: Book
    borrower: str
    due: date


def lend(book, borrower, days=14):
    """Lend a book, due back after `days`."""
    # Two weeks is what the local library gives you, and long enough that a
    # reader who forgets about it for a weekend is not immediately overdue.
    return Loan(book=book, borrower=borrower, due=date.today() + timedelta(days=days))


def renew(loan, days=14):
    """Return a fresh loan for the same book, due `days` from today."""
    # Counted from today rather than from the loan's own due date: renewing a
    # loan that is already late should not hand back one that is still late.
    return Loan(
        book=loan.book,
        borrower=loan.borrower,
        due=date.today() + timedelta(days=days),
    )


def overdue(loans, today, grace_days=0):
    """The loans past their due date, allowing `grace_days` of slack."""
    # `today` is passed in rather than read off the clock so that a caller can
    # ask what was overdue on a day that has already been and gone.
    return [loan for loan in loans if (today - loan.due).days > grace_days]
