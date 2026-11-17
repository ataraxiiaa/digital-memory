import { NextRequest } from "next/server";
import { proxySearch } from "../../../../lib/search-proxy";

export async function POST(request: NextRequest) {
  return proxySearch(request, "/search/index", "POST");
}
