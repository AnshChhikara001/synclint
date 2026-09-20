# Store the index as JSON and search it with numpy

The obvious choice for embedding search is a vector database such as ChromaDB. A repository produces a few hundred to a few thousand chunks, so the entire embedding matrix fits comfortably in memory and a cosine similarity search over it is a single numpy dot product taking microseconds.

We store the index as a JSON file and compute similarity directly. A vector database here would add a dependency, a storage format, and a failure mode in exchange for nothing measurable at this scale. If a repository ever produces enough chunks that a linear scan is too slow, that is the point to revisit — and the threshold is far above anything we expect.

This is a deliberate deviation from the conventional stack. Do not "fix" it by adding a vector store without a measurement showing the scan is a bottleneck.
