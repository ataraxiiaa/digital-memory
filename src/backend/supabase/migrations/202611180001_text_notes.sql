begin;

-- Plain-text notes reuse the PDF extraction contract. The note's exact text is
-- the single region of a one-page extraction, so a citation's character offsets
-- are offsets into the note. The extraction is tied to the SHA-256 of the exact
-- UTF-8 content: the database recomputes it and requires the report to carry the
-- same hash and the same text, so a client cannot attach a report that does not
-- match the stored note.
--
-- Behavior:
--   * Same text again: returns the existing extraction and changes nothing, so
--     entity runs and review decisions stay attached.
--   * Changed text (only possible through an administrative edit, because
--     clients have no UPDATE grant): a new extraction replaces the document's
--     current one. Its region IDs derive from the new hash, so review decisions
--     made on the old text are never carried onto the new text (carry-over
--     matches on region ID). Older entity runs stay as history.
create function public.create_text_extraction(p_document_id uuid, p_run_id uuid, p_report jsonb)
returns uuid language plpgsql security definer set search_path = '' as $$
declare
    d public.documents%rowtype;
    digest text;
    existing public.document_extractions%rowtype;
begin
    select * into d from public.documents
        where id = p_document_id and owner_id = auth.uid() for update;
    if not found or d.storage_path is not null then
        raise exception 'Note not found' using errcode = 'P0002';
    end if;
    digest := encode(sha256(convert_to(d.content, 'UTF8')), 'hex');
    if p_report is null or jsonb_typeof(p_report) <> 'object'
        or p_report #>> '{native_extraction,document_id}' is distinct from d.id::text
        or p_report #>> '{native_extraction,source_sha256}' is distinct from digest
        or p_report #>> '{native_extraction,page_count}' is distinct from '1'
        or jsonb_array_length(p_report #> '{native_extraction,pages}') <> 1
        or jsonb_array_length(p_report #> '{native_extraction,pages,0,regions}') <> 1
        or p_report #>> '{native_extraction,pages,0,regions,0,text}' is distinct from d.content
        or coalesce(jsonb_typeof(p_report -> 'ocr_extraction'), 'null') <> 'null'
        or jsonb_array_length(coalesce(p_report -> 'tables', '[]'::jsonb)) <> 0
    then
        raise exception 'Report does not match the note' using errcode = '22023';
    end if;
    if d.processing_run_id is not null then
        select * into existing from public.document_extractions
            where id = d.processing_run_id and owner_id = d.owner_id;
        if found and existing.status in ('ready', 'needs_review')
            and existing.report #>> '{native_extraction,source_sha256}' = digest then
            return existing.id;
        end if;
    end if;
    insert into public.document_extractions(id, document_id, owner_id, status, finished_at, report)
        values (p_run_id, d.id, d.owner_id, 'ready', now(), p_report);
    update public.documents set status = 'ready', processing_run_id = p_run_id,
        processing_started_at = null, processing_error = null where id = d.id;
    return p_run_id;
end $$;

revoke all on function public.create_text_extraction(uuid, uuid, jsonb) from public, anon;
grant execute on function public.create_text_extraction(uuid, uuid, jsonb) to authenticated;

commit;
