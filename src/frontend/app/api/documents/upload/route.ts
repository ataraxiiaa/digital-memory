import { NextRequest } from "next/server";
import { proxyDocuments } from "../../../../lib/document-proxy";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  return proxyDocuments(request, "/documents/upload", "upload");
}
