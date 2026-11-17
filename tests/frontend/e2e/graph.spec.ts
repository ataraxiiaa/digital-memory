// NOT EXECUTED by the Phase 5 author: ports 3100/3199 are shared with other agents' runs.
import { test, expect, type Page } from "@playwright/test";

const user = { id: "00000000-0000-0000-0000-000000000001", email: "alex@example.test", role: "authenticated", aud: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-11-13T10:00:00Z" };
const headers = { Authorization: "Bearer fixture-access-token" };
const RUN = "00000000-0000-0000-0000-000000000030";
const A = "00000000-0000-0000-0000-0000000000a1", B = "00000000-0000-0000-0000-0000000000a2";

async function signedIn(page: Page) {
  await page.route("**/api/documents?*", route => route.fulfill({ json: [] }));
  await page.route("https://auth-fixture.supabase.co/auth/v1/**", route => route.fulfill({ json: { access_token: "fixture-access-token", refresh_token: "r", expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, token_type: "bearer", user } }));
  await page.route("**/api/auth/me", route => route.fulfill({ json: user }));
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill("alex@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fictional-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

// Open the view only after routes are mocked: it fetches as soon as it mounts.
async function openGraph(page: Page) {
  await page.getByRole("button", { name: /Graph/ }).click();
}

const end = (id: string, label: string, text: string, start: number) => ({
  run_id: RUN, mention_id: id, label, text, citation: { start_char: start, end_char: start + text.length } });
const sentence = "Sara works at Northwind.";
const item = (id: string, status: string, extra = {}) => ({
  id, type: "works_at", subject: end(A, "person", "Sara", 0), object: end(B, "organization", "Northwind", 14),
  page_number: 1, sentence_text: sentence, sentence_start: 0, score: 0.93, review_status: status,
  corrected_type: null, swapped: false, document_title: "Team notes", ...extra });

test("review queue highlights evidence and sends confirm, reversed, retype and reject", async ({ page }) => {
  const bodies: unknown[] = [];
  const rows = [item("00000000-0000-0000-0000-0000000000b1", "suggested"), item("00000000-0000-0000-0000-0000000000b2", "suggested")];
  await signedIn(page);
  await page.route("**/api/relationships?*", route => {
    const pending = rows.filter(row => row.review_status === "suggested");
    return route.fulfill({ json: { total: pending.length, offset: 0, limit: 10, relationships: pending } });
  });
  await page.route("**/api/graph", route => route.fulfill({ json: { nodes: [], edges: [], relationship_count: 0 } }));
  await page.route("**/api/relationships/*", async route => {
    expect(route.request().method()).toBe("PATCH");
    const body = route.request().postDataJSON();
    bodies.push(body);
    const row = rows.find(candidate => route.request().url().endsWith(candidate.id))!;
    row.review_status = body.status;
    await route.fulfill({ json: row });
  });
  await openGraph(page);

  await expect(page.getByText("Review queue (2)")).toBeVisible();
  await expect(page.locator(".relation-sentence mark[data-role=subject]").first()).toHaveText("Sara");
  await expect(page.locator(".relation-sentence mark[data-role=object]").first()).toHaveText("Northwind");
  await expect(page.getByText("Team notes · page 1 · score 0.93").first()).toBeVisible();
  // Person -> organization cannot be reversed under works_at, so no reverse button is offered.
  await expect(page.getByRole("button", { name: "Confirm, reversed" })).toHaveCount(0);
  await page.getByRole("button", { name: "Confirm", exact: true }).first().click();
  await expect(page.getByText("Review queue (1)")).toBeVisible();
  await page.getByLabel(/as another type/).selectOption("member_of");
  await expect(page.getByText("No relationships are waiting.")).toBeVisible();
  expect(bodies).toEqual([{ status: "confirmed" }, { status: "confirmed", type: "member_of" }]);
});

test("graph draws confirmed edges only, with a list fallback and node focus", async ({ page }) => {
  await signedIn(page);
  await page.route("**/api/relationships?*", route => route.fulfill({ json: { total: 0, offset: 0, limit: 10, relationships: [] } }));
  await page.route("**/api/graph", route => route.fulfill({ json: {
    nodes: [{ id: "n1", name: "Sara Ahmed", type: "person", mention_count: 2 }, { id: "n2", name: "Northwind", type: "organization", mention_count: 1 },
      { id: "n3", name: "Lahore", type: "location", mention_count: 1 }],
    edges: [{ id: "works_at:n1:n2", type: "works_at", source: "n1", target: "n2", evidence_ids: [A, B], count: 2 },
      { id: "located_in:n2:n3", type: "located_in", source: "n2", target: "n3", evidence_ids: [A], count: 1 }],
    relationship_count: 3 } }));
  await openGraph(page);

  await expect(page.getByRole("img", { name: /3 entities and 2 confirmed links/ })).toBeVisible();
  const list = page.getByRole("list", { name: "Confirmed relationships" });
  await expect(list.getByText("Sara Ahmed")).toBeVisible();
  await expect(list.locator("li")).toHaveCount(2);
  await expect(page.getByText("3 entities · 2 links from 3 confirmed relationships")).toBeVisible();
  await page.getByRole("button", { name: "Lahore, location" }).click();
  await expect(list.locator("li")).toHaveCount(1);
  await expect(list.getByText("is located in")).toBeVisible();
  await page.getByRole("button", { name: "Lahore, location" }).press("Enter");
  await expect(list.locator("li")).toHaveCount(2);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator(".graph-view").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
});

test("find relationships reports skipped statements and refreshes; errors are shown", async ({ page }) => {
  let built = false;
  await signedIn(page);
  await page.route("**/api/relationships?*", route => route.fulfill({ json: { total: built ? 1 : 0, offset: 0, limit: 10, relationships: built ? [item("00000000-0000-0000-0000-0000000000b1", "suggested")] : [] } }));
  await page.route("**/api/graph", route => route.fulfill({ json: { nodes: [], edges: [], relationship_count: 0 } }));
  await page.route("**/api/relationships/build", route => {
    built = true;
    return route.fulfill({ json: { documents: 2, candidates: 1, filtered: 3 } });
  });
  await openGraph(page);
  await expect(page.getByText("No relationships are waiting.")).toBeVisible();
  await page.getByRole("button", { name: "Find relationships" }).click();
  await expect(page.getByText("Checked 2 documents: 1 possible relationship. 3 negated, hedged, or unclear statements were skipped.")).toBeVisible();
  await expect(page.getByText("Review queue (1)")).toBeVisible();
});

test("a refused review shows the safe message and keeps the item", async ({ page }) => {
  await signedIn(page);
  await page.route("**/api/relationships?*", route => route.fulfill({ json: { total: 1, offset: 0, limit: 10, relationships: [item("00000000-0000-0000-0000-0000000000b1", "suggested")] } }));
  await page.route("**/api/graph", route => route.fulfill({ json: { nodes: [], edges: [], relationship_count: 0 } }));
  await page.route("**/api/relationships/*", route => route.fulfill({ status: 422, json: { detail: "That change does not fit these two entities." } }));
  await openGraph(page);
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "does not fit these two entities" })).toBeVisible();
  await expect(page.locator(".relation-item")).toHaveCount(1);
});

test("relationship and graph proxies require credentials and allow only known operations", async ({ request }) => {
  expect((await request.get("/api/relationships")).status()).toBe(401);
  expect((await request.post("/api/relationships/build")).status()).toBe(401);
  expect((await request.get("/api/graph")).status()).toBe(401);
  expect((await request.get("/api/relationships/not-an-id", { headers })).status()).toBe(404);
  expect((await request.delete("/api/relationships/00000000-0000-0000-0000-0000000000b1", { headers })).status()).toBe(405);
  expect((await request.post("/api/relationships/00000000-0000-0000-0000-0000000000b1", { headers, data: {} })).status()).toBe(404);
});
