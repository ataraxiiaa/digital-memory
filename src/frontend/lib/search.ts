export type SearchType = "all" | "passages" | "entities" | "timeline";

export type Segment = { text: string; match: boolean };

export type Hit = {
  kind: "passage" | "entity" | "mention" | "timeline";
  score: number;
  snippet: Segment[];
  document_id: string | null;
  document_title: string | null;
  page_number: number | null;
  citation: { document_id: string; page_number: number; start_char: number; end_char: number } | null;
  review_required: boolean | null;
  entity_id: string | null;
  mention_id: string | null;
  entity_type: string | null;
  entity_status: string | null;
  mention_count: number | null;
  timeline_item_id: string | null;
  date_text: string | null;
  start_date: string | null;
  review_status: string | null;
};

export type SearchResponse = {
  query: string; type: SearchType; totals: Record<string, number>; offset: number; limit: number; results: Hit[];
};

export type IndexDocument = {
  document_id: string; title: string; status: "current" | "stale" | "missing" | "not_ready";
  passage_count: number; indexed_at: string | null;
};

export type IndexStatus = { documents: IndexDocument[]; current: number; stale: number; missing: number; not_ready: number };

export const typeLabel: Record<SearchType, string> = {
  all: "Everything", passages: "Passages", entities: "People and things", timeline: "Timeline",
};

export const kindLabel: Record<Hit["kind"], string> = {
  passage: "Passage", entity: "Entity", mention: "Reviewed mention", timeline: "Timeline",
};
