import { test, expect, type Page } from "@playwright/test";
import { pdfFixture } from "./pdf-fixture.mjs";

async function fillSignup(page: Page, confirmation = "fictional-password") {
  await page.getByLabel("Email address").fill("alex@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fictional-password");
  await page.getByLabel("Confirm password").fill(confirmation);
  await page.getByRole("button", { name: "Create account", exact: true }).click();
}

const user = { id: "00000000-0000-0000-0000-000000000001", email: "alex@example.test", role: "authenticated", aud: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-11-13T10:00:00Z" };

async function mockAuth(page: Page, options: { badPassword?: boolean; backendStatus?: number } = {}) {
  await page.route("**/api/documents/*/file", route => route.fulfill({ contentType: "application/pdf", body: pdfFixture() }));
  await page.route("**/api/documents?*", route => route.fulfill({ json: [] }));
  await page.route("https://auth-fixture.supabase.co/auth/v1/**", async route => {
    if (route.request().url().includes("/logout")) { await route.fulfill({ status: 204 }); return; }
    if (options.badPassword) { await route.fulfill({ status: 400, json: { error_code: "invalid_credentials", msg: "Invalid login credentials" } }); return; }
    await route.fulfill({ json: { access_token: "fixture-access-token", refresh_token: "fixture-refresh-token", expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, token_type: "bearer", user } });
  });
  await page.route("**/api/auth/me", async route => {
    expect(route.request().headers()["authorization"]).toBe("Bearer fixture-access-token");
    await route.fulfill({ status: options.backendStatus ?? 200, json: options.backendStatus ? { detail: "unavailable" } : user });
  });
}

async function signIn(page: Page) {
  await page.getByLabel("Email address").fill("alex@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fictional-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

test("unauthenticated workspace redirects without displaying sample documents", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in$/);
  await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
  await expect(page.getByText("Recent memories")).toHaveCount(0);
  await page.screenshot({ path: "test-results/sign-in-desktop.png", fullPage: true });
});

test("bad credentials show an error and keep the user out", async ({ page }) => {
  await mockAuth(page, { badPassword: true });
  await page.goto("/sign-in");
  await signIn(page);
  await expect(page.getByRole("alert").filter({ hasText: "Check your email and password" })).toBeVisible();
  await expect(page).toHaveURL(/\/sign-in$/);
});

test("verified session opens workspace, survives reload, and signs out", async ({ page }) => {
  await mockAuth(page);
  await page.goto("/sign-in");
  await signIn(page);
  await expect(page.getByRole("heading", { name: "Make room for remembering." })).toBeVisible();
  await expect(page.getByText("alex@example.test", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "A fresh start." })).toBeVisible();
  await expect(page.getByText("Alex’s workspace")).toHaveCount(0);
  await expect(page.getByText("Studio project")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Make room for remembering." })).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in$/);
});

test("backend outage blocks workspace and supports retry", async ({ page }) => {
  await mockAuth(page, { backendStatus: 503 });
  await page.goto("/sign-in");
  await signIn(page);
  await expect(page.getByRole("alert").filter({ hasText: "couldn’t verify your session" })).toBeVisible();
  await expect(page.getByText("Recent memories")).toHaveCount(0);
  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me", route => route.fulfill({ json: user }));
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "Make room for remembering." })).toBeVisible();
});

test("rejected session returns to sign-in", async ({ page }) => {
  await mockAuth(page, { backendStatus: 401 });
  await page.goto("/sign-in");
  await signIn(page);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByText("Recent memories")).toHaveCount(0);
});

test("sign-in form fits a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/sign-in");
  await expect(page.getByLabel("Email address")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/sign-in-mobile.png", fullPage: true });
});

test("sign-in links to signup and mismatched passwords never reach Supabase", async ({ page }) => {
  let requests = 0;
  await page.route("https://auth-fixture.supabase.co/auth/v1/**", route => { requests++; return route.abort(); });
  await page.goto("/sign-in");
  await page.getByRole("link", { name: "Create an account" }).click();
  await expect(page).toHaveURL(/\/sign-up$/);
  await fillSignup(page, "different-password");
  await expect(page.getByRole("alert").filter({ hasText: "Passwords don’t match" })).toBeVisible();
  expect(requests).toBe(0);
});

