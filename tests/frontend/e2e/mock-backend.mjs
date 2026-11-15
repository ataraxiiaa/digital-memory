// Local-only upstream fixture for exercising real Next.js proxy routes.
import { createServer } from "node:http";
import { pdfFixture } from "./pdf-fixture.mjs";

const row = { id: "00000000-0000-0000-0000-000000000010", title: "Proxy upload", collection: "Unfiled", document_date: null, created_at: "2026-11-13T12:00:00Z", status: "uploaded", content: "" };
createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1:3199");
  const send = (status, data) => { response.writeHead(status, { "Content-Type": "application/json" }); response.end(JSON.stringify(data)); };
  if (url.pathname === "/health") return send(200, { status: "ok" });
  if (request.headers.authorization !== "Bearer fixture-access-token") return send(401, { detail: "Rejected" });
  if (url.pathname === "/documents" && request.method === "GET") {
    return send(200, [{ ...row, title: `Page offset ${url.searchParams.get("offset")}` }]);
  }
  if (url.pathname === "/documents/upload" && request.method === "POST") {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString();
    if (!request.headers["content-type"]?.includes("boundary=") || !body.includes("proxy-fixture.pdf") || !body.includes("%PDF-proxy-body")) return send(422, { detail: "Multipart body was not forwarded intact" });
    return send(201, row);
  }
  if (url.pathname === `/documents/${row.id}/file`) {
    response.writeHead(200, { "Content-Type": "application/pdf", "Content-Disposition": "inline; filename=preview.pdf" });
    return response.end(pdfFixture());
  }
  if (url.pathname === `/documents/${row.id}/process` && request.method === "POST") {
    return send(202, { ...row, status: "processing", processing_run_id: "00000000-0000-0000-0000-000000000020" });
  }
  if (url.pathname === `/documents/${row.id}/extraction`) {
    return send(200, { native_extraction: { pages: [{ page_number: 1, warnings: [], regions: [{ text: "Saved budget evidence" }] }] }, pages: [], tables: [], warnings: [], ocr_extraction: null });
  }
  if (url.pathname === `/documents/${row.id}`) return send(200, row);
  return send(404, { detail: "Not found" });
}).listen(3199, "127.0.0.1");
