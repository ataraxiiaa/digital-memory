import test from "node:test";
import assert from "node:assert/strict";
import { documents, filterDocuments, formatDate } from "../../src/frontend/lib/sample-data.ts";

test("empty search returns all documents in date order", () => {
  assert.equal(filterDocuments("  ", "All collections").length, 6);
  assert.deepEqual(documents.map(doc => doc.date), documents.map(doc => doc.date).sort().reverse());
});
test("search includes people, passages and tags regardless of case", () => {
  assert.equal(filterDocuments(" SAM CHEN ", "All collections").length, 3);
  assert.equal(filterDocuments("thermos", "All collections")[0].id, "4");
  assert.equal(filterDocuments("outdoors", "All collections")[0].id, "4");
});
test("collection filters combine with search and handle no results", () => {
  assert.equal(filterDocuments("Sam", "Studio").length, 2);
  assert.equal(filterDocuments("Sam", "Learning").length, 0);
  assert.equal(filterDocuments("no-such-note", "All collections").length, 0);
});
test("dates have a stable readable format", () => {
  assert.equal(formatDate("2026-11-12"), "Nov 12, 2026");
});
