import { NextRequest, NextResponse } from "next/server";

const headers = { "Cache-Control": "no-store" };
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Only these query parameters are forwarded, and only when they match; anything else is dropped.
const FILTERS: [string, RegExp][] = [
  ["type", /^(passages|entities|timeline|all)$/], ["document_id", UUID],
  ["limit", /^\d{1,2}$/], ["offset", /^\d{1,5}$/],
];

export function searchQuery(request: NextRequest): { query: URLSearchParams } | { error: NextResponse } {
  const params = request.nextUrl.searchParams;
  const q = (params.get("q") ?? "").trim();
  // eslint-disable-next-line no-control-regex
  if (!q || q.length > 200 || /[\u0000-\u001f]/.test(q)) {
    return { error: NextResponse.json({ detail: "Enter something to search for (up to 200 characters)." }, { status: 422, headers }) };
  }
  const query = new URLSearchParams({ q });
  for (const [key, pattern] of FILTERS) {
    const value = params.get(key);
    if (value && pattern.test(value)) query.set(key, value);
  }
  return { query };
}

export async function proxySearch(request: NextRequest, path: string, method: "GET" | "POST" = "GET") {
  const authorization = request.headers.get("authorization");
  if (!authorization?.toLowerCase().startsWith("bearer ")) {
    return NextResponse.json({ detail: "Sign in again to search your documents." }, { status: 401, headers });
  }
  const base = (process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  try {
    const response = await fetch(`${base}${path}`, {
      method, headers: { Authorization: authorization }, cache: "no-store", redirect: "error",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(method === "POST" ? 120000 : 30000)]),
    });
    if (!response.ok) {
      // Fixed messages only; never reflect backend or request text.
      const detail = response.status === 401 ? "Your session expired. Please sign in again."
        : response.status === 404 ? "That document was not found."
        : response.status === 409 ? "Extract this document before adding it to search."
        : response.status === 422 ? "That search is not allowed. Try different words."
        : method === "POST" ? "Couldn’t update the search index. Please try again." : "Couldn’t search right now. Please try again.";
      return NextResponse.json({ detail }, { status: [401, 404, 409, 422].includes(response.status) ? response.status : 503, headers });
    }
    return NextResponse.json(await response.json(), { status: response.status, headers });
  } catch {
    return NextResponse.json({ detail: "Couldn’t reach the search service. Please try again." }, { status: 503, headers });
  }
}
