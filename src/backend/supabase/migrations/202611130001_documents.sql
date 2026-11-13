-- Apply once to the intended Supabase project using its SQL editor.
-- Text and metadata only. Original file storage and passage tables come later.
begin;

create table public.documents (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
    title text not null check (char_length(btrim(title)) between 1 and 300),
    content text not null check (char_length(btrim(content)) > 0),
    document_date date,
    collection text not null default 'Unfiled'
        check (char_length(btrim(collection)) between 1 and 100),
    synthetic boolean not null default false,
    created_at timestamptz not null default now()
);

create index documents_owner_created_idx
    on public.documents (owner_id, created_at desc, id desc);

alter table public.documents enable row level security;
revoke all on public.documents from anon, authenticated;
grant select on public.documents to authenticated;
grant insert (title, content, document_date, collection, synthetic)
    on public.documents to authenticated;

create policy documents_read_own on public.documents
    for select to authenticated using ((select auth.uid()) = owner_id);
create policy documents_insert_own on public.documents
    for insert to authenticated with check ((select auth.uid()) = owner_id);

commit;
