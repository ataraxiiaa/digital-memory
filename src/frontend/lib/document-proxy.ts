import { NextRequest, NextResponse } from "next/server";

const headers = { "Cache-Control": "no-store" };
const MAX_MULTIPART_BYTES = 30 * 1024 * 1024 + 64 * 1024;

export async function proxyDocuments(request: NextRequest, path: string, operation: "read" | "upload" | "process" = "read") {
  const upload = operation === "upload";
  const processing = operation === "process";
  const authorization = request.headers.get("authorization");
  if (!authorization?.toLowerCase().startsWith("bearer ")) {
    return NextResponse.json({ detail: "Sign in again to access your documents." }, { status: 401, headers });
  }
  const base = (process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  let oversized = false;
  try {
    const upstreamHeaders: Record<string, string> = { Authorization: authorization };
    let body: ReadableStream<Uint8Array> | undefined;
    if (upload) {
      const type = request.headers.get("content-type") ?? "";
      if (!type.toLowerCase().startsWith("multipart/form-data;")) {
        return NextResponse.json({ detail: "Choose a PDF to upload." }, { status: 415, headers });
      }
      if (Number(request.headers.get("content-length")) > MAX_MULTIPART_BYTES) {
        return NextResponse.json({ detail: "PDF files must not exceed 30 MiB." }, { status: 413, headers });
      }
      if (!request.body) return NextResponse.json({ detail: "Choose a PDF to upload." }, { status: 422, headers });
      let bytes = 0;
      body = request.body.pipeThrough(new TransformStream({
        transform(chunk, controller) {
          bytes += chunk.byteLength;
          if (bytes > MAX_MULTIPART_BYTES) { oversized = true; controller.error(new Error("Upload too large")); return; }
          controller.enqueue(chunk);
        },
      }));
      upstreamHeaders["Content-Type"] = type;
    }
    const options: RequestInit & { duplex?: "half" } = {
      method: upload || processing ? "POST" : "GET", headers: upstreamHeaders,
      cache: "no-store", redirect: "error",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(processing ? 300000 : upload ? 120000 : 20000)]),
    };
    if (body) { options.body = body; options.duplex = "half"; }
    const response = await fetch(`${base}${path}`, options);
    if (!response.ok) {
      // Only propagate fixed upload messages; never reflect a user's request body.
      let detail = response.status === 401 ? "Your session expired. Please sign in again."
        : response.status === 404 ? "Document not found."
        : upload ? "Could not confirm the upload. Refresh your document list before trying again."
        : "Couldn’t load your documents. Please try again.";
      if (upload && [413, 415, 422].includes(response.status)) {
        const error = await response.json().catch(() => null);
        detail = typeof error?.detail === "string" ? error.detail : "Check the PDF and form fields, then try again.";
      }
      if (processing) detail = response.status === 409 ? "Extraction is already active or the processor is busy. Refresh the document status and try again shortly." : response.status === 401 ? "Your session expired. Please sign in again." : "Could not confirm extraction. Refresh the document status before retrying.";
      return NextResponse.json({ detail }, { status: [401, 404, 409, 413, 415, 422].includes(response.status) ? response.status : 503, headers });
    }
    return NextResponse.json(await response.json(), { status: response.status, headers });
  } catch {
    return NextResponse.json({ detail: oversized ? "PDF files must not exceed 30 MiB." : upload ? "Could not confirm the upload. Refresh your document list before trying again." : "Couldn’t reach the document service. Please try again." }, { status: oversized ? 413 : 503, headers });
  }
}
