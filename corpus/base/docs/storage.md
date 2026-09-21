# Storage

A catalogue is saved as a single JSON file.

## Saving

`save(catalogue, path)` writes the catalogue to `path`. Whatever file is already
there is copied to `path.bak` first; pass `backup=False` to overwrite it
outright.

## Loading

`load(path)` reads a saved catalogue back. A path that does not exist raises
`FileNotFoundError` unless you pass `create_missing=True`, which gives you an
empty catalogue instead.
