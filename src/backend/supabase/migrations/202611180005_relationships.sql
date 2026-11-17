begin;

-- Relationship suggestions between two ACCEPTED mentions in one sentence. They stay
-- suggestions until a person confirms them, and only confirmed ones reach the graph.
create table public.relationships (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    type text not null check (type in ('works_at', 'located_in', 'attended', 'part_of', 'member_of', 'collaborated_with')),
    subject_run_id uuid not null,
    subject_mention_id uuid not null,
    object_run_id uuid not null,
    object_mention_id uuid not null,
    -- Display copies of both ends (label, exact text, citation), written by the builder.
    subject jsonb not null,
    object jsonb not null,
    page_number integer not null check (page_number >= 1),
    -- Exact slice of the preserved region text, with offsets into that region.
    sentence_text text not null,
    sentence_start integer not null check (sentence_start >= 0),
    sentence_end integer not null check (sentence_end > sentence_start),
    method text not null,
    score real not null check (score >= 0 and score <= 1),
    review_status text not null default 'suggested' check (review_status in ('suggested', 'confirmed', 'rejected')),
    -- A reviewer's corrections. The computed type and direction are never overwritten.
    corrected_type text check (corrected_type is null
        or corrected_type in ('works_at', 'located_in', 'attended', 'part_of', 'member_of', 'collaborated_with')),
    swapped boolean not null default false,
    -- True when a supporting mention is no longer accepted.
    stale boolean not null default false,
    reviewed_at timestamptz,
    created_at timestamptz not null default now(),
    foreign key (subject_run_id, subject_mention_id) references public.entity_mentions(run_id, id) on delete cascade,
    foreign key (object_run_id, object_mention_id) references public.entity_mentions(run_id, id) on delete cascade,
    check (subject_run_id <> object_run_id or subject_mention_id <> object_mention_id),
    check (sentence_end - sentence_start = char_length(sentence_text)),
    check ((review_status = 'suggested') = (reviewed_at is null)),
    check (review_status = 'confirmed' or (corrected_type is null and not swapped))
);
create index relationships_owner_document_idx on public.relationships(owner_id, document_id);
create index relationships_owner_status_idx on public.relationships(owner_id, review_status, type);
alter table public.relationships enable row level security;
revoke all on public.relationships from public, anon, authenticated;
grant select on public.relationships to authenticated;
create policy relationships_read_own on public.relationships
    for select to authenticated using (owner_id = (select auth.uid()));

-- Replace a document's computed candidates. Reviewed rows keep their decisions;
-- unsupported suggestions are deleted and unsupported reviewed rows are marked stale.
create function public.replace_relationships(p_document_id uuid, p_items jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    d public.documents%rowtype;
    foreign_items integer;
    unsupported integer;
    n_written integer;
    n_removed integer;
    n_stale integer;
    ids uuid[];
begin
    select * into d from public.documents where id = p_document_id and owner_id = auth.uid() for update;
    if not found then
        raise exception 'Document not found' using errcode = 'P0002';
    end if;
    if p_items is null or jsonb_typeof(p_items) <> 'array' then
        raise exception 'Invalid relationships' using errcode = '22023';
    end if;
    select count(*) into foreign_items from jsonb_array_elements(p_items) i
        where (i ->> 'document_id') is distinct from p_document_id::text;
    if foreign_items > 0 then
        raise exception 'Relationship belongs to another document' using errcode = '22023';
    end if;
    -- Both ends must be accepted mentions of this document owned by the caller.
    select count(*) into unsupported
    from jsonb_array_elements(p_items) i, unnest(array['subject', 'object']) as side(name)
    where not exists (
        select 1 from public.entity_mentions m
        where m.run_id = ((i -> side.name) ->> 'run_id')::uuid and m.id = ((i -> side.name) ->> 'mention_id')::uuid
            and m.owner_id = auth.uid() and m.document_id = p_document_id and m.review_status = 'accepted');
    if unsupported > 0 then
        raise exception 'Relationship cites a mention that is not accepted' using errcode = '22023';
    end if;
    ids := array(select (i ->> 'id')::uuid from jsonb_array_elements(p_items) i);

    insert into public.relationships(id, owner_id, document_id, type, subject_run_id, subject_mention_id,
        object_run_id, object_mention_id, subject, object, page_number, sentence_text, sentence_start,
        sentence_end, method, score)
    select t.id, d.owner_id, d.id, t.type, (t.subject ->> 'run_id')::uuid, (t.subject ->> 'mention_id')::uuid,
        (t.object ->> 'run_id')::uuid, (t.object ->> 'mention_id')::uuid, t.subject, t.object, t.page_number,
        t.sentence_text, t.sentence_start, t.sentence_end, t.method, t.score
    from jsonb_to_recordset(p_items) as t(id uuid, type text, subject jsonb, object jsonb, page_number integer,
        sentence_text text, sentence_start integer, sentence_end integer, method text, score real)
    on conflict (id) do update set
        type = excluded.type, subject = excluded.subject, object = excluded.object,
        page_number = excluded.page_number, sentence_text = excluded.sentence_text,
        sentence_start = excluded.sentence_start, sentence_end = excluded.sentence_end,
        method = excluded.method, score = excluded.score, stale = false
    where public.relationships.owner_id = auth.uid() and public.relationships.review_status = 'suggested';
    get diagnostics n_written = row_count;

    -- Reviewed rows keep their decision; they are only marked current again.
    update public.relationships r set stale = false
        where r.document_id = d.id and r.owner_id = auth.uid() and r.review_status <> 'suggested' and r.id = any(ids);

    delete from public.relationships r
        where r.document_id = d.id and r.owner_id = auth.uid() and r.review_status = 'suggested' and not (r.id = any(ids));
    get diagnostics n_removed = row_count;

    update public.relationships r set stale = true
        where r.document_id = d.id and r.owner_id = auth.uid() and r.review_status <> 'suggested' and not (r.id = any(ids));
    get diagnostics n_stale = row_count;

    return jsonb_build_object('written', n_written, 'removed', n_removed, 'stale', n_stale);
end $$;

create function public.review_relationship(
    p_id uuid, p_status text, p_type text default null, p_swap boolean default false
) returns setof public.relationships language plpgsql security definer set search_path = '' as $$
begin
    if p_status not in ('suggested', 'confirmed', 'rejected') then
        raise exception 'Invalid review status' using errcode = '22023';
    end if;
    if p_status <> 'confirmed' and (p_type is not null or coalesce(p_swap, false)) then
        raise exception 'Only a confirmed relationship can be corrected' using errcode = '22023';
    end if;
    return query update public.relationships r set
        review_status = p_status,
        corrected_type = case when p_status = 'confirmed' and p_type is distinct from r.type then p_type end,
        swapped = p_status = 'confirmed' and coalesce(p_swap, false),
        reviewed_at = case when p_status = 'suggested' then null else now() end
        where r.id = p_id and r.owner_id = auth.uid()
        returning r.*;
    if not found then
        raise exception 'Relationship not found' using errcode = 'P0002';
    end if;
end $$;

revoke all on function public.replace_relationships(uuid, jsonb) from public, anon;
revoke all on function public.review_relationship(uuid, text, text, boolean) from public, anon;
grant execute on function public.replace_relationships(uuid, jsonb) to authenticated;
grant execute on function public.review_relationship(uuid, text, text, boolean) to authenticated;

commit;
