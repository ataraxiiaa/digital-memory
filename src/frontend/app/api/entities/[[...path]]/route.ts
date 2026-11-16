import { NextRequest, NextResponse } from "next/server";

// Forwards only an explicit allowlist of entity-identity operations to the backend.
const headers = { "Cache-Control": "no-store" };
const ID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const ROUTES: { method: string; pattern: RegExp; body: boolean }[] = [
  { method: "GET", pattern: /^$/, body: false },
  { method: "GET", pattern: /^suggestions$/, body: false },
  { method: "POST", pattern: /^resolve$/, body: false },
  { method: "POST", pattern: /^merge$/, body: true },
  { method: "POST", pattern: /^mentions\/split$/, body: true },
  { method: "POST", pattern: new RegExp(`^suggestions/${ID}/(confirm|dismiss)$`, "i"), body: true },
  { method: "POST", pattern: new RegExp(`^merges/${ID}/undo$`, "i"), body: false },
  { method: "GET", pattern: new RegExp(`^${ID}$`, "i"), body: false },
  { method: "PATCH", pattern: new RegExp(`^${ID}$`, "i"), body: true },
  { method: "POST", pattern: new RegExp(`^${ID}/(confirm|link)$`, "i"), body: true },
];
const FILTERS: [string, RegExp][] = [
  ["limit", /^\d{1,3}$/], ["offset", /^\d{1,6}$/], ["type", /^[a-z]{3,20}$/], ["status", /^[a-z]{3,20}$/],
  ["min_score", /^(0|1|0?\.\d{1,3})$/], ["q", /^[^\u0000-\u001f]{1,100}$/u],
];

async function forward(request: NextRequest, { params }: { params: Promise<{ path?: string[] }> }) {
  const { path = [] } = await params;
  const joined = path.join("/");
  const route = ROUTES.find(candidate => candidate.method === request.method && candidate.pattern.test(joined));
  if (!route) return NextResponse.json({ detail: "Not found." }, { status: 404, headers });
  const authorization = request.headers.get("authorization");
  if (!authorization?.toLowerCase().startsWith("bearer ")) {
    return NextResponse.json({ detail: "Sign in again to access your entities." }, { status: 401, headers });
  }
  const query = new URLSearchParams();
  if (request.method === "GET") {
    for (const [key, pattern] of FILTERS) {
      const value = request.nextUrl.searchParams.get(key);
      if (value && pattern.test(value)) query.set(key, value);
    }
  }
  let body: string | undefined;
  if (route.body) {
    body = await request.text();
    if (body.length > 2048) return NextResponse.json({ detail: "Request too large." }, { status: 413, headers });
  }
  const base = (process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  try {
    const response = await fetch(`${base}/entities${joined ? `/${joined}` : ""}${query.size ? `?${query}` : ""}`, {
      method: request.method, cache: "no-store", redirect: "error",
      headers: { Authorization: authorization, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body || undefined, signal: AbortSignal.any([request.signal, AbortSignal.timeout(60000)]),
    });
    if (response.status === 204) return new NextResponse(null, { status: 204, headers });
    if (!response.ok) {
      // Fixed messages only; never reflect backend or request text.
      const detail = response.status === 401 ? "Your session expired. Please sign in again."
        : response.status === 404 ? "That entity was not found. It may have been merged or removed."
        : response.status === 409 ? "That change conflicts with the current state. Refresh and try again."
        : response.status === 422 ? "That change is not allowed. Entities only merge with the same type."
        : "Couldn’t reach your entities. Please try again.";
      return NextResponse.json({ detail }, { status: [401, 404, 409, 422].includes(response.status) ? response.status : 503, headers });
    }
    return NextResponse.json(await response.json(), { status: response.status, headers });
  } catch {
    return NextResponse.json({ detail: "Couldn’t reach the entity service. Please try again." }, { status: 503, headers });
  }
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
