begin;

-- Re-running entity extraction used to discard every accept/reject decision.
-- A new run now inherits the latest earlier human decision for the same exact
-- source span and model label in the same document. Spans in a re-extracted
-- document (new region IDs) never match, so a changed source cannot inherit one.
create or replace function public.finish_entity_run(
    p_run_id uuid, p_status text, p_meta jsonb default null, p_mentions jsonb default '[]'::jsonb
) returns boolean language plpgsql security definer set search_path = '' as $$
declare
    attempt public.entity_runs%rowtype;
begin
    if p_status not in ('completed', 'needs_review', 'failed') then
        raise exception 'Invalid entity result' using errcode = '22023';
    end if;
    select * into attempt from public.entity_runs
        where id = p_run_id and owner_id = auth.uid() for update;
    if not found then
        raise exception 'Entity run not found' using errcode = 'P0002';
    end if;
    if attempt.status <> 'processing' then return false; end if;
    if p_status = 'failed' then
        update public.entity_runs set status = 'failed', finished_at = now(),
            error = 'Entity extraction could not finish. Please retry.' where id = p_run_id;
        return true;
    end if;
    if p_meta is null or jsonb_typeof(p_meta) <> 'object' or jsonb_typeof(p_mentions) <> 'array' then
        raise exception 'Invalid entity result' using errcode = '22023';
    end if;
    insert into public.entity_mentions(id, run_id, document_id, owner_id, passage_id,
        page_number, region_id, start_char, end_char, text, label, score,
        source_sha256, citation, warnings)
    select m.id, p_run_id, attempt.document_id, attempt.owner_id, m.passage_id,
        (m.citation ->> 'page_number')::integer, (m.citation ->> 'region_id')::uuid,
        (m.citation ->> 'start_char')::integer, (m.citation ->> 'end_char')::integer,
        m.text, m.label, m.score, m.source_sha256, m.citation,
        coalesce(array(select jsonb_array_elements_text(m.warnings)), '{}')
    from jsonb_to_recordset(p_mentions) as m(id uuid, passage_id uuid, text text, label text,
        score real, source_sha256 text, citation jsonb, warnings jsonb)
    where (m.citation ->> 'document_id') = attempt.document_id::text;
    if (select count(*) from public.entity_mentions where run_id = p_run_id) <> jsonb_array_length(p_mentions) then
        raise exception 'Entity result source mismatch' using errcode = '22023';
    end if;
    update public.entity_mentions n set
        review_status = prior.review_status,
        reviewed_label = prior.reviewed_label,
        reviewed_at = prior.reviewed_at
    from (
        select distinct on (region_id, start_char, end_char, label)
            region_id, start_char, end_char, label, review_status, reviewed_label, reviewed_at
        from public.entity_mentions
        where document_id = attempt.document_id and owner_id = attempt.owner_id
            and run_id <> p_run_id and review_status <> 'suggested'
        order by region_id, start_char, end_char, label, reviewed_at desc
    ) prior
    where n.run_id = p_run_id and n.region_id = prior.region_id
        and n.start_char = prior.start_char and n.end_char = prior.end_char
        and n.label = prior.label;
    update public.entity_runs set status = p_status, finished_at = now(),
        label_schema_version = p_meta ->> 'label_schema_version',
        model_repository = p_meta ->> 'model_repository',
        model_revision = p_meta ->> 'model_revision',
        library_version = p_meta ->> 'library_version',
        threshold = (p_meta ->> 'threshold')::real,
        labels = p_meta -> 'labels',
        passage_count = (p_meta ->> 'passage_count')::integer,
        failed_passage_count = (p_meta ->> 'failed_passage_count')::integer,
        rejected_candidate_count = (p_meta ->> 'rejected_candidate_count')::integer
        where id = p_run_id;
    return true;
end $$;

commit;
