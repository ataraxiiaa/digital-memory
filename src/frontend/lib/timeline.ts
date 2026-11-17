export type Precision = "day" | "week" | "month" | "quarter" | "year";
export type ReviewStatus = "suggested" | "confirmed" | "rejected";
export type DateStatus = "resolved" | "needs_anchor" | "ambiguous" | "unparsed" | "none";

export type Ref = {
  run_id: string; mention_id: string; role: "event" | "date" | "location" | "participant"; label: string; text: string;
  citation: { start_char: number; end_char: number; page_number: number };
};
export type TimelineItem = {
  id: string; document_id: string; document_title: string | null; title: string; page_number: number;
  sentence_text: string; sentence_start: number; sentence_end: number; date_text: string | null; date_status: DateStatus;
  start_date: string | null; end_date: string | null; precision: Precision | null; flags: string[]; refs: Ref[];
  review_status: ReviewStatus; corrected_title: string | null; corrected_start: string | null; corrected_end: string | null;
  corrected_precision: Precision | null; stale: boolean;
};
export type TimelinePage = { total: number; offset: number; limit: number; undated: number; items: TimelineItem[] };

export const flagLabel: Record<string, string> = {
  year_inferred: "Year assumed from the document date",
  relative_date: "Worked out from the document date",
  weekday_direction_assumed: "Weekday direction assumed",
  part_of_day_ignored: "Time of day ignored",
  ambiguous_day_month: "Day and month order unclear",
  bare_weekday: "A weekday alone does not give a date",
  multiple_dates_nearest_chosen: "Several dates in the sentence; the nearest was used",
  date_range: "Range from two dates",
  date_changed_language: "The sentence mentions a change; the later date was used",
  season_unsupported: "Seasons are not turned into dates",
  invalid_date: "That date does not exist",
};

export const noDateReason: Record<DateStatus, string> = {
  resolved: "", none: "No date in this sentence", ambiguous: "The date is unclear",
  unparsed: "The date could not be read", needs_anchor: "Needs the document's own date",
};

const utc = (iso: string) => new Date(`${iso}T00:00:00Z`);
const fmt = (iso: string, options: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", ...options }).format(utc(iso));

export function effective(item: TimelineItem) {
  return {
    title: item.corrected_title ?? item.title,
    start: item.corrected_start ?? item.start_date,
    end: item.corrected_end ?? item.end_date,
    precision: item.corrected_precision ?? item.precision,
  };
}

export function formatDate(start: string | null, end: string | null, precision: Precision | null): string {
  if (!start || !end || !precision) return "No date";
  switch (precision) {
    case "year": return start.slice(0, 4) === end.slice(0, 4) ? start.slice(0, 4) : `${start.slice(0, 4)}–${end.slice(0, 4)}`;
    case "quarter": return `Q${Math.floor((Number(start.slice(5, 7)) - 1) / 3) + 1} ${start.slice(0, 4)}`;
    case "month": return fmt(start, { month: "long", year: "numeric" });
    case "week": return `Week of ${fmt(start, { day: "numeric", month: "short", year: "numeric" })}`;
    default:
      if (start === end) return fmt(start, { day: "numeric", month: "short", year: "numeric" });
      return start.slice(0, 7) === end.slice(0, 7)
        ? `${fmt(start, { day: "numeric" })}–${fmt(end, { day: "numeric", month: "short", year: "numeric" })}`
        : `${fmt(start, { day: "numeric", month: "short" })} – ${fmt(end, { day: "numeric", month: "short", year: "numeric" })}`;
  }
}

/** Split the sentence around the event and date mentions so the source can be shown with highlights. */
export function highlightParts(item: TimelineItem) {
  const marks = item.refs.filter(ref => ref.role === "event" || ref.role === "date")
    .map(ref => ({ start: ref.citation.start_char - item.sentence_start, end: ref.citation.end_char - item.sentence_start, role: ref.role }))
    .filter(mark => mark.start >= 0 && mark.end <= item.sentence_text.length && mark.end > mark.start)
    .sort((a, b) => a.start - b.start);
  const parts: { text: string; role?: string }[] = [];
  let cursor = 0;
  for (const mark of marks) {
    if (mark.start < cursor) continue;
    if (mark.start > cursor) parts.push({ text: item.sentence_text.slice(cursor, mark.start) });
    parts.push({ text: item.sentence_text.slice(mark.start, mark.end), role: mark.role });
    cursor = mark.end;
  }
  if (cursor < item.sentence_text.length) parts.push({ text: item.sentence_text.slice(cursor) });
  return parts;
}
