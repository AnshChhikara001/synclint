from datetime import date, timedelta

from bookshelf.book import Book
from bookshelf.loans import lend, overdue, renew

DUNE = Book(title="Dune", author="Frank Herbert", isbn="9780441013593", year=1965)


def test_a_loan_runs_a_fortnight_unless_told_otherwise():
    loan = lend(DUNE, "reader")

    assert loan.due == date.today() + timedelta(days=14)


def test_a_shorter_loan_comes_back_sooner():
    loan = lend(DUNE, "reader", 3)

    assert loan.due == date.today() + timedelta(days=3)


def test_renewing_counts_from_today_not_from_the_old_due_date():
    late = lend(DUNE, "reader", -30)

    assert renew(late).due == date.today() + timedelta(days=14)


def test_only_the_loans_past_their_due_date_are_overdue():
    late = lend(DUNE, "reader", -1)
    current = lend(DUNE, "reader", 7)

    assert overdue([late, current], date.today()) == [late]


def test_grace_days_keep_the_barely_late_off_the_list():
    late = lend(DUNE, "reader", -1)

    assert overdue([late], date.today(), grace_days=1) == []
