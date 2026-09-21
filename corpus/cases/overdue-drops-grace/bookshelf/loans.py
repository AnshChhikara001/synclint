"""Who has what, and when it is due back."""

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
    return Loan(book=book, borrower=borrower, due=date.today() + timedelta(days=days))


def renew(loan, days=14):
    """Return a fresh loan for the same book, due `days` from today."""
    return Loan(
        book=loan.book,
        borrower=loan.borrower,
        due=date.today() + timedelta(days=days),
    )


def overdue(loans, today):
    """The loans past their due date."""
    return [loan for loan in loans if today > loan.due]
