begin;

-- Keyword search over a user's own passages, entities, and timeline items.
-- Every result carries the exact citation of its source. Reads are RLS-scoped; the
-- index is written only through an owner-checked function, and queries run through
-- owner-checked functions that never build SQL from user input.

create table public.search_index_state (
    document_id uuid primary key references public.documents(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    -- The document_extractions row the index was built from. A different current
    -- documents.processing_run_id means the index is stale.
    extraction_id uuid not null references public.document_extractions(id) on delete cascade,
    passage_count integer not null check (passage_count >= 0),
    indexed_at timestamptz not null default now()
);
alter table public.search_index_state enable row level security;
revoke all on public.search_index_state from public, anon, authenticated;
grant select on public.search_index_state to authenticated;
create policy search_index_state_read_own on public.search_index_state
    for select to authenticated using (owner_id = (select auth.uid()));

create table public.search_passages (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    document_id uuid not null references public.documents(id) on delete cascade,
    page_number integer not null check (page_number >= 1),
    region_id uuid not null,
    start_char integer not null check (start_char >= 0),
    end_char integer not null check (end_char > start_char),
    method text not null check (method in ('native', 'ocr')),
    text text not null check (char_length(btrim(text)) > 0),
    source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
    review_required boolean not null default false,
    citation jsonb not null,
    tsv tsvector generated always as (to_tsvector('english', text)) stored,
    check (end_char - start_char = char_length(text))
);
create index search_passages_tsv_idx on public.search_passages using gin (tsv);
create index search_passages_document_idx on public.search_passages(owner_id, document_id, page_number, start_char);
alter table public.search_passages enable row level security;
revoke all on public.search_passages from public, anon, authenticated;
grant select on public.search_passages to authenticated;
create policy search_passages_read_own on public.search_passages
    for select to authenticated using (owner_id = (select auth.uid()));

-- Replace one document's passages atomically and record which extraction they came from.
create function public.replace_search_passages(p_document_id uuid, p_extraction_id uuid, p_passages jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    d public.documents%rowtype;
    bad integer;
    written integer;
begin
    select * into d from public.documents where id = p_document_id and owner_id = auth.uid() for update;
    if not found then
        raise exception 'Document not found' using errcode = 'P0002';
    end if;
    -- Only the document's current extraction may be indexed.
    if d.processing_run_id is distinct from p_extraction_id
        or not exists (select 1 from public.document_extractions e
            where e.id = p_extraction_id and e.owner_id = auth.uid() and e.document_id = d.id
                and e.status in ('ready', 'needs_review')) then
        raise exception 'Extraction is not the current finished extraction' using errcode = '22023';
    end if;
    if p_passages is null or jsonb_typeof(p_passages) <> 'array' then
        raise exception 'Invalid passages' using errcode = '22023';
    end if;
    select count(*) into bad from jsonb_to_recordset(p_passages) as t(id uuid, page_number integer,
        region_id uuid, start_char integer, end_char integer, method text, text text,
        source_sha256 text, review_required boolean, citation jsonb)
    where t.citation is null
        or (t.citation ->> 'document_id') is distinct from p_document_id::text
        or (t.citation ->> 'page_number')::integer is distinct from t.page_number
        or (t.citation ->> 'region_id')::uuid is distinct from t.region_id
        or (t.citation ->> 'start_char')::integer is distinct from t.start_char
        or (t.citation ->> 'end_char')::integer is distinct from t.end_char
        or t.end_char - t.start_char is distinct from char_length(t.text);
    if bad > 0 then
        raise exception 'Passage citation does not match its text' using errcode = '22023';
    end if;
    delete from public.search_passages where document_id = d.id and owner_id = auth.uid();
    insert into public.search_passages(id, owner_id, document_id, page_number, region_id, start_char,
        end_char, method, text, source_sha256, review_required, citation)
    select t.id, d.owner_id, d.id, t.page_number, t.region_id, t.start_char, t.end_char, t.method,
        t.text, t.source_sha256, coalesce(t.review_required, false), t.citation
    from jsonb_to_recordset(p_passages) as t(id uuid, page_number integer, region_id uuid,
        start_char integer, end_char integer, method text, text text, source_sha256 text,
        review_required boolean, citation jsonb);
    get diagnostics written = row_count;
    insert into public.search_index_state(document_id, owner_id, extraction_id, passage_count)
        values (d.id, d.owner_id, p_extraction_id, written)
    on conflict (document_id) do update set extraction_id = excluded.extraction_id,
        passage_count = excluded.passage_count, indexed_at = now();
    return jsonb_build_object('passages', written);
end $$;

-- Ranked passage search. The snippet wraps matches in private-use markers (U+E000 / U+E001)
-- that the backend turns into highlight ranges, so no markup is ever returned.
create function public.search_passages_query(
    p_query text, p_document_id uuid default null, p_limit integer default 20, p_offset integer default 0
) returns table(id uuid, document_id uuid, page_number integer, citation jsonb, review_required boolean,
    rank real, snippet text, total bigint)
language plpgsql stable security definer set search_path = '' as $$
#variable_conflict use_column
declare
    q tsquery;
begin
    if auth.uid() is null then raise exception 'Sign in required' using errcode = '28000'; end if;
    if p_query is null or char_length(btrim(p_query)) = 0 or char_length(p_query) > 500 then
        raise exception 'Invalid query' using errcode = '22023';
    end if;
    if p_limit < 1 or p_limit > 100 or p_offset < 0 then
        raise exception 'Invalid paging' using errcode = '22023';
    end if;
    q := websearch_to_tsquery('english', p_query);
    if q::text = '' then return; end if;
    return query
    select s.id, s.document_id, s.page_number, s.citation, s.review_required,
        ts_rank_cd(s.tsv, q)::real,
        ts_headline('english', translate(s.text, chr(57344) || chr(57345), ''), q,
            'StartSel=' || chr(57344) || ', StopSel=' || chr(57345)
            || ', MaxFragments=2, MaxWords=35, MinWords=12, FragmentDelimiter= … '),
        count(*) over ()
    from public.search_passages s
    where s.owner_id = auth.uid() and s.tsv @@ q
        and (p_document_id is null or s.document_id = p_document_id)
    order by 6 desc, s.document_id, s.page_number, s.start_char
    limit p_limit offset p_offset;
end $$;

-- Entities: a canonical entity matches when its name, or the text of any mention linked to it,
-- contains every query word (case-insensitive substring; no pattern syntax from the caller).
create function public.search_entities_query(p_query text, p_limit integer default 20, p_offset integer default 0)
returns table(kind text, entity_id uuid, mention_id uuid, run_id uuid, document_id uuid, entity_type text,
    name text, matched text, status text, mention_count bigint, page_number integer,
    citation jsonb, total bigint)
language plpgsql stable security definer set search_path = '' as $$
#variable_conflict use_column
declare
    words text[];
begin
    if auth.uid() is null then raise exception 'Sign in required' using errcode = '28000'; end if;
    if p_query is null or char_length(btrim(p_query)) = 0 or char_length(p_query) > 500 then
        raise exception 'Invalid query' using errcode = '22023';
    end if;
    if p_limit < 1 or p_limit > 100 or p_offset < 0 then
        raise exception 'Invalid paging' using errcode = '22023';
    end if;
    words := array(select lower(w) from regexp_split_to_table(btrim(p_query), '\s+') w where w <> '');
    return query
    with candidates as (
        select 'entity'::text as kind, e.id as entity_id, null::uuid as mention_id, null::uuid as run_id,
            null::uuid as document_id, e.type as entity_type, e.canonical_name as name,
            (select n from (select e.canonical_name as n union all
                select m.text from public.entity_links l join public.entity_mentions m
                    on m.run_id = l.run_id and m.id = l.mention_id where l.entity_id = e.id) names
                where not exists (select 1 from unnest(words) w where strpos(lower(n), w) = 0)
                order by (n = e.canonical_name) desc, char_length(n) limit 1) as matched,
            e.status as status,
            (select count(*) from public.entity_links l where l.entity_id = e.id) as mention_count,
            null::integer as page_number, null::jsonb as citation
        from public.entities e
        where e.owner_id = auth.uid() and e.status <> 'merged'
        union all
        -- Accepted mentions of each document's latest run that no entity covers yet.
        select 'mention', null, m.id, m.run_id, m.document_id, coalesce(m.reviewed_label, m.label), m.text,
            m.text, 'accepted', null::bigint, m.page_number, m.citation
        from public.entity_mentions m
        where m.owner_id = auth.uid() and m.review_status = 'accepted'
            and m.run_id = public.latest_entity_run_id(m.document_id)
            and not exists (select 1 from public.entity_links l where l.run_id = m.run_id and l.mention_id = m.id)
            and not exists (select 1 from unnest(words) w where strpos(lower(m.text), w) = 0)
    )
    select c.kind, c.entity_id, c.mention_id, c.run_id, c.document_id, c.entity_type, c.name, c.matched,
        c.status, c.mention_count, c.page_number, c.citation, count(*) over ()
    from candidates c
    where c.matched is not null
    order by (c.kind = 'entity') desc, c.mention_count desc nulls last, c.name, c.mention_id
    limit p_limit offset p_offset;
end $$;

-- Timeline items (only when the timeline table exists, so this migration stands alone).
create function public.search_timeline_query(p_query text, p_limit integer default 20, p_offset integer default 0)
returns table(id uuid, document_id uuid, title text, date_text text, start_date date,
    page_number integer, review_status text, sentence_text text, total bigint)
language plpgsql stable security definer set search_path = '' as $$
#variable_conflict use_column
declare
    words text[];
begin
    if auth.uid() is null then raise exception 'Sign in required' using errcode = '28000'; end if;
    if p_query is null or char_length(btrim(p_query)) = 0 or char_length(p_query) > 500 then
        raise exception 'Invalid query' using errcode = '22023';
    end if;
    if p_limit < 1 or p_limit > 100 or p_offset < 0 then
        raise exception 'Invalid paging' using errcode = '22023';
    end if;
    if to_regclass('public.timeline_items') is null then return; end if;
    words := array(select lower(w) from regexp_split_to_table(btrim(p_query), '\s+') w where w <> '');
    return query execute $q$
        select i.id, i.document_id, coalesce(i.corrected_title, i.title), i.date_text,
            coalesce(i.corrected_start, i.start_date), i.page_number, i.review_status, i.sentence_text,
            count(*) over ()
        from public.timeline_items i
        where i.owner_id = auth.uid() and i.review_status <> 'rejected'
            and not exists (select 1 from unnest($1) w
                where strpos(lower(coalesce(i.corrected_title, i.title) || ' ' || i.sentence_text), w) = 0)
        order by coalesce(i.corrected_start, i.start_date) nulls last, i.id
        limit $2 offset $3$q$ using words, p_limit, p_offset;
end $$;

revoke all on function public.replace_search_passages(uuid, uuid, jsonb) from public, anon;
revoke all on function public.search_passages_query(text, uuid, integer, integer) from public, anon;
revoke all on function public.search_entities_query(text, integer, integer) from public, anon;
revoke all on function public.search_timeline_query(text, integer, integer) from public, anon;
grant execute on function public.replace_search_passages(uuid, uuid, jsonb) to authenticated;
grant execute on function public.search_passages_query(text, uuid, integer, integer) to authenticated;
grant execute on function public.search_entities_query(text, integer, integer) to authenticated;
grant execute on function public.search_timeline_query(text, integer, integer) to authenticated;

commit;
