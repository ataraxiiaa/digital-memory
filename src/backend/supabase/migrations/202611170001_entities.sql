begin;

-- One run is one pass of the local model over one saved extraction. Mentions are
-- suggestions until a person reviews them; nothing here merges identities.
create table public.entity_runs (
    id uuid primary key,
    document_id uuid not null references public.documents(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    extraction_id uuid not null references public.document_extractions(id) on delete cascade,
    status text not null check (status in ('processing', 'completed', 'needs_review', 'failed')),
    label_schema_version text,
    model_repository text,
    model_revision text,
    library_version text,
    threshold real check (threshold is null or (threshold >= 0 and threshold <= 1)),
    labels jsonb,
    passage_count integer check (passage_count is null or passage_count >= 0),
    failed_passage_count integer check (failed_passage_count is null or failed_passage_count >= 0),
    rejected_candidate_count integer check (rejected_candidate_count is null or rejected_candidate_count >= 0),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    error text,
    check ((status = 'processing' and finished_at is null)
        or (status <> 'processing' and finished_at is not null))
);
create index entity_runs_document_idx on public.entity_runs(document_id, started_at desc);
alter table public.entity_runs enable row level security;
revoke all on public.entity_runs from public, anon, authenticated;
grant select on public.entity_runs to authenticated;
create policy entity_runs_read_own on public.entity_runs
    for select to authenticated using (owner_id = (select auth.uid()));

create table public.entity_mentions (
    id uuid not null,
    run_id uuid not null references public.entity_runs(id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    passage_id uuid not null,
    page_number integer not null check (page_number >= 1),
    region_id uuid not null,
    -- Exact end-exclusive offsets into the preserved region text.
    start_char integer not null check (start_char >= 0),
    end_char integer not null check (end_char > start_char),
    text text not null check (char_length(btrim(text)) > 0),
    label text not null check (label in ('person', 'organization', 'project', 'model',
        'method', 'dataset', 'component', 'date', 'event', 'location')),
    score real not null check (score >= 0 and score <= 1),
    source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
    citation jsonb not null,
    warnings text[] not null default '{}',
    review_status text not null default 'suggested'
        check (review_status in ('suggested', 'accepted', 'rejected')),
    -- A reviewer's corrected type. The model's original label is never overwritten.
    reviewed_label text check (reviewed_label is null or reviewed_label in ('person',
        'organization', 'project', 'model', 'method', 'dataset', 'component',
        'date', 'event', 'location')),
    reviewed_at timestamptz,
    primary key (run_id, id),
    check (review_status = 'accepted' or reviewed_label is null),
    check ((review_status = 'suggested') = (reviewed_at is null))
);
create index entity_mentions_document_idx on public.entity_mentions(run_id, page_number, start_char);
create index entity_mentions_review_idx on public.entity_mentions(run_id, review_status, label);
alter table public.entity_mentions enable row level security;
revoke all on public.entity_mentions from public, anon, authenticated;
grant select on public.entity_mentions to authenticated;
create policy entity_mentions_read_own on public.entity_mentions
    for select to authenticated using (owner_id = (select auth.uid()));

create function public.start_entity_run(p_document_id uuid, p_run_id uuid) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
    d public.documents%rowtype;
    existing public.entity_runs%rowtype;
begin
    select * into d from public.documents
        where id = p_document_id and owner_id = auth.uid() for update;
    if not found or d.processing_run_id is null or d.status not in ('ready', 'needs_review') then
        raise exception 'Extraction not found' using errcode = 'P0002';
    end if;
    select * into existing from public.entity_runs
        where document_id = d.id and status = 'processing' order by started_at desc limit 1;
    if found then
        if existing.started_at > now() - interval '20 minutes' then
            raise exception 'Entity extraction already active' using errcode = '55000';
        end if;
        update public.entity_runs set status = 'failed', finished_at = now(),
            error = 'Entity extraction was interrupted. Please retry.' where id = existing.id;
    end if;
    insert into public.entity_runs(id, document_id, owner_id, extraction_id, status)
        values (p_run_id, d.id, d.owner_id, d.processing_run_id, 'processing');
    return p_run_id;
end $$;

create function public.finish_entity_run(
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

create function public.review_entity_mention(
    p_run_id uuid, p_mention_id uuid, p_status text, p_label text default null
) returns setof public.entity_mentions language plpgsql security definer set search_path = '' as $$
begin
    if p_status not in ('suggested', 'accepted', 'rejected') then
        raise exception 'Invalid review status' using errcode = '22023';
    end if;
    if p_label is not null and p_status <> 'accepted' then
        raise exception 'Only an accepted mention can change type' using errcode = '22023';
    end if;
    return query update public.entity_mentions m set
        review_status = p_status,
        reviewed_label = case when p_status = 'accepted' then p_label else null end,
        reviewed_at = case when p_status = 'suggested' then null else now() end
        where m.run_id = p_run_id and m.id = p_mention_id and m.owner_id = auth.uid()
        returning m.*;
    if not found then
        raise exception 'Entity mention not found' using errcode = 'P0002';
    end if;
end $$;

revoke all on function public.start_entity_run(uuid, uuid) from public, anon;
revoke all on function public.finish_entity_run(uuid, text, jsonb, jsonb) from public, anon;
revoke all on function public.review_entity_mention(uuid, uuid, text, text) from public, anon;
grant execute on function public.start_entity_run(uuid, uuid) to authenticated;
grant execute on function public.finish_entity_run(uuid, text, jsonb, jsonb) to authenticated;
grant execute on function public.review_entity_mention(uuid, uuid, text, text) to authenticated;

commit;
