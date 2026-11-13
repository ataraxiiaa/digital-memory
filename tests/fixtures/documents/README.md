# Synthetic document fixtures

Ten original fictional UTF-8 `.txt` documents for Digital Memory development. These were authored for this repository, not downloaded from third-party sources. All people, places, messages, and events are invented. They contain no real personal data and require no external service.

## Format

Each file starts with seven metadata lines: `Synthetic`, `ID`, `Title`, `Date`, `Type`, `Collection`, and `Author`. A blank line separates metadata from the original body. Dates use `YYYY-MM-DD` and describe the document date, not necessarily the date of every event mentioned. Preserve paragraph boundaries for future source citations. The metadata format is a proposed ingestion fixture contract; no parser or backend is implemented yet.

The set expands the frontend's six sample themes into ten standalone source documents. It is independent of the currently hardcoded frontend data and is not automatically loaded by the interface.

## Manual acceptance scenarios for future ingestion

| Question or check | Expected evidence |
| --- | --- |
| What did Alex and Sam agree to build? | sample-005, body paragraph 2: a reading space with source-linked results. |
| Who owns the fictional document task? | sample-005, body paragraph 3: Alex. |
| When is the next prototype review? | sample-008, body paragraphs 2–3: confirmed November 20 at 10:30; supersedes November 18. No time zone recorded. |
| Has that review already happened? | No: sample-008 and sample-010 describe an upcoming event. |
| Is the spring trip booked? | No: sample-003 and sample-006 explicitly leave it unconfirmed and undated. |
| Find notes mentioning Sam Chen | sample-001 through sample-007 and sample-010 mention the full name in metadata or body; sample-008 identifies Sam as author. sample-009 does not mention Sam. |
| Does every mention of cedar refer to Cedar Library? | No: sample-009 explicitly refers to a tree. |
| Which notes share a document date? | sample-006/007: November 12; sample-008/009/010: November 13. Preserve both/all rather than collapsing them. |
| Search for an absent term such as submarine | No document matches. |
| Can each extracted assertion be traced to a passage? | Store the document ID and exact body passage or offsets; do not cite this answer table as evidence. |

These are review expectations, not passing automated integration tests. There are 5 Studio, 3 Learning, and 2 Personal documents. IDs are unique, and filenames follow document-date order; same-day ordering has no implied event time.
