export type DocumentStatus = "uploaded" | "processing" | "ready" | "needs_review" | "failed";

export type DocumentSummary = {
  id: string;
  title: string;
  document_date: string | null;
  collection: string;
  created_at: string;
  status: DocumentStatus;
  processing_run_id?: string | null;
  processing_started_at?: string | null;
  processing_error?: string | null;
};

export type DocumentDetail = DocumentSummary & {
  content: string;
  original_filename: string | null;
  file_size_bytes: number | null;
  page_count: number | null;
};

export const documentStatusLabel: Record<DocumentStatus, string> = {
  uploaded: "Uploaded",
  processing: "Processing",
  ready: "Ready",
  needs_review: "Needs review",
  failed: "Failed",
};
