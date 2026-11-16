import { NextRequest, NextResponse } from "next/server";
import { proxyDocuments } from "../../../../../../../lib/document-proxy";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function PATCH(request: NextRequest,
  { params }: { params: Promise<{ id: string; runId: string; mentionId: string }> }) {
  const { id, runId, mentionId } = await params;
  if (![id, runId, mentionId].every(value => UUID.test(value))) {
    return NextResponse.json({ detail: "Entity not found." }, { status: 404 });
  }
  return proxyDocuments(request, `/documents/${id}/entities/${runId}/${mentionId}`, "review");
}
