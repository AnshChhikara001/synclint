# Importing

Books come in from CSV. One row is one book, and the header row names the
columns.

## Reading a CSV

`read_csv(path)` reads a file with `title`, `author`, `isbn` and `year` columns
and returns a list of `Book`. Pass `delimiter` for tab-separated files.

```python
from bookshelf.importer import read_csv

books = read_csv("library.csv")
```

## Invalid rows

A row missing a column, or carrying a year that is not a number, is skipped.
Pass `skip_invalid=False` to `read_csv` to raise instead, which is what you want
for an import you are about to commit to.
