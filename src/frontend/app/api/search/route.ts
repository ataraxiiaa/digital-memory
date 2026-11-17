import { NextRequest } from "next/server";
import { proxySearch, searchQuery } from "../../../lib/search-proxy";

export async function GET(request: NextRequest) {
  const parsed = searchQuery(request);
  if ("error" in parsed) return parsed.error;
  return proxySearch(request, `/search?${parsed.query}`);
}
