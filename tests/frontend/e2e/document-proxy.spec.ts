import { test, expect } from "@playwright/test";

const headers = { Authorization: "Bearer fixture-access-token" };

test("real proxy forwards multipart bytes and user token to the upstream", async ({ request }) => {
  const response = await request.post("/api/documents/upload", {
    headers,
    multipart: { file: { name: "proxy-fixture.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-proxy-body") }, title: "Proxy upload" },
  });
  expect(response.status()).toBe(201);
  expect(response.headers()["cache-control"]).toBe("no-store");
  expect((await response.json()).status).toBe("uploaded");
});

test("real proxy preserves pagination and reads document details", async ({ request }) => {
  const response = await request.get("/api/documents?limit=20&offset=40", { headers });
  expect(response.status()).toBe(200);
  expect((await response.json())[0].title).toBe("Page offset 40");
  const detail = await request.get("/api/documents/00000000-0000-0000-0000-000000000010", { headers });
  expect(detail.status()).toBe(200);
  expect((await detail.json()).title).toBe("Proxy upload");
});

test("real proxy maps rejected tokens and invalid uploads safely", async ({ request }) => {
  const rejected = await request.get("/api/documents", { headers: { Authorization: "Bearer invalid" } });
  expect(rejected.status()).toBe(401);
  const wrongType = await request.post("/api/documents/upload", { headers, data: "not multipart" });
  expect(wrongType.status()).toBe(415);
  const invalidId = await request.get("/api/documents/not-an-id", { headers });
  expect(invalidId.status()).toBe(404);
});
