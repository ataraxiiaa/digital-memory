export const ENTITY_TYPES = ["person", "organization", "project", "model", "method", "dataset", "component", "date", "event", "location"] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];
export type EntityStatus = "suggested" | "confirmed" | "merged";

export type Evidence = {
  run_id: string; mention_id: string; document_id: string; page_number: number; text: string;
};
export type EntityRecord = {
  id: string; type: EntityType; canonical_name: string; status: EntityStatus;
  mention_count: number; document_count: number; aliases: string[];
};
export type EntityDetail = {
  entity: EntityRecord; mentions: Evidence[];
  merges: { id: string; absorbed_name: string; merged_at: string; undone_at: string | null }[];
};
export type SuggestionSide = {
  id: string; type: EntityType; canonical_name: string; mention_count: number; aliases: string[]; evidence: Evidence[];
};
export type Suggestion = {
  id: string; kind: "exact" | "initial" | "surname" | "related"; score: number; ambiguous: boolean;
  reason: string; a: SuggestionSide; b: SuggestionSide;
};

export const kindLabel: Record<Suggestion["kind"], string> = {
  exact: "Same name", initial: "Initial or middle name", surname: "Surname only (weak)", related: "Possibly related (weak)",
};

export function sourceLabel(evidence: Evidence, titles: Record<string, string>) {
  return `${titles[evidence.document_id] ?? "Document " + evidence.document_id.slice(0, 8)}, page ${evidence.page_number}`;
}
