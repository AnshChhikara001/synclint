# Loans

Lending is tracked outside the catalogue: a `Loan` records the book, who has it,
and when it is due back.

## Lending a book

`lend(book, borrower)` returns a `Loan` due back in fourteen days. Pass `days`
for a longer or shorter loan.

## Renewing

`renew(loan)` returns a fresh `Loan` for the same book and borrower, due
fourteen days from today rather than fourteen days from the original due date.

## Overdue loans

`overdue(loans, today)` returns the loans past their due date. Pass `grace_days`
to ignore the ones that are only a little late.
