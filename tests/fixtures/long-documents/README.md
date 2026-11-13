# Long-document fixtures

Three full-length UTF-8 texts downloaded from Project Gutenberg. These are published literary works, **not synthetic documents**. Keep this corpus separate from the original fictional personal-note fixtures in `../documents/`.

| File | Author | Bytes | Whitespace-delimited words |
| --- | --- | ---: | ---: |
| pride-and-prejudice.txt | Jane Austen | 772,386 | 130,416 |
| moby-dick.txt | Herman Melville | 1,276,315 | 215,845 |
| war-and-peace.txt | Leo Tolstoy | 3,359,610 | 566,333 |

Counts include Gutenberg front matter and license text. Plain text has no fixed page count. Original downloaded bytes, credits, and license notices are preserved. `manifest.json` records source pages, download URLs, UTC download timestamps, byte and word counts, and SHA-256 checksums. Project Gutenberg lists these editions as public domain in the USA; its included license explains use elsewhere.

Sources:

- [Pride and Prejudice, ebook 1342](https://www.gutenberg.org/ebooks/1342)
- [Moby Dick, ebook 2701](https://www.gutenberg.org/ebooks/2701)
- [War and Peace, ebook 2600](https://www.gutenberg.org/ebooks/2600)

## Future ingestion checks

- Import each file without silent truncation; verify the full byte count and checksum before processing.
- Separate Gutenberg front matter and license text from searchable book content while preserving the original file and original offsets.
- Split content into passages while retaining headings, order, document identity, and source positions.
- Search for passages from the beginning, middle, and end, and verify exact source links.
- Measure import time, chunk count, and peak memory independently of the small-note corpus.
- Treat book characters and historical narrative as document content, not as real user contacts or calendar events. Download dates are not story-event dates.

Download validation confirmed strict UTF-8 decoding, expected titles, and Gutenberg start/end markers. This establishes fixture integrity, not app support: no ingestion backend has been implemented, and these files are not loaded into the frontend.
