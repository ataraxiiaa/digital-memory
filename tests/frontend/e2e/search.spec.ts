// NOT EXECUTED by the Phase 4 author: Playwright ports 3100/3199 are shared with other work.
// It was only parsed with `playwright test --list`. Routes are mocked before the view opens.
import { test, expect, type Page } from "@playwright/test";

const user = { id: "00000000-0000-0000-0000-000000000001", email: "alex@example.test", role: "authenticated", aud: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-11-13T10:00:00Z" };
const headers = { Authorization: "Bearer fixture-access-token" };
const DOC = "00000000-0000-0000-0000-000000000010";
const documentRow = { id: DOC, title: "Q3 notes", document_date: null, collection: "Work", created_at: "2026-11-13T12:00:00Z", status: "ready", content: "" };
const citation = { document_id: DOC, extraction_id: "00000000-0000-0000-0000-0000000000e1", page_number: 2, region_id: "00000000-0000-0000-0000-0000000000f1", start_char: 0, end_char: 30 };

async function signIn(page: Page) {
  await page.route("**/api/documents?*", route => route.fulfill({ json: [documentRow] }));
  await page.route(`**/api/documents/${DOC}`, route => route.fulfill({ json: { ...documentRow, original_filename: null } }));
  await page.route("https://auth-fixture.supabase.co/auth/v1/**", route => route.fulfill({ json: { access_token: "fixture-access-token", refresh_token: "r", expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, token_type: "bearer", user } }));
  await page.route("**/api/auth/me", route => route.fulfill({ json: user }));
}

async function openSearch(page: Page) {
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill("alex@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fictional-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: /^S?\s*Search$/ }).click();
}

const status = (state: "missing" | "current") => ({ documents: [{ document_id: DOC, title: "Q3 notes", status: state, passage_count: state === "current" ? 2 : 0, indexed_at: null }],
  current: state === "current" ? 1 : 0, stale: 0, missing: state === "missing" ? 1 : 0, not_ready: 0 });

test("search shows freshness, safe highlighted snippets, and opens the source document", async ({ page }) => {
  await signIn(page);
  let indexed = false;
  const requests: string[] = [];
  await page.route("**/api/search/status", route => route.fulfill({ json: status(indexed ? "current" : "missing") }));
  await page.route("**/api/search/index", async route => {
    expect(route.request().method()).toBe("POST");
    indexed = true;
    await route.fulfill({ json: { documents: 1, passages: 2, skipped: 0 } });
  });
  await page.route("**/api/search?*", route => {
    requests.push(new URL(route.request().url()).search);
    return route.fulfill({ json: { query: "budget", type: "all", totals: { passages: 1 }, offset: 0, limit: 20, results: [{
      kind: "passage", score: 0.4, document_id: DOC, document_title: "Q3 notes", page_number: 2, citation, review_required: true,
      snippet: [{ text: "<script>window.hacked = true</script> The ", match: false }, { text: "budget", match: true }, { text: " was approved", match: false }],
      entity_id: null, mention_id: null, entity_type: null, entity_status: null, mention_count: null, timeline_item_id: null,
      date_text: null, start_date: null, review_status: null }] } });
  });

  await openSearch(page);
  await expect(page.getByText("1 document isn’t searchable yet.")).toBeVisible();
  await page.getByRole("button", { name: "Update search index" }).click();
  await expect(page.getByText("Indexed 2 passages from 1 document.")).toBeVisible();
  await expect(page.getByText("Search is up to date for 1 document.")).toBeVisible();

  await page.getByLabel("Search your documents").fill("budget");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.locator(".search-snippet mark")).toHaveText("budget");
  // Source text is rendered as text: no script element exists and nothing ran.
  await expect(page.locator(".search-snippet script")).toHaveCount(0);
  await expect(page.getByText("<script>window.hacked = true</script>", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { hacked?: boolean }).hacked)).toBeUndefined();
  await expect(page.getByText("check against the original")).toBeVisible();
  expect(requests.at(-1)).toContain("q=budget");

  await page.getByRole("button", { name: "Open Q3 notes, page 2" }).click();
  await expect(page.getByRole("dialog").getByRole("heading", { name: "Q3 notes" })).toBeVisible();
});

test("blank searches are refused locally and misses explain themselves", async ({ page }) => {
  await signIn(page);
  let calls = 0;
  await page.route("**/api/search/status", route => route.fulfill({ json: status("current") }));
  await page.route("**/api/search?*", route => { calls++; return route.fulfill({ json: { query: "zzz", type: "all", totals: {}, offset: 0, limit: 20, results: [] } }); });
  await openSearch(page);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Enter something to search for." })).toBeVisible();
  expect(calls).toBe(0);
  await page.getByLabel("Search your documents").fill("zzz");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("No matches for “zzz”.")).toBeVisible();
});

test("search errors are shown without details and the narrow layout fits", async ({ page }) => {
  await signIn(page);
  await page.route("**/api/search/status", route => route.fulfill({ status: 503, json: { detail: "Couldn’t search right now. Please try again." } }));
  await page.route("**/api/search?*", route => route.fulfill({ status: 503, json: { detail: "Couldn’t search right now. Please try again." } }));
  await page.setViewportSize({ width: 390, height: 844 });
  await openSearch(page);
  await page.getByLabel("Search your documents").fill("budget");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Couldn’t search right now." }).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("search proxies require credentials, validate input and forward only allowlisted parameters", async ({ request }) => {
  expect((await request.get("/api/search?q=a")).status()).toBe(401);
  expect((await request.get("/api/search/status")).status()).toBe(401);
  expect((await request.post("/api/search/index")).status()).toBe(401);
  expect((await request.get("/api/search?q=%20%20", { headers })).status()).toBe(422);
  expect((await request.get(`/api/search?q=${"x".repeat(201)}`, { headers })).status()).toBe(422);
  expect((await request.get("/api/search", { headers })).status()).toBe(422);
  const listed = await request.get(`/api/search?q=budget&type=passages&limit=5&offset=10&document_id=${DOC}&evil=1&type=bogus`, { headers });
  expect(listed.status()).toBe(200);
  expect((await listed.json()).query).toBe(`q=budget&type=passages&document_id=${DOC}&limit=5&offset=10`);
  const bad = await request.get("/api/search?q=a&type=bogus&limit=999&document_id=nope", { headers });
  expect((await bad.json()).query).toBe("q=a");
  expect((await request.get("/api/search/status", { headers })).status()).toBe(200);
  expect((await request.post("/api/search/index", { headers })).status()).toBe(200);
  expect((await request.post(`/api/documents/${DOC}/search-index`, { headers })).status()).toBe(200);
  expect((await request.post("/api/documents/not-an-id/search-index", { headers })).status()).toBe(404);
});
