// NOT EXECUTED by the Phase 2 author: ports 3100/3199 were reserved for another agent's run.
import { test, expect, type Page } from "@playwright/test";

const user = { id: "00000000-0000-0000-0000-000000000001", email: "alex@example.test", role: "authenticated", aud: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-11-13T10:00:00Z" };
const headers = { Authorization: "Bearer fixture-access-token" };
const A = "00000000-0000-0000-0000-0000000000a1", B = "00000000-0000-0000-0000-0000000000a2";
const DOC = "00000000-0000-0000-0000-000000000010", RUN = "00000000-0000-0000-0000-000000000020";
const evidence = (id: string, text: string) => ({ run_id: RUN, mention_id: id, document_id: DOC, page_number: 2, text });

async function signedIn(page: Page) {
  await page.route("**/api/documents?*", route => route.fulfill({ json: [{ id: DOC, title: "Founders notes", collection: "Work", created_at: "2026-11-13T12:00:00Z", status: "ready" }] }));
  await page.route("https://auth-fixture.supabase.co/auth/v1/**", route => route.fulfill({ json: { access_token: "fixture-access-token", refresh_token: "r", expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, token_type: "bearer", user } }));
  await page.route("**/api/auth/me", route => route.fulfill({ json: user }));
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill("alex@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fictional-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: /Entities/ }).click();
}

test("entities view lists merge suggestions with reasons and evidence, and merges on confirmation", async ({ page }) => {
  let merged = false;
  const posts: string[] = [];
  await signedIn(page);
  const entity = (id: string, name: string, count: number) => ({ id, type: "person", canonical_name: name, status: "suggested", mention_count: count, document_count: 1, aliases: [name] });
  await page.route("**/api/entities?*", route => route.fulfill({ json: { total: merged ? 1 : 2, offset: 0, limit: 30, entities: merged ? [entity(A, "Larry Page", 3)] : [entity(A, "Larry Page", 2), entity(B, "L. Page", 1)] } }));
  await page.route("**/api/entities/suggestions?*", route => route.fulfill({ json: { total: merged ? 0 : 1, offset: 0, limit: 20, suggestions: merged ? [] : [{
    id: "00000000-0000-0000-0000-0000000000b1", kind: "initial", score: 0.85, ambiguous: false,
    reason: "“L. Page” may be “Larry Page” (first name abbreviated to an initial)",
    a: { ...entity(A, "Larry Page", 2), evidence: [evidence("m1", "Larry Page founded Google")] },
    b: { ...entity(B, "L. Page", 1), evidence: [evidence("m2", "L. Page spoke")] } }] } }));
  await page.route("**/api/entities/suggestions/*/confirm", async route => {
    posts.push(route.request().postData() ?? "");
    merged = true;
    await route.fulfill({ json: { entity: entity(A, "Larry Page", 3), mentions: [], merges: [] } });
  });

  await expect(page.getByText("Possible merges (1)")).toBeVisible();
  await expect(page.getByText("may be “Larry Page”")).toBeVisible();
  await expect(page.getByText("Larry Page founded Google")).toBeVisible();
  await expect(page.getByText("Founders notes, page 2").first()).toBeVisible();
  await page.getByRole("button", { name: "Merge into “Larry Page”" }).click();
  await expect(page.getByText("Merged into “Larry Page”")).toBeVisible();
  expect(JSON.parse(posts[0])).toEqual({ keep_id: A });
  await expect(page.getByText("No merges to review.")).toBeVisible();
});

test("ambiguous and weak suggestions are labelled and can be dismissed", async ({ page }) => {
  await signedIn(page);
  let dismissed = false;
  const side = (id: string, name: string) => ({ id, type: "person", canonical_name: name, status: "suggested", mention_count: 1, aliases: [name], evidence: [] });
  await page.route("**/api/entities?*", route => route.fulfill({ json: { total: 0, offset: 0, limit: 30, entities: [] } }));
  await page.route("**/api/entities/suggestions?*", route => route.fulfill({ json: { total: dismissed ? 0 : 1, offset: 0, limit: 20, suggestions: dismissed ? [] : [{
    id: "00000000-0000-0000-0000-0000000000b2", kind: "surname", score: 0.4, ambiguous: true,
    reason: "Page is only a surname. Ambiguous: it also fits “Lisa Page”", a: side(A, "Page"), b: side(B, "Larry Page") }] } }));
  await page.route("**/api/entities/suggestions/*/dismiss", route => { dismissed = true; return route.fulfill({ status: 204 }); });
  await expect(page.getByText("Ambiguous", { exact: true })).toBeVisible();
  await expect(page.getByText("Surname only (weak) · 40%")).toBeVisible();
  await page.getByRole("button", { name: "Not the same" }).click();
  await expect(page.getByText("No merges to review.")).toBeVisible();
});

test("entity detail renames, splits a mention, and undoes a merge", async ({ page }) => {
  await signedIn(page);
  const calls: string[] = [];
  const base = { id: A, type: "person", canonical_name: "Larry Page", status: "confirmed", mention_count: 2, document_count: 1, aliases: ["Larry Page", "L. Page"] };
  await page.route("**/api/entities?*", route => route.fulfill({ json: { total: 1, offset: 0, limit: 30, entities: [base] } }));
  await page.route("**/api/entities/suggestions?*", route => route.fulfill({ json: { total: 0, offset: 0, limit: 20, suggestions: [] } }));
  await page.route(`**/api/entities/${A}`, route => {
    if (route.request().method() === "PATCH") calls.push("rename:" + route.request().postData());
    return route.fulfill({ json: { entity: base, mentions: [evidence("m1", "Larry Page"), evidence("m2", "L. Page")],
      merges: [{ id: "00000000-0000-0000-0000-0000000000c1", absorbed_name: "L. Page", merged_at: "2026-11-18T00:00:00Z", undone_at: null }] } });
  });
  await page.route("**/api/entities/mentions/split", route => { calls.push("split"); return route.fulfill({ status: 201, json: { entity: base, mentions: [], merges: [] } }); });
  await page.route("**/api/entities/merges/*/undo", route => { calls.push("undo"); return route.fulfill({ status: 204 }); });

  await page.getByRole("button", { name: "Larry Page", exact: true }).click();
  await page.getByLabel("Name").fill("Larry E. Page");
  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await page.getByRole("button", { name: "Split off" }).first().click();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect.poll(() => calls).toEqual([`rename:${JSON.stringify({ name: "Larry E. Page" })}`, "split", "undo"]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator(".entity-library").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
});

test("entity proxy requires credentials and forwards only allowlisted operations", async ({ request }) => {
  expect((await request.get("/api/entities")).status()).toBe(401);
  expect((await request.post("/api/entities/resolve")).status()).toBe(401);
  expect((await request.get("/api/entities/not-an-id", { headers })).status()).toBe(404);
  expect((await request.delete(`/api/entities/${A}`, { headers })).status()).toBe(405);
  expect((await request.post("/api/entities/suggestions/x/confirm", { headers, data: {} })).status()).toBe(404);
  expect((await request.get("/api/entities/resolve", { headers })).status()).toBe(404);
});
