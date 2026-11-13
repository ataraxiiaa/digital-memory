import { NextRequest, NextResponse } from "next/server";
import { proxyDocuments } from "../../../../lib/document-proxy";

export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    return NextResponse.json({ detail: "Document not found." }, { status: 404, headers: { "Cache-Control": "no-store" } });
  }
  return proxyDocuments(request, `/documents/${id}`);
}
