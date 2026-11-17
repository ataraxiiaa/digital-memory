import { NextRequest, NextResponse } from "next/server";
import { UUID, proxySearch } from "../../../../../lib/search-proxy";

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!UUID.test(id)) return NextResponse.json({ detail: "That document was not found." }, { status: 404, headers: { "Cache-Control": "no-store" } });
  return proxySearch(request, `/documents/${id}/search-index`, "POST");
}
