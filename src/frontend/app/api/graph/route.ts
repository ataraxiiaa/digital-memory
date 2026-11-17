import { NextRequest, NextResponse } from "next/server";

const headers = { "Cache-Control": "no-store" };
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// The graph is read-only and contains only confirmed relationships.
export async function GET(request: NextRequest) {
  const authorization = request.headers.get("authorization");
  if (!authorization?.toLowerCase().startsWith("bearer ")) {
    return NextResponse.json({ detail: "Sign in again to see your graph." }, { status: 401, headers });
  }
  const documentId = request.nextUrl.searchParams.get("document_id");
  const suffix = documentId && ID.test(documentId) ? `?document_id=${documentId}` : "";
  const base = (process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  try {
    const response = await fetch(`${base}/graph${suffix}`, {
      cache: "no-store", redirect: "error", headers: { Authorization: authorization },
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(30000)]),
    });
    if (!response.ok) {
      const detail = response.status === 401 ? "Your session expired. Please sign in again." : "Couldn’t load your graph. Please try again.";
      return NextResponse.json({ detail }, { status: response.status === 401 ? 401 : 503, headers });
    }
    return NextResponse.json(await response.json(), { status: 200, headers });
  } catch {
    return NextResponse.json({ detail: "Couldn’t reach the graph service. Please try again." }, { status: 503, headers });
  }
}
