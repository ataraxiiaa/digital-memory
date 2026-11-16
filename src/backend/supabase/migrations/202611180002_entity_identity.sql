begin;

-- Canonical entities span documents and belong to one user. Only ACCEPTED
-- mentions from a document's latest completed entity run are linked to them.
create table public.entities (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    type text not null check (type in ('person', 'organization', 'project', 'model',
        'method', 'dataset', 'component', 'date', 'event', 'location')),
    canonical_name text not null check (char_length(btrim(canonical_name)) > 0),
    -- suggested: created by exact-text grouping; confirmed: a person approved,
    -- renamed, or created it; merged: absorbed into merged_into (kept for undo).
    status text not null default 'suggested' check (status in ('suggested', 'confirmed', 'merged')),
    merged_into uuid references public.entities(id),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check ((status = 'merged') = (merged_into is not null)),
    check (merged_into is null or merged_into <> id)
);
create index entities_owner_idx on public.entities(owner_id, type, canonical_name);

create table public.entity_links (
    run_id uuid not null,
    mention_id uuid not null,
    document_id uuid not null references public.documents(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    entity_id uuid not null references public.entities(id) on delete cascade,
    method text not null check (method in ('exact', 'manual', 'split', 'merge')),
    linked_at timestamptz not null default now(),
    primary key (run_id, mention_id),
    foreign key (run_id, mention_id) references public.entity_mentions(run_id, id) on delete cascade
);
create index entity_links_entity_idx on public.entity_links(entity_id);
create index entity_links_document_idx on public.entity_links(document_id);

create table public.entity_merges (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    keep_id uuid not null references public.entities(id) on delete cascade,
    absorbed_id uuid not null references public.entities(id) on delete cascade,
    absorbed_name text not null,
    absorbed_status text not null check (absorbed_status in ('suggested', 'confirmed')),
    -- Links that moved, so the merge can be undone without losing any evidence.
    moved jsonb not null default '[]'::jsonb,
    merged_at timestamptz not null default now(),
    undone_at timestamptz
);
create index entity_merges_keep_idx on public.entity_merges(keep_id);

create table public.merge_suggestions (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    entity_a uuid not null references public.entities(id) on delete cascade,
    entity_b uuid not null references public.entities(id) on delete cascade,
    kind text not null check (kind in ('exact', 'initial', 'surname', 'related')),
    score real not null check (score > 0 and score <= 1),
    ambiguous boolean not null default false,
    reason text not null,
    status text not null default 'pending' check (status in ('pending', 'accepted', 'dismissed')),
    created_at timestamptz not null default now(),
    decided_at timestamptz,
    check (entity_a < entity_b),
    unique (entity_a, entity_b)
);
create index merge_suggestions_owner_idx on public.merge_suggestions(owner_id, status, score desc);

alter table public.entities enable row level security;
alter table public.entity_links enable row level security;
alter table public.entity_merges enable row level security;
alter table public.merge_suggestions enable row level security;
revoke all on public.entities, public.entity_links, public.entity_merges, public.merge_suggestions
    from public, anon, authenticated;
grant select on public.entities, public.entity_links, public.entity_merges, public.merge_suggestions to authenticated;
create policy entities_read_own on public.entities for select to authenticated using (owner_id = (select auth.uid()));
create policy entity_links_read_own on public.entity_links for select to authenticated using (owner_id = (select auth.uid()));
create policy entity_merges_read_own on public.entity_merges for select to authenticated using (owner_id = (select auth.uid()));
create policy merge_suggestions_read_own on public.merge_suggestions for select to authenticated using (owner_id = (select auth.uid()));

create function public.latest_entity_run_id(p_document uuid) returns uuid
language sql stable security definer set search_path = '' as $$
    select id from public.entity_runs
    where document_id = p_document and owner_id = auth.uid() and status in ('completed', 'needs_review')
    order by started_at desc limit 1
$$;

-- Internal: suggestions from a plan that name two live entities of one type owned by p_owner.
create function public.entity_plan_pairs(p_suggestions jsonb, p_owner uuid)
returns table(a uuid, b uuid, kind text, score real, ambiguous boolean, reason text)
language sql stable security definer set search_path = '' as $$
    select least(x.entity_a, x.entity_b), greatest(x.entity_a, x.entity_b),
        x.kind, x.score, coalesce(x.ambiguous, false), x.reason
    from jsonb_to_recordset(p_suggestions) as x(entity_a uuid, entity_b uuid, kind text, score real,
        ambiguous boolean, reason text)
    join public.entities ea on ea.id = x.entity_a and ea.owner_id = p_owner and ea.status <> 'merged'
    join public.entities eb on eb.id = x.entity_b and eb.owner_id = p_owner and eb.status <> 'merged'
        and eb.type = ea.type
    where x.entity_a <> x.entity_b
$$;

-- Step 1 of a resolve: bring existing links in line with the current reviews.
create function public.refresh_entity_links() returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
    repointed integer; dropped integer; orphaned integer;
begin
    if uid is null then raise exception 'Sign in required' using errcode = '28000'; end if;

    -- A re-run creates new mention rows. Move existing links (including manual
    -- choices) onto the equivalent mention of the document's latest run.
    update public.entity_links l set run_id = nm.run_id, mention_id = nm.id
    from public.entity_mentions om, public.entity_mentions nm
    where l.owner_id = uid and om.run_id = l.run_id and om.id = l.mention_id
        and nm.run_id = public.latest_entity_run_id(l.document_id)
        and nm.run_id <> l.run_id and nm.region_id = om.region_id
        and nm.start_char = om.start_char and nm.end_char = om.end_char and nm.label = om.label
        and nm.review_status = 'accepted'
        and not exists (select 1 from public.entity_links x where x.run_id = nm.run_id and x.mention_id = nm.id);
    get diagnostics repointed = row_count;

    -- Drop links whose mention is stale, no longer accepted, or retyped.
    delete from public.entity_links l
    where l.owner_id = uid and not exists (
        select 1 from public.entity_mentions m join public.entities e on e.id = l.entity_id
        where m.run_id = l.run_id and m.id = l.mention_id and m.review_status = 'accepted'
            and coalesce(m.reviewed_label, m.label) = e.type
            and m.run_id = public.latest_entity_run_id(l.document_id));
    get diagnostics dropped = row_count;

    delete from public.entities e
    where e.owner_id = uid and e.status = 'suggested'
        and not exists (select 1 from public.entity_links l where l.entity_id = e.id);
    get diagnostics orphaned = row_count;
    return jsonb_build_object('relinked', repointed, 'dropped_links', dropped, 'removed_empty_entities', orphaned);
end $$;

-- Step 2: apply a plan computed by the backend matcher atomically. Every row is
-- re-validated here against the caller's own data.
create function public.apply_entity_resolution(
    p_entities jsonb, p_links jsonb, p_suggestions jsonb
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
    new_entities integer; new_links integer; kept_suggestions integer;
begin
    if uid is null then raise exception 'Sign in required' using errcode = '28000'; end if;
    if jsonb_typeof(p_entities) <> 'array' or jsonb_typeof(p_links) <> 'array'
        or jsonb_typeof(p_suggestions) <> 'array' then
        raise exception 'Invalid resolution plan' using errcode = '22023';
    end if;

    insert into public.entities(id, owner_id, type, canonical_name)
    select x.id, uid, x.type, btrim(x.canonical_name)
    from jsonb_to_recordset(p_entities) as x(id uuid, type text, canonical_name text)
    on conflict (id) do nothing;
    get diagnostics new_entities = row_count;

    insert into public.entity_links(run_id, mention_id, document_id, owner_id, entity_id, method)
    select x.run_id, x.mention_id, m.document_id, uid, x.entity_id, 'exact'
    from jsonb_to_recordset(p_links) as x(entity_id uuid, run_id uuid, mention_id uuid)
    join public.entities e on e.id = x.entity_id and e.owner_id = uid and e.status <> 'merged'
    join public.entity_mentions m on m.run_id = x.run_id and m.id = x.mention_id and m.owner_id = uid
        and m.review_status = 'accepted' and coalesce(m.reviewed_label, m.label) = e.type
        and m.run_id = public.latest_entity_run_id(m.document_id)
    on conflict (run_id, mention_id) do nothing;
    get diagnostics new_links = row_count;

    -- The plan holds every current suggestion: refresh pending ones, keep decided ones.
    delete from public.merge_suggestions s
    where s.owner_id = uid and s.status = 'pending'
        and not exists (select 1 from public.entity_plan_pairs(p_suggestions, uid) p
                        where p.a = s.entity_a and p.b = s.entity_b);
    insert into public.merge_suggestions(owner_id, entity_a, entity_b, kind, score, ambiguous, reason)
    select uid, p.a, p.b, p.kind, p.score, p.ambiguous, p.reason from public.entity_plan_pairs(p_suggestions, uid) p
    on conflict (entity_a, entity_b) do update set kind = excluded.kind, score = excluded.score,
        ambiguous = excluded.ambiguous, reason = excluded.reason
        where public.merge_suggestions.status = 'pending';
    select count(*) into kept_suggestions from public.merge_suggestions where owner_id = uid and status = 'pending';

    return jsonb_build_object('new_entities', new_entities, 'new_links', new_links,
        'pending_suggestions', kept_suggestions);
end $$;

create function public.rename_entity(p_id uuid, p_name text) returns void
language plpgsql security definer set search_path = '' as $$
begin
    if p_name is null or char_length(btrim(p_name)) = 0 or char_length(p_name) > 300 then
        raise exception 'Invalid name' using errcode = '22023';
    end if;
    update public.entities set canonical_name = btrim(p_name), updated_at = now(),
        status = case when status = 'suggested' then 'confirmed' else status end
    where id = p_id and owner_id = auth.uid() and status <> 'merged';
    if not found then raise exception 'Entity not found' using errcode = 'P0002'; end if;
end $$;

create function public.confirm_entity(p_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
    update public.entities set status = 'confirmed', updated_at = now()
    where id = p_id and owner_id = auth.uid() and status = 'suggested';
    if not found then
        if exists (select 1 from public.entities where id = p_id and owner_id = auth.uid() and status = 'confirmed') then
            return;
        end if;
        raise exception 'Entity not found' using errcode = 'P0002';
    end if;
end $$;

create function public.merge_entities(p_keep uuid, p_absorb uuid) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
    keep public.entities%rowtype; gone public.entities%rowtype; merge_id uuid;
begin
    if p_keep = p_absorb then raise exception 'Choose two different entities' using errcode = '22023'; end if;
    -- Lock in a fixed order so concurrent opposite merges cannot deadlock.
    perform 1 from public.entities where id in (p_keep, p_absorb) and owner_id = uid order by id for update;
    select * into keep from public.entities where id = p_keep and owner_id = uid;
    select * into gone from public.entities where id = p_absorb and owner_id = uid;
    if keep.id is null or gone.id is null or keep.status = 'merged' or gone.status = 'merged' then
        raise exception 'Entity not found' using errcode = 'P0002';
    end if;
    if keep.type <> gone.type then
        raise exception 'Entities of different types cannot merge' using errcode = '22023';
    end if;
    insert into public.entity_merges(owner_id, keep_id, absorbed_id, absorbed_name, absorbed_status, moved)
    values (uid, keep.id, gone.id, gone.canonical_name, gone.status,
        coalesce((select jsonb_agg(jsonb_build_object('run_id', run_id, 'mention_id', mention_id))
                  from public.entity_links where entity_id = gone.id), '[]'::jsonb))
    returning id into merge_id;
    update public.entity_links set entity_id = keep.id, method = 'merge' where entity_id = gone.id;
    update public.entities set status = 'merged', merged_into = keep.id, updated_at = now() where id = gone.id;
    update public.entities set status = 'confirmed', updated_at = now() where id = keep.id;
    update public.merge_suggestions set status = 'accepted', decided_at = now()
        where entity_a = least(keep.id, gone.id) and entity_b = greatest(keep.id, gone.id);
    delete from public.merge_suggestions
        where owner_id = uid and status = 'pending' and (entity_a = gone.id or entity_b = gone.id);
    return merge_id;
end $$;

create function public.undo_merge(p_merge_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
    m public.entity_merges%rowtype;
begin
    select * into m from public.entity_merges where id = p_merge_id and owner_id = uid for update;
    if not found or m.undone_at is not null then raise exception 'Merge not found' using errcode = 'P0002'; end if;
    perform 1 from public.entities where id = m.absorbed_id and owner_id = uid and merged_into = m.keep_id for update;
    if not found then raise exception 'Merge cannot be undone' using errcode = '22023'; end if;
    update public.entity_links l set entity_id = m.absorbed_id, method = 'manual'
    where l.entity_id = m.keep_id and exists (
        select 1 from jsonb_to_recordset(m.moved) as x(run_id uuid, mention_id uuid)
        where x.run_id = l.run_id and x.mention_id = l.mention_id);
    update public.entities set status = m.absorbed_status, merged_into = null, updated_at = now()
        where id = m.absorbed_id;
    update public.entity_merges set undone_at = now() where id = m.id;
    update public.merge_suggestions set status = 'pending', decided_at = null
        where entity_a = least(m.keep_id, m.absorbed_id) and entity_b = greatest(m.keep_id, m.absorbed_id);
end $$;

-- Shared by link/split: validate an accepted, latest-run mention owned by the caller.
create function public.link_mention(p_run_id uuid, p_mention_id uuid, p_entity_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
    m public.entity_mentions%rowtype; e public.entities%rowtype; previous uuid;
begin
    select * into m from public.entity_mentions where run_id = p_run_id and id = p_mention_id and owner_id = uid;
    select * into e from public.entities where id = p_entity_id and owner_id = uid and status <> 'merged';
    if m.id is null or e.id is null then raise exception 'Not found' using errcode = 'P0002'; end if;
    if m.review_status <> 'accepted' or m.run_id is distinct from public.latest_entity_run_id(m.document_id) then
        raise exception 'Only accepted mentions from the latest run can be linked' using errcode = '22023';
    end if;
    if coalesce(m.reviewed_label, m.label) <> e.type then
        raise exception 'A mention can only join an entity of its own type' using errcode = '22023';
    end if;
    select entity_id into previous from public.entity_links where run_id = p_run_id and mention_id = p_mention_id;
    insert into public.entity_links(run_id, mention_id, document_id, owner_id, entity_id, method)
    values (p_run_id, p_mention_id, m.document_id, uid, e.id, 'manual')
    on conflict (run_id, mention_id) do update set entity_id = excluded.entity_id, method = 'manual', linked_at = now();
    delete from public.entities x where x.id = previous and x.owner_id = uid and x.status = 'suggested'
        and not exists (select 1 from public.entity_links l where l.entity_id = x.id);
end $$;

create function public.split_mention(p_run_id uuid, p_mention_id uuid, p_new_id uuid) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
    m public.entity_mentions%rowtype; previous uuid;
begin
    select * into m from public.entity_mentions where run_id = p_run_id and id = p_mention_id and owner_id = uid;
    if m.id is null then raise exception 'Not found' using errcode = 'P0002'; end if;
    if m.review_status <> 'accepted' or m.run_id is distinct from public.latest_entity_run_id(m.document_id) then
        raise exception 'Only accepted mentions from the latest run can be split' using errcode = '22023';
    end if;
    select entity_id into previous from public.entity_links where run_id = p_run_id and mention_id = p_mention_id;
    insert into public.entities(id, owner_id, type, canonical_name, status)
    values (p_new_id, uid, coalesce(m.reviewed_label, m.label), btrim(regexp_replace(m.text, '\s+', ' ', 'g')), 'confirmed');
    insert into public.entity_links(run_id, mention_id, document_id, owner_id, entity_id, method)
    values (p_run_id, p_mention_id, m.document_id, uid, p_new_id, 'split')
    on conflict (run_id, mention_id) do update set entity_id = excluded.entity_id, method = 'split', linked_at = now();
    delete from public.entities x where x.id = previous and x.owner_id = uid and x.status = 'suggested'
        and not exists (select 1 from public.entity_links l where l.entity_id = x.id);
    return p_new_id;
end $$;

create function public.dismiss_merge_suggestion(p_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
    update public.merge_suggestions set status = 'dismissed', decided_at = now()
    where id = p_id and owner_id = auth.uid() and status = 'pending';
    if not found then raise exception 'Suggestion not found' using errcode = 'P0002'; end if;
end $$;

revoke all on function public.latest_entity_run_id(uuid) from public, anon, authenticated;
revoke all on function public.entity_plan_pairs(jsonb, uuid) from public, anon, authenticated;
revoke all on function public.refresh_entity_links() from public, anon;
revoke all on function public.apply_entity_resolution(jsonb, jsonb, jsonb) from public, anon;
revoke all on function public.rename_entity(uuid, text) from public, anon;
revoke all on function public.confirm_entity(uuid) from public, anon;
revoke all on function public.merge_entities(uuid, uuid) from public, anon;
revoke all on function public.undo_merge(uuid) from public, anon;
revoke all on function public.link_mention(uuid, uuid, uuid) from public, anon;
revoke all on function public.split_mention(uuid, uuid, uuid) from public, anon;
revoke all on function public.dismiss_merge_suggestion(uuid) from public, anon;
grant execute on function public.refresh_entity_links() to authenticated;
grant execute on function public.apply_entity_resolution(jsonb, jsonb, jsonb) to authenticated;
grant execute on function public.rename_entity(uuid, text) to authenticated;
grant execute on function public.confirm_entity(uuid) to authenticated;
grant execute on function public.merge_entities(uuid, uuid) to authenticated;
grant execute on function public.undo_merge(uuid) to authenticated;
grant execute on function public.link_mention(uuid, uuid, uuid) to authenticated;
grant execute on function public.split_mention(uuid, uuid, uuid) to authenticated;
grant execute on function public.dismiss_merge_suggestion(uuid) to authenticated;

commit;