test("signup requiring email confirmation explains the next step without opening workspace", async ({ page }) => {
  await page.route("https://auth-fixture.supabase.co/auth/v1/signup**", async route => {
    const payload = route.request().postDataJSON();
    expect(payload.email).toBe("alex@example.test");
    expect(payload.password).toBe("fictional-password");
    expect(route.request().url()).toContain("redirect_to=");
    await route.fulfill({ json: { ...user, identities: [] } });
  });
  await page.goto("/sign-up");
  await fillSignup(page);
  await expect(page.getByRole("heading", { name: "Check your email." })).toBeVisible();
  await expect(page.getByText("Follow the confirmation link", { exact: false })).toBeVisible();
  await expect(page.getByRole("heading", { name: "A fresh start." })).toHaveCount(0);
  await page.screenshot({ path: "test-results/signup-confirmation.png", fullPage: true });
  await page.getByRole("link", { name: "Back to sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
});

test("signup with automatic confirmation verifies the session and opens empty workspace", async ({ page }) => {
  await mockAuth(page);
  await page.goto("/sign-up");
  await fillSignup(page);
  await expect(page.getByRole("heading", { name: "A fresh start." })).toBeVisible();
  await expect(page).toHaveURL("/");
});

test("signup reports rate limiting without showing provider details", async ({ page }) => {
  await page.route("https://auth-fixture.supabase.co/auth/v1/signup**", route => route.fulfill({ status: 429, json: { msg: "private provider error", error_code: "over_email_send_rate_limit" } }));
  await page.goto("/sign-up");
  await fillSignup(page);
  await expect(page.getByRole("alert").filter({ hasText: "confirmation-email limit" })).toBeVisible();
  await expect(page.getByText("private provider error")).toHaveCount(0);
});

test("signup fits a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/sign-up");
  await expect(page.getByRole("heading", { name: "Start your collection." })).toBeVisible();
  await expect(page.getByLabel("Confirm password")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/signup-mobile.png", fullPage: true });
});

for (const scenario of [
  { code: "email_address_not_authorized", status: 403, message: "configure an email provider" },
  { code: "signup_disabled", status: 403, message: "New account creation is disabled" },
  { code: "email_address_invalid", status: 422, message: "Use a real email address" },
  { code: "weak_password", status: 422, message: "Choose a stronger password" },
  { code: "captcha_failed", status: 422, message: "security check" },
  { code: "unexpected_failure", status: 500, message: "account service couldn’t finish signup" },
  { code: "user_already_exists", status: 422, message: "Check your details, or try signing in" },
]) {
  test(`signup explains ${scenario.code} without leaking upstream details`, async ({ page }) => {
    await page.route("https://auth-fixture.supabase.co/auth/v1/signup**", route => route.fulfill({ status: scenario.status, json: { code: scenario.code, error_code: scenario.code, msg: "private provider detail" } }));
    await page.goto("/sign-up");
    await fillSignup(page);
    await expect(page.getByRole("alert").filter({ hasText: scenario.message })).toBeVisible();
    await expect(page.getByText("private provider detail")).toHaveCount(0);
  });
}

const documentRow = { id: "00000000-0000-0000-0000-000000000010", title: "Project notes", document_date: null, collection: "Work", created_at: "2026-11-13T12:00:00Z", status: "uploaded" };

test("upload PDF refreshes the owned list and opens file metadata", async ({ page }) => {
  await mockAuth(page);
  let uploaded = false;
  await page.route("**/api/documents?*", route => route.fulfill({ json: uploaded ? [documentRow] : [] }));
  await page.route("**/api/documents/upload", async route => {
    expect(route.request().headers()["authorization"]).toBe("Bearer fixture-access-token");
    expect(route.request().headers()["content-type"]).toContain("multipart/form-data");
    expect(route.request().postDataBuffer()?.toString()).toContain("notes.pdf");
    uploaded = true;
    await route.fulfill({ status: 201, json: { ...documentRow, content: "" } });
  });
  await page.route(`**/api/documents/${documentRow.id}`, route => route.fulfill({ json: { ...documentRow, content: "", original_filename: "notes.pdf", page_count: 3, file_size_bytes: 2000 } }));
  await page.goto("/sign-in"); await signIn(page);
  await expect(page.getByRole("heading", { name: "A fresh start." })).toBeVisible();
  await page.getByLabel("Choose a PDF").setInputFiles({ name: "notes.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-fixture") });
  await page.getByLabel("Document title").fill("Project notes");
  await page.getByLabel("Collection (optional)").fill("Work");
  await page.getByRole("button", { name: "Upload PDF", exact: true }).click();
  await expect(page.getByText("“Project notes” was uploaded.", { exact: false })).toBeVisible();
  await expect(page.getByText("Uploaded", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Project notes.*Work/ }).click();
  await expect(page.getByRole("dialog")).toContainText("3 pages");
  await expect(page.getByRole("dialog")).toContainText("Your PDF is stored");
  await expect(page.locator('iframe[title="PDF preview: notes.pdf"]')).toHaveAttribute("src", /^blob:/);
  await expect(page.getByRole("link", { name: "Download PDF" })).toHaveAttribute("download", "notes.pdf");
  await page.screenshot({ path: "test-results/pdf-viewer.png", fullPage: true });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.screenshot({ path: "test-results/document-library.png", fullPage: true });
});

