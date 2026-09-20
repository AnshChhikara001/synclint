# Parse Python only, using the standard library `ast`

synclint needs to extract chunks from the repository it runs against. tree-sitter would let it parse many languages, at the cost of a native dependency and a grammar per language. We target Python repositories only and use the standard library's `ast` module, which adds no dependency and gives us exactly the node types we need.

Supporting a second language means adding tree-sitter later, not rewriting the extractor — chunk extraction sits behind a single interface so the parser can be swapped. Restricting to one language up front lets us spend the time on drift detection accuracy instead of on grammars.
