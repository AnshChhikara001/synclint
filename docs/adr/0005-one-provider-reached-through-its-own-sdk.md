# One model provider, reached through its own SDK

synclint asks a model whether a section still describes its chunk, and will later embed sections and chunks to propose links. Those are two different jobs and could come from two different providers — the strongest reasoning model from one, the cheapest good embedding model from another.

We use OpenAI for both. A maintainer installing the Action supplies one repository secret and reads one bill, and the spend ledger has one price table to keep honest. Splitting the two would buy a marginally better verification model in exchange for a second secret, a second failure mode, and a second set of prices to keep current — and the fixture corpus, not the vendor, is what will tell us whether verification is good enough.

We call it through the official `openai` package rather than posting to the API with `urllib`. Hand-rolling the request is close to the thirty-line bar this repository sets for taking a dependency, so the line count is not the argument. Retries, timeouts, typed errors and the structured-output plumbing are, and they are the parts that would otherwise be written badly and discovered in production. The adapter is one small class implementing `Model`, so the dependency touches exactly one file and swapping providers means writing another one.

The `Model` protocol is where tests inject their own, which is why none of this is reached in the test suite.
