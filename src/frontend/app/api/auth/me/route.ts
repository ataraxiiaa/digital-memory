import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const authorization = request.headers.get("authorization");
  const headers = { "Cache-Control": "no-store" };
  if (!authorization?.toLowerCase().startsWith("bearer ")) {
    return NextResponse.json({ detail: "Sign-in required" }, { status: 401, headers });
  }
  const base = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/auth/me`, {
      headers: { Authorization: authorization }, cache: "no-store",
      redirect: "error", signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) {
      const status = response.status === 401 ? 401 : 503;
      return NextResponse.json({ detail: status === 401 ? "Sign-in required" : "Authentication service is unavailable" }, { status, headers });
    }
    const user = await response.json();
    if (typeof user.id !== "string" || user.role !== "authenticated") throw new Error("Invalid identity");
    return NextResponse.json({ id: user.id, email: user.email ?? null, role: user.role, email_confirmed_at: user.email_confirmed_at ?? null }, { headers });
  } catch {
    return NextResponse.json({ detail: "Authentication service is unavailable" }, { status: 503, headers });
  }
}
