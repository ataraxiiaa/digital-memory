import { test, expect, type Page } from "@playwright/test";

const user = { id: "00000000-0000-0000-0000-000000000001", email: "alex@example.test", role: "authenticated", aud: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-11-13T10:00:00Z" };
const headers = { Authorization: "Bearer fixture-access-token" };

async function mockAuth(page: Page) {
  await page.route("https://auth-fixture.supabase.co/auth/v1/**", async route => {
    if (route.request().url().includes("/logout")) { await route.fulfill({ status: 204 }); return; }
    await route.fulfill({ json: { access_token: "fixture-access-token", refresh_token: "fixture-refresh-token", expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, token_type: "bearer", user } });
  });
  await page.route("**/api/auth/me", route => route.fulfill({ json: user }));
}

async function signIn(page: Page) {
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill("alex@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fictional-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

test("a typed note is saved exactly, prepared, and reviewed with entities", async ({ page }) => {
  await mockAuth(page);
  const id = "00000000-0000-0000-0000-000000000050";
  const runId = "00000000-0000-0000-0000-000000000060";
  const text = "Sara met Zoë in Lahore on 3 March 2024.\n\nÉmile’s 日本語 note stays exact.";
  let row: Record<string, unknown> | null = null;
  let saved: { title: string; collection: string; document_date: string | null; content: string } | null = null;
  let prepared = 0;
  const mentions = [
    { id: "00000000-0000-0000-0000-000000000071", text: "Sara", label: "person", start: text.indexOf("Sara") },
    { id: "00000000-0000-0000-0000-000000000072", text: "Lahore", label: "location", start: text.indexOf("Lahore") },
    { id: "00000000-0000-0000-0000-000000000073", text: "3 March 2024", label: "date", start: text.indexOf("3 March 2024") },
  ].map(item => ({ id: item.id, run_id: runId, page_number: 1, label: item.label, text: item.text, score: 0.9,
    citation: { document_id: id, extraction_id: "e1", page_number: 1, region_id: "r1", start_char: item.start, end_char: item.start + item.text.length },
    warnings: [] as string[], review_status: "suggested", reviewed_label: null as string | null }));
  await page.route("**/api/documents?*", route => route.fulfill({ json: row ? [row] : [] }));
  await page.route("**/api/documents", async route => {
    expect(route.request().method()).toBe("POST");
    saved = route.request().postDataJSON();
    row = { id, title: saved!.title, document_date: saved!.document_date, collection: saved!.collection, created_at: "2026-11-18T10:00:00Z",
      status: "ready", synthetic: false, content: saved!.content, original_filename: null, file_size_bytes: null, page_count: null,
      processing_run_id: null, processing_started_at: null, processing_error: null };
    await route.fulfill({ status: 201, json: row });
  });
  await page.route(`**/api/documents/${id}`, route => route.fulfill({ json: row }));
  await page.route(`**/api/documents/${id}/process-text`, async route => {
    expect(route.request().method()).toBe("POST");
    prepared += 1;
    row = { ...row!, processing_run_id: runId };
    await route.fulfill({ json: row });
  });
  await page.route(`**/api/documents/${id}/extraction`, route => route.fulfill({ json: {
    native_extraction: { id: "e1", pages: [{ page_number: 1, warnings: [], regions: [{ id: "r1", text }] }] },
    ocr_extraction: null, pages: [], tables: [], warnings: [] } }));
  await page.route(`**/api/documents/${id}/entities?*`, route => {
    const query = new URL(route.request().url()).searchParams;
    const shown = mentions.filter(item => !query.get("status") || item.review_status === query.get("status"));
    const count = (name: string) => mentions.filter(item => item.review_status === name).length;
    return route.fulfill({ json: { run: { id: runId, status: "completed", label_schema_version: "full-v1", threshold: 0.5, failed_passage_count: 0, rejected_candidate_count: 0, error: null },
      counts: { total: 3, suggested: count("suggested"), accepted: count("accepted"), rejected: count("rejected"), by_label: { person: 1, location: 1, date: 1 } },
      total: shown.length, offset: 0, limit: 25, mentions: shown } });
  });
  await page.route(`**/api/documents/${id}/entities/${runId}/*`, async route => {
    const body = route.request().postDataJSON();
    const item = mentions.find(candidate => route.request().url().endsWith(candidate.id))!;
    item.review_status = body.status; item.reviewed_label = body.label;
    await route.fulfill({ json: item });
  });

  await signIn(page);
  const form = page.getByRole("form", { name: "New note" });
  await expect(form.getByRole("button", { name: "Save note" })).toBeDisabled();
  await form.getByLabel("Title", { exact: true }).fill("  Trip note  ");
  await form.getByLabel("Date of the note (optional)").fill("2026-03-03");
  await form.getByLabel("Note", { exact: true }).fill(text);
  await expect(form.getByText(`${text.length} / 200,000 characters`)).toBeVisible();
  await form.getByRole("button", { name: "Save note" }).click();
  await expect(page.getByRole("status").filter({ hasText: "“Trip note” was saved" })).toBeVisible();
  // Exactly what was typed reaches the server; only the title label is trimmed.
  expect(saved).toEqual({ title: "Trip note", collection: "Notes", document_date: "2026-03-03", content: text });
  expect(prepared).toBe(1);
  await expect(form.getByLabel("Note", { exact: true })).toHaveValue("");

  await page.getByRole("button", { name: /Trip note.*Notes/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".note-text")).toHaveText(text);
  const panel = dialog.getByLabel("Entity review");
  await expect(panel.getByText("3 suggested · 0 accepted · 0 rejected · 3 awaiting review")).toBeVisible();
  await panel.locator(".entity-item", { hasText: "Lahore" }).getByRole("button", { name: "Show source" }).click();
  await expect(panel.locator(".entity-source mark")).toHaveText("Lahore");
  // The opened source passage also contains "Sara", so match the mention text itself.
  await panel.locator(".entity-item").filter({ has: page.locator(".entity-text", { hasText: /^Sara$/ }) }).getByRole("button", { name: "Accept", exact: true }).click();
  await expect(panel.locator(".entity-item")).toHaveCount(2);
  await page.screenshot({ path: "test-results/note-entities.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
});

test("a saved note that was never prepared offers a retry, and over-limit text is blocked", async ({ page }) => {
  await mockAuth(page);
  const id = "00000000-0000-0000-0000-000000000051";
  const runId = "00000000-0000-0000-0000-000000000061";
  let row = { id, title: "Old note", document_date: null, collection: "Notes", created_at: "2026-11-01T10:00:00Z", status: "ready", synthetic: false,
    content: "Brian moved to Dubai in 2020.", original_filename: null, file_size_bytes: null, page_count: null,
    processing_run_id: null as string | null, processing_started_at: null, processing_error: null };
  let attempts = 0;
  await page.route("**/api/documents?*", route => route.fulfill({ json: [row] }));
  await page.route(`**/api/documents/${id}`, route => route.fulfill({ json: row }));
  await page.route(`**/api/documents/${id}/process-text`, async route => {
    attempts += 1;
    if (attempts === 1) { await route.fulfill({ status: 503, json: { detail: "Could not prepare the note for entity extraction. Try again." } }); return; }
    row = { ...row, processing_run_id: runId };
    await route.fulfill({ json: row });
  });
  await page.route(`**/api/documents/${id}/entities?*`, route => route.fulfill({ json: { run: null, counts: { total: 0, suggested: 0, accepted: 0, rejected: 0, by_label: {} }, total: 0, offset: 0, limit: 25, mentions: [] } }));
  await signIn(page);
  await page.getByRole("button", { name: /Old note.*Notes/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("This note has not been prepared for entity extraction yet.")).toBeVisible();
  await dialog.getByRole("button", { name: "Prepare note" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Could not prepare the note");
  await dialog.getByRole("button", { name: "Prepare note" }).click();
  await expect(dialog.getByRole("button", { name: "Find entities" })).toBeVisible();
  await page.getByRole("button", { name: "Close document" }).click();
  const form = page.getByRole("form", { name: "New note" });
  await form.getByLabel("Title", { exact: true }).fill("Huge");
  await form.getByLabel("Note", { exact: true }).fill("x".repeat(200001));
  await expect(form.getByRole("button", { name: "Save note" })).toBeDisabled();
  await expect(form.getByText("200,001 / 200,000 characters")).toBeVisible();
});

test("note proxies forward the exact JSON body, prepare a note, and reject bad input", async ({ request }) => {
  const created = await request.post("/api/documents", { headers, data: { title: "Proxy note", content: "Sara met Zoë.\r\nOK", collection: "Notes", document_date: null } });
  expect(created.status()).toBe(201);
  expect((await created.json()).received.content).toBe("Sara met Zoë.\r\nOK");
  expect((await request.post("/api/documents", { data: { title: "x", content: "y" } })).status()).toBe(401);
  const tooBig = await request.post("/api/documents", { headers, data: { title: "x", content: "y".repeat(1_600_000) } });
  expect(tooBig.status()).toBe(413);
  const base = "/api/documents/00000000-0000-0000-0000-000000000010/process-text";
  expect((await request.post(base)).status()).toBe(401);
  expect((await request.post(base, { headers })).status()).toBe(200);
  expect((await request.post("/api/documents/not-an-id/process-text", { headers })).status()).toBe(404);
});
