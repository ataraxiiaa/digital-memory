export const documents = [
  { id: "1", title: "A quieter way to plan", type: "Note", date: "2026-11-12", collection: "Personal", excerpt: "Keep Sunday mornings open for a walk and a little planning. A short list of what matters is more useful than a long list of everything.", people: ["Alex Morgan"], tags: ["Routines", "Reflection"] },
  { id: "2", title: "Studio project · first conversation", type: "Meeting", date: "2026-11-10", collection: "Studio", excerpt: "Alex and Sam agreed to start with a small reading space. The first prototype should make it easy to find a note and understand where it came from.", people: ["Alex Morgan", "Sam Chen"], tags: ["Studio project", "Planning"] },
  { id: "3", title: "Notes from the library", type: "Note", date: "2026-11-08", collection: "Learning", excerpt: "An interesting idea from today's reading: remembering is easier when an idea has context. Save the question that led to a passage, alongside the passage itself.", people: ["Alex Morgan"], tags: ["Reading", "Reflection"] },
  { id: "4", title: "Weekend in the hills", type: "Journal", date: "2026-11-05", collection: "Personal", excerpt: "A long walk with Sam, a thermos of coffee, and no particular destination. We talked about returning in the spring when the trail opens again.", people: ["Sam Chen"], tags: ["Travel", "Outdoors"] },
  { id: "5", title: "Prototype review", type: "Meeting", date: "2026-11-03", collection: "Studio", excerpt: "Sam suggested putting the source next to each finding. Alex will simplify the navigation before the next review. Keep the first version focused on the essentials.", people: ["Alex Morgan", "Sam Chen"], tags: ["Studio project", "Design"] },
  { id: "6", title: "A reading list for slower days", type: "Note", date: "2026-11-01", collection: "Learning", excerpt: "Make room for essays on attention, a book about urban gardens, and the collection of walking journals. Read one thing at a time and leave a few notes.", people: [], tags: ["Reading", "Ideas"] },
];
export type MemoryDocument = (typeof documents)[number];
export function filterDocuments(query: string, collection: string) {
  const term = query.toLowerCase().trim();
  return documents.filter(doc => (collection === "All collections" || doc.collection === collection) && [doc.title, doc.excerpt, ...doc.people, ...doc.tags].join(" ").toLowerCase().includes(term));
}
export function formatDate(date: string) {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(`${date}T00:00:00Z`));
}
