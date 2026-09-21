# Shelves

A `Shelf` is a named group of books with a limit on how many it holds.

## Capacity

`Shelf("landing", capacity=20)` makes a shelf that holds twenty books. Leave the
capacity out and the shelf holds fifty. `Shelf.add` raises `ShelfFull` once the
shelf is full.

## Listing titles

`Shelf.titles()` returns the titles on the shelf, sorted alphabetically. Pass
`sort=False` to get them in the order they were added.