test("non-PDF and oversized files are rejected before upload", async ({ page }) => {
  await mockAuth(page);
  let uploads = 0;
  await page.route("**/api/documents/upload", route => { uploads++; return route.abort(); });
  await page.goto("/sign-in"); await signIn(page);
  await page.getByLabel("Choose a PDF").setInputFiles({ name: "note.txt", mimeType: "text/plain", buffer: Buffer.from("text") });
  await expect(page.getByRole("alert").filter({ hasText: "Choose a PDF file" })).toBeVisible();
  await page.getByLabel("Choose a PDF").setInputFiles({ name: "large.pdf", mimeType: "application/pdf", buffer: Buffer.alloc(30 * 1024 * 1024 + 1) });
  await expect(page.getByRole("alert").filter({ hasText: "must not exceed 30 MiB" })).toBeVisible();
  expect(uploads).toBe(0);
  await expect(page.getByRole("button", { name: "Upload PDF", exact: true })).toBeDisabled();
});

test("damaged PDF error preserves the form and allows another attempt", async ({ page }) => {
  await mockAuth(page);
  await page.route("**/api/documents/upload", route => route.fulfill({ status: 422, json: { detail: "The PDF is damaged or cannot be opened" } }));
  await page.goto("/sign-in"); await signIn(page);
  await page.getByLabel("Choose a PDF").setInputFiles({ name: "bad.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-broken") });
  await page.getByRole("button", { name: "Upload PDF", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "PDF is damaged" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload PDF", exact: true })).toBeEnabled();
  await expect(page.getByLabel("Document title")).toHaveValue("bad");
});

test("list errors support retry without inventing document counts", async ({ page }) => {
  await mockAuth(page);
  let retry = false;
  await page.route("**/api/documents?*", route => route.fulfill({ status: retry ? 200 : 503, json: retry ? [] : { detail: "Unavailable" } }));
  await page.goto("/sign-in"); await signIn(page);
  await expect(page.getByRole("alert").filter({ hasText: "Couldn’t load your documents" })).toBeVisible();
  retry = true;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "A fresh start." })).toBeVisible();
});

test("pagination requests the next page and returns to the first", async ({ page }) => {
  await mockAuth(page);
  const rows = Array.from({ length: 20 }, (_, index) => ({ ...documentRow, id: `00000000-0000-0000-0000-${String(index).padStart(12, "0")}`, title: `Note ${index}` }));
  await page.route("**/api/documents?*", route => route.fulfill({ json: new URL(route.request().url()).searchParams.get("offset") === "0" ? rows : [] }));
  await page.goto("/sign-in"); await signIn(page);
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("heading", { name: "No more documents." })).toBeVisible();
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(page.getByRole("button", { name: /Note 0.*Work/ })).toBeVisible();
});

