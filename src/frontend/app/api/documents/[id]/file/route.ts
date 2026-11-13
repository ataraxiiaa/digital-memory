import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
  const authorization = request.headers.get("authorization");
  if (!authorization?.toLowerCase().startsWith("bearer ")) return NextResponse.json({ detail: "Sign in again to view this PDF." }, { status: 401, headers });
  const { id } = await params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) return NextResponse.json({ detail: "Original PDF not found." }, { status: 404, headers });
  const base = (process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  try {
    const response = await fetch(`${base}/documents/${id}/file`, {
      headers: { Authorization: authorization }, cache: "no-store", redirect: "error",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(65000)]),
    });
    if (!response.ok) {
      const status = [401, 404].includes(response.status) ? response.status : 503;
      return NextResponse.json({ detail: status === 401 ? "Your session expired. Please sign in again." : status === 404 ? "Original PDF not found." : "Couldn’t load this PDF. Please try again." }, { status, headers });
    }
    if (!response.headers.get("content-type")?.toLowerCase().startsWith("application/pdf")) throw new Error("Unexpected response");
    return new NextResponse(response.body, { headers: { ...headers, "Content-Type": "application/pdf", "Content-Disposition": response.headers.get("content-disposition") ?? "inline; filename=document.pdf" } });
  } catch {
    return NextResponse.json({ detail: "Couldn’t load this PDF. Please try again." }, { status: 503, headers });
  }
}
