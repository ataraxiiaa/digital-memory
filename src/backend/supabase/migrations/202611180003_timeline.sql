begin;

-- Timeline items pair an accepted event with the date, places and people in its
-- sentence. They are suggestions until reviewed, and each keeps exact citations.
create table public.timeline_items (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    title text not null check (char_length(btrim(title)) > 0),
    page_number integer not null check (page_number >= 1),
    -- Exact slice of the preserved region text, with offsets into that region.
    sentence_text text not null,
    sentence_start integer not null check (sentence_start >= 0),
    sentence_end integer not null check (sentence_end > sentence_start),
    date_text text,
    date_status text not null check (date_status in ('resolved', 'needs_anchor', 'ambiguous', 'unparsed', 'none')),
    start_date date,
    end_date date,
    precision text check (precision in ('day', 'week', 'month', 'quarter', 'year')),
    flags text[] not null default '{}',
    anchor_date date,
    refs jsonb not null check (jsonb_typeof(refs) = 'array'
        and jsonb_path_exists(refs, '$[*] ? (@.role == "event")')),
    review_status text not null default 'suggested' check (review_status in ('suggested', 'confirmed', 'rejected')),
    -- A reviewer's corrections. The computed values above are never overwritten.
    corrected_title text check (corrected_title is null or char_length(btrim(corrected_title)) > 0),
    corrected_start date,
    corrected_end date,
    corrected_precision text check (corrected_precision is null
        or corrected_precision in ('day', 'week', 'month', 'quarter', 'year')),
    -- True when the supporting accepted mentions no longer produce this item.
    stale boolean not null default false,
    reviewed_at timestamptz,
    created_at timestamptz not null default now(),
    check (sentence_end - sentence_start = char_length(sentence_text)),
    check ((date_status = 'resolved') = (start_date is not null)),
    check ((start_date is null) = (end_date is null) and (end_date is null or end_date >= start_date)),
    check (start_date is null or precision is not null),
    check ((corrected_start is null) = (corrected_end is null)
        and (corrected_end is null or corrected_end >= corrected_start)),
    check (corrected_start is null or corrected_precision is not null),
    check ((review_status = 'suggested') = (reviewed_at is null)),
    check (review_status = 'confirmed' or (corrected_title is null and corrected_start is null))
);
create index timeline_items_owner_document_idx on public.timeline_items(owner_id, document_id);
create index timeline_items_owner_date_idx on public.timeline_items(owner_id, (coalesce(corrected_start, start_date)));
alter table public.timeline_items enable row level security;
revoke all on public.timeline_items from public, anon, authenticated;
grant select on public.timeline_items to authenticated;
create policy timeline_items_read_own on public.timeline_items
    for select to authenticated using (owner_id = (select auth.uid()));

