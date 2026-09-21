# Exporting

Two writers, both taking any iterable of books.

## JSON

`write_json(books, path)` writes a list of objects with `title`, `author`,
`isbn` and `year` keys, indented by two spaces so that the file reads well in a
diff.

## CSV

`write_csv(books, path)` writes a header row and one row per book. Three columns
are written — title, author and ISBN — unless you pass `columns` yourself.
