import { test, expect, type Page } from "@playwright/test";

async function fillSignup(page: Page, confirmation = "fictional-password") {
  await page.getByLabel("Email address").fill("alex@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fictional-password");
  await page.getByLabel("Confirm password").fill(confirmation);
  await page.getByRole("button", { name: "Create account", exact: true }).click();
}

const user = { id: "00000000-0000-0000-0000-000000000001", email: "alex@example.test", role: "authenticated", aud: "authenticated", app_metadata: {}, user_metadata: {}, created_at: "2026-11-13T10:00:00Z" };

async function mockAuth(page: Page, options: { badPassword?: boolean; backendStatus?: number } = {}) {
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
