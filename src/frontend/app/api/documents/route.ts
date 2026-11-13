import { NextRequest } from "next/server";
import { proxyDocuments } from "../../../lib/document-proxy";

export async function GET(request: NextRequest) {
  const params = new URLSearchParams();
  for (const name of ["limit", "offset"]) {
    const value = request.nextUrl.searchParams.get(name);
    if (value !== null) params.set(name, value);
  }
  return proxyDocuments(request, `/documents?${params}`);
}
