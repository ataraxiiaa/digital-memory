import { NextRequest, NextResponse } from "next/server";
import { proxyDocuments } from "../../../../../lib/document-proxy";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const notFound = () => NextResponse.json({ detail: "Document not found." }, { status: 404 });

export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!UUID.test(id)) return notFound();
  // Forward only known filters so arbitrary query text never reaches the backend.
  const source = request.nextUrl.searchParams;
  const query = new URLSearchParams();
  for (const [key, pattern] of [["limit", /^\d{1,3}$/], ["offset", /^\d{1,6}$/], ["label", /^[a-z]{3,20}$/],
    ["status", /^(suggested|accepted|rejected)$/], ["min_score", /^(0|1|0?\.\d{1,3})$/]] as const) {
    const value = source.get(key);
    if (value && pattern.test(value)) query.set(key, value);
  }
  const suffix = query.size ? `?${query}` : "";
  return proxyDocuments(request, `/documents/${id}/entities${suffix}`);
}

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!UUID.test(id)) return notFound();
  const schema = request.nextUrl.searchParams.get("schema");
  const suffix = schema && /^(core|research|full)-v1$/.test(schema) ? `?schema=${schema}` : "";
  return proxyDocuments(request, `/documents/${id}/entities${suffix}`, "entities");
}