test("upload and library fit a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAuth(page);
  await page.goto("/sign-in"); await signIn(page);
  await expect(page.getByRole("heading", { name: "Your documents" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/document-upload-mobile.png", fullPage: true });
});

test("document proxy endpoints reject missing credentials", async ({ request }) => {
  expect((await request.get("/api/documents")).status()).toBe(401);
  expect((await request.post("/api/documents/upload", { multipart: { file: { name: "a.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF") } } })).status()).toBe(401);
  expect((await request.get(`/api/documents/${documentRow.id}`)).status()).toBe(401);
});

test("PDF viewer supports retry and releases its temporary URL on close", async ({ page }) => {
  await page.addInitScript(() => {
    const original = URL.revokeObjectURL;
    (window as unknown as { revoked: string[] }).revoked = [];
    URL.revokeObjectURL = value => {
      (window as unknown as { revoked: string[] }).revoked.push(value);
      original.call(URL, value);
    };
  });
  await mockAuth(page);
  await page.route("**/api/documents?*", route => route.fulfill({ json: [documentRow] }));
  await page.route(`**/api/documents/${documentRow.id}`, route => route.fulfill({ json: { ...documentRow, original_filename: "notes.pdf", page_count: 1, file_size_bytes: pdfFixture().length, content: "" } }));
  let fail = true;
  await page.route(`**/api/documents/${documentRow.id}/file`, async route => {
    expect(route.request().headers()["authorization"]).toBe("Bearer fixture-access-token");
    if (fail) await route.fulfill({ status: 503, json: { detail: "Unavailable" } });
    else await route.fulfill({ contentType: "application/pdf", body: pdfFixture() });
  });
  await page.goto("/sign-in"); await signIn(page);
  await page.getByRole("button", { name: /Project notes.*Work/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Couldn’t load this PDF" })).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "Retry PDF" }).click();
  const iframe = page.locator('iframe[title="PDF preview: notes.pdf"]');
  await expect(iframe).toHaveAttribute("src", /^blob:/);
  const src = await iframe.getAttribute("src");
  await page.getByRole("button", { name: "Close document" }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { revoked: string[] }).revoked)).toContain(src);
});

test("document processing shows persisted status and extracted evidence after reload", async ({ page }) => {
  await mockAuth(page);
  let row = { ...documentRow, original_filename: "notes.pdf", page_count: 1,
    file_size_bytes: pdfFixture().length, content: "", processing_run_id: null as string | null,
    processing_started_at: null as string | null };
  let finish: () => void = () => {};
  await page.route("**/api/documents?*", route => route.fulfill({ json: [row] }));
  await page.route(`**/api/documents/${row.id}`, route => route.fulfill({ json: row }));
  await page.route(`**/api/documents/${row.id}/process`, async route => {
    expect(route.request().method()).toBe("POST");
    row = { ...row, status: "processing", processing_run_id: "00000000-0000-0000-0000-000000000020", processing_started_at: new Date().toISOString() };
    finish = () => { row = { ...row, status: "needs_review" }; };
    await route.fulfill({ status: 202, json: row });
  });
  await page.route(`**/api/documents/${row.id}/extraction`, route => route.fulfill({ json: {
    native_extraction: { pages: [{ page_number: 1, warnings: [], regions: [{ text: "Budget approved." }] }] },
    ocr_extraction: { pages: [{ page_number: 1, warnings: [], regions: [{ text: "2000 USD" }] }] },
    pages: [{ page_number: 1, warnings: ["Check the original page."] }],
    tables: [{ page_number: 1, warnings: ["Check table values."] }], warnings: [],
  } }));
  await page.goto("/sign-in"); await signIn(page);
  await page.getByRole("button", { name: /Project notes.*Work/ }).click();
  await page.getByRole("button", { name: "Extract document", exact: true }).click();
  await expect(page.getByRole("dialog").getByText("Processing", { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("button", { name: "Extracting…", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Close document" }).click();
  finish();
  await expect(page.locator('.document-row [data-status="needs_review"]')).toBeVisible({ timeout: 12000 });
  await page.getByRole("button", { name: /Project notes.*Work/ }).click();
  await expect(page.getByRole("dialog").getByText("Needs review", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "View extracted text" }).click();
  await expect(page.getByRole("dialog").getByText("Budget approved.", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog").getByText("2000 USD", { exact: true })).toBeVisible();
  await page.getByText("Review notes (2)").click();
  await expect(page.getByText("Check table values.")).toBeVisible();
  await page.screenshot({ path: "test-results/document-processing.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.getByRole("dialog").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: "test-results/document-processing-mobile.png", fullPage: true });
  await page.reload();
  await expect(page.locator('.document-row [data-status="needs_review"]')).toBeVisible();
});

test("failed extraction refreshes the persisted failure and offers retry", async ({ page }) => {
  await mockAuth(page);
  let row = { ...documentRow, original_filename: "notes.pdf", page_count: 1,
    file_size_bytes: pdfFixture().length, content: "", processing_error: null as string | null };
  await page.route("**/api/documents?*", route => route.fulfill({ json: [row] }));
  await page.route(`**/api/documents/${row.id}`, route => route.fulfill({ json: row }));
  await page.route(`**/api/documents/${row.id}/process`, async route => {
    row = { ...row, status: "failed", processing_error: "Extraction could not finish. Please retry." };
    await route.fulfill({ status: 503, json: { detail: "Could not confirm extraction. Refresh the document status." } });
  });
  await page.goto("/sign-in"); await signIn(page);
  await page.getByRole("button", { name: /Project notes.*Work/ }).click();
  await page.getByRole("button", { name: "Extract document", exact: true }).click();
  await expect(page.getByRole("dialog").getByText("Failed", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry extraction", exact: true })).toBeEnabled();
});
