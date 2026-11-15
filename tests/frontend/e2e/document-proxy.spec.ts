import { test, expect } from "@playwright/test";
import { pdfFixture } from "./pdf-fixture.mjs";

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

test("PDF file proxy preserves original bytes and requires credentials", async ({ request }) => {
  const path = "/api/documents/00000000-0000-0000-0000-000000000010/file";
  expect((await request.get(path)).status()).toBe(401);
  const response = await request.get(path, { headers });
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("application/pdf");
  expect(response.headers()["cache-control"]).toBe("no-store");
  expect(await response.body()).toEqual(pdfFixture());
});

test("processing and extraction proxies preserve authentication and response status", async ({ request }) => {
  const base = "/api/documents/00000000-0000-0000-0000-000000000010";
  expect((await request.post(`${base}/process`)).status()).toBe(401);
  expect((await request.get(`${base}/extraction`)).status()).toBe(401);
  const processed = await request.post(`${base}/process`, { headers });
  expect(processed.status()).toBe(202);
  expect((await processed.json()).status).toBe("processing");
  const report = await request.get(`${base}/extraction`, { headers });
  expect(report.status()).toBe(200);
  expect(report.headers()["cache-control"]).toBe("no-store");
  expect((await report.json()).native_extraction.pages[0].regions[0].text).toBe("Saved budget evidence");
});
