import { test, expect, type Page } from "@playwright/test";

const user = { id: "00000000-0000-0000-0000-000000000001", email: "alex@example.test", role: "authenticated", aud: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-11-13T10:00:00Z" };
const headers = { Authorization: "Bearer fixture-access-token" };
const DOC = "00000000-0000-0000-0000-000000000010";
const SENTENCE = "Omar's wedding is on 21 December in Murree.";

const ref = (role: string, label: string, text: string) => {
  const start = SENTENCE.indexOf(text);
  return { run_id: "00000000-0000-0000-0000-000000000020", mention_id: `00000000-0000-0000-0000-0000000001${start.toString().padStart(2, "0")}`,
    role, label, text, citation: { start_char: start, end_char: start + text.length, page_number: 1 } };
};
const base = {
  document_id: DOC, document_title: "Family notes", page_number: 1, sentence_text: SENTENCE, sentence_start: 0,
  sentence_end: SENTENCE.length, review_status: "suggested", corrected_title: null, corrected_start: null,
  corrected_end: null, corrected_precision: null, stale: false,
};
const dated = { ...base, id: "00000000-0000-0000-0000-0000000000a1", title: "wedding", date_text: "21 December", date_status: "resolved",
  start_date: "2026-12-21", end_date: "2026-12-21", precision: "day", flags: ["year_inferred"],
  refs: [ref("event", "event", "wedding"), ref("date", "date", "21 December"), ref("location", "location", "Murree"), ref("participant", "person", "Omar")] };
const undated = { ...base, id: "00000000-0000-0000-0000-0000000000a2", title: "board meeting", date_text: null, date_status: "none",
  start_date: null, end_date: null, precision: null, flags: [], refs: [ref("event", "event", "wedding")] };

async function openTimeline(page: Page, rows: { dated: typeof dated[]; undated: typeof undated[] }, reviews: unknown[] = []) {
  await page.route("**/api/documents?*", route => route.fulfill({ json: [] }));
  await page.route("https://auth-fixture.supabase.co/auth/v1/**", route => route.fulfill({ json: { access_token: "fixture-access-token", refresh_token: "r", expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, token_type: "bearer", user } }));
  await page.route("**/api/auth/me", route => route.fulfill({ json: user }));
  await page.route(/\/api\/timeline\?/, route => {
    const query = new URL(route.request().url()).searchParams;
    const items = query.get("dated") === "true" ? rows.dated : rows.undated;
    return route.fulfill({ json: { total: items.length, offset: 0, limit: 50, undated: rows.undated.length, items } });
  });
  await page.route(/\/api\/timeline\/build$/, route => route.fulfill({ json: { documents: 2, items: 2, undated: 1, unattached_dates: 3 } }));
  await page.route(/\/api\/timeline\/[0-9a-f-]{36}$/, async route => {
    expect(route.request().method()).toBe("PATCH");
    const body = route.request().postDataJSON();
    reviews.push(body);
    const item = [...rows.dated, ...rows.undated].find(candidate => route.request().url().endsWith(candidate.id))!;
    item.review_status = body.status;
    await route.fulfill({ json: item });
  });
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill("alex@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fictional-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: /Timeline/ }).click();
}

test("timeline shows dated events by year, explains uncertainty and shows the exact source", async ({ page }) => {
  await openTimeline(page, { dated: [structuredClone(dated)], undated: [structuredClone(undated)] });
  await expect(page.getByRole("heading", { name: "2026" })).toBeVisible();
  const card = page.locator(".timeline-card", { hasText: "wedding" }).first();
  await expect(card.locator(".timeline-date")).toHaveText("21 Dec 2026");
  await expect(card.getByText("Year assumed from the document date")).toBeVisible();
  await expect(card.getByText("Murree")).toBeVisible();
  await card.getByRole("button", { name: "Show source" }).click();
  await expect(card.locator(".timeline-source mark[data-role=event]")).toHaveText("wedding");
  await expect(card.locator(".timeline-source mark[data-role=date]")).toHaveText("21 December");
  await expect(page.getByRole("heading", { name: "Needs a date (1)" })).toBeVisible();
  await expect(page.getByText("No date in this sentence")).toBeVisible();
});

test("timeline confirms, corrects a date, rejects, and updates from reviewed entities", async ({ page }) => {
  const reviews: unknown[] = [];
  await openTimeline(page, { dated: [structuredClone(dated)], undated: [structuredClone(undated)] }, reviews);
  const card = page.locator(".timeline-card", { hasText: "wedding" }).first();
  await card.getByRole("button", { name: "Confirm" }).click();
  await card.getByRole("button", { name: "Edit" }).click();
  await card.getByLabel("Title").fill("Omar's wedding");
  await card.getByLabel("Starts").fill("2027-12-21");
  await card.getByLabel("Ends").fill("2027-12-22");
  await card.getByLabel("Precision").selectOption("day");
  await card.getByRole("button", { name: "Save and confirm" }).click();
  await page.locator(".timeline-card", { hasText: "board meeting" }).getByRole("button", { name: "Reject" }).click();
  await expect.poll(() => reviews).toEqual([
    { status: "confirmed" },
    { status: "confirmed", title: "Omar's wedding", start: "2027-12-21", end: "2027-12-22", precision: "day" },
    { status: "rejected" }]);
  await page.getByRole("button", { name: "Update from reviewed entities" }).click();
  await expect(page.getByText("Updated 2 documents: 2 timeline suggestions, 1 still need a date. 3 accepted dates have no event in the same sentence.")).toBeVisible();
});

test("empty timeline explains how to build one, and narrow screens do not overflow", async ({ page }) => {
  await openTimeline(page, { dated: [], undated: [] });
  await expect(page.getByText("The timeline is built from entities you have accepted.")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("timeline with items fits a narrow screen", async ({ page }) => {
  await openTimeline(page, { dated: [structuredClone(dated)], undated: [structuredClone(undated)] });
  await expect(page.locator(".timeline-card").first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("timeline proxy requires credentials, allowlists operations and forwards filters", async ({ request }) => {
  expect((await request.get("/api/timeline")).status()).toBe(401);
  expect((await request.post("/api/timeline/build")).status()).toBe(401);
  expect((await request.get("/api/timeline/not-an-id", { headers })).status()).toBe(404);
  expect((await request.delete("/api/timeline", { headers })).status()).toBe(405);
  const listed = await request.get("/api/timeline?dated=true&limit=5&evil=1&after=2026-01-01", { headers });
  expect(listed.status()).toBe(200);
  expect((await listed.json()).query).toBe("limit=5&after=2026-01-01&dated=true");
  expect((await request.post("/api/timeline/build", { headers })).status()).toBe(200);
  const patched = await request.patch("/api/timeline/00000000-0000-0000-0000-0000000000a1", { headers, data: { status: "confirmed" } });
  expect((await patched.json()).received).toEqual({ status: "confirmed" });
});
