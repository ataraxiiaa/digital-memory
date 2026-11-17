import { NextRequest } from "next/server";
import { proxySearch } from "../../../../lib/search-proxy";

export async function GET(request: NextRequest) {
  return proxySearch(request, "/search/status");
}