-- Replace a document's computed items. Reviewed items keep their decisions; items
-- that are no longer supported are deleted if still suggestions, else marked stale.
create function public.replace_timeline_items(p_document_id uuid, p_items jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    d public.documents%rowtype;
    unsupported integer;
    foreign_items integer;
    n_changed integer;
    n_removed integer;
    n_stale integer;
    ids uuid[];
begin
    select * into d from public.documents where id = p_document_id and owner_id = auth.uid() for update;
    if not found then
        raise exception 'Document not found' using errcode = 'P0002';
    end if;
    if p_items is null or jsonb_typeof(p_items) <> 'array' then
        raise exception 'Invalid timeline items' using errcode = '22023';
    end if;
    select count(*) into foreign_items from jsonb_array_elements(p_items) i
        where (i ->> 'document_id') is distinct from p_document_id::text;
    if foreign_items > 0 then
        raise exception 'Timeline item belongs to another document' using errcode = '22023';
    end if;
    -- Every cited mention must be an accepted mention of this document owned by the caller.
    select count(*) into unsupported
    from jsonb_array_elements(p_items) i, jsonb_array_elements(i -> 'refs') r
    where not exists (
        select 1 from public.entity_mentions m
        where m.run_id = (r ->> 'run_id')::uuid and m.id = (r ->> 'mention_id')::uuid
            and m.owner_id = auth.uid() and m.document_id = p_document_id
            and m.review_status = 'accepted');
    if unsupported > 0 then
        raise exception 'Timeline item cites a mention that is not accepted' using errcode = '22023';
    end if;

    ids := array(select (i ->> 'id')::uuid from jsonb_array_elements(p_items) i);

    insert into public.timeline_items(id, owner_id, document_id, title, page_number, sentence_text,
        sentence_start, sentence_end, date_text, date_status, start_date, end_date, precision,
        flags, anchor_date, refs)
    select t.id, d.owner_id, d.id, t.title, t.page_number, t.sentence_text, t.sentence_start,
        t.sentence_end, t.date_text, t.date_status, t.start_date, t.end_date, t.precision,
        coalesce(array(select jsonb_array_elements_text(t.flags)), '{}'), t.anchor_date, t.refs
    from jsonb_to_recordset(p_items) as t(id uuid, title text, page_number integer,
        sentence_text text, sentence_start integer, sentence_end integer, date_text text,
        date_status text, start_date date, end_date date, precision text, flags jsonb,
        anchor_date date, refs jsonb)
    on conflict (id) do update set
        title = excluded.title, page_number = excluded.page_number, sentence_text = excluded.sentence_text,
        sentence_start = excluded.sentence_start, sentence_end = excluded.sentence_end,
        date_text = excluded.date_text, date_status = excluded.date_status,
        start_date = excluded.start_date, end_date = excluded.end_date, precision = excluded.precision,
        flags = excluded.flags, anchor_date = excluded.anchor_date, refs = excluded.refs, stale = false
    where public.timeline_items.owner_id = auth.uid() and public.timeline_items.review_status = 'suggested';
    get diagnostics n_changed = row_count;

    -- Reviewed items keep their decision; they are only marked current again.
    update public.timeline_items i set stale = false
        where i.document_id = d.id and i.owner_id = auth.uid() and i.review_status <> 'suggested'
            and i.id = any(ids);

    delete from public.timeline_items i
        where i.document_id = d.id and i.owner_id = auth.uid() and i.review_status = 'suggested'
            and not (i.id = any(ids));
    get diagnostics n_removed = row_count;

    update public.timeline_items i set stale = true
        where i.document_id = d.id and i.owner_id = auth.uid() and i.review_status <> 'suggested'
            and not (i.id = any(ids));
    get diagnostics n_stale = row_count;

    return jsonb_build_object('written', n_changed, 'removed', n_removed, 'stale', n_stale);
end $$;

create function public.review_timeline_item(
    p_id uuid, p_status text, p_title text default null,
    p_start date default null, p_end date default null, p_precision text default null
) returns setof public.timeline_items language plpgsql security definer set search_path = '' as $$
begin
    if p_status not in ('suggested', 'confirmed', 'rejected') then
        raise exception 'Invalid review status' using errcode = '22023';
    end if;
    if p_status <> 'confirmed' and (p_title is not null or p_start is not null) then
        raise exception 'Only a confirmed item can be corrected' using errcode = '22023';
    end if;
    return query update public.timeline_items i set
        review_status = p_status,
        corrected_title = case when p_status = 'confirmed' then nullif(btrim(p_title), '') end,
        corrected_start = case when p_status = 'confirmed' then p_start end,
        corrected_end = case when p_status = 'confirmed' then p_end end,
        corrected_precision = case when p_status = 'confirmed' and p_start is not null then p_precision end,
        reviewed_at = case when p_status = 'suggested' then null else now() end
        where i.id = p_id and i.owner_id = auth.uid()
        returning i.*;
    if not found then
        raise exception 'Timeline item not found' using errcode = 'P0002';
    end if;
end $$;

revoke all on function public.replace_timeline_items(uuid, jsonb) from public, anon;
revoke all on function public.review_timeline_item(uuid, text, text, date, date, text) from public, anon;
grant execute on function public.replace_timeline_items(uuid, jsonb) to authenticated;
grant execute on function public.review_timeline_item(uuid, text, text, date, date, text) to authenticated;

commit;
