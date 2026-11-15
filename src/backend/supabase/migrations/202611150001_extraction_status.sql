begin;

alter table public.documents drop constraint documents_status_check;
alter table public.documents add constraint documents_status_check
    check (status in ('uploaded', 'processing', 'ready', 'needs_review', 'failed'));
alter table public.documents drop constraint documents_content_check;
alter table public.documents add constraint documents_content_check
    check (char_length(btrim(content)) > 0 or storage_path is not null);
alter table public.documents
    add column processing_run_id uuid,
    add column processing_started_at timestamptz,
    add column processing_error text;

create table public.document_extractions (
    id uuid primary key,
    document_id uuid not null references public.documents(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    status text not null check (status in ('processing', 'ready', 'needs_review', 'failed')),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    report jsonb,
    error text,
    check ((status = 'processing' and finished_at is null and report is null)
        or (status = 'failed' and finished_at is not null and report is null)
        or (status in ('ready', 'needs_review') and finished_at is not null and report is not null))
);
create index document_extractions_document_idx on public.document_extractions(document_id, started_at desc);
alter table public.document_extractions enable row level security;
revoke all on public.document_extractions from public, anon, authenticated;
grant select on public.document_extractions to authenticated;
create policy extractions_read_own on public.document_extractions
    for select to authenticated using (owner_id = (select auth.uid()));
alter table public.documents add constraint documents_processing_run_fk
    foreign key (processing_run_id) references public.document_extractions(id);

-- Upload and text-create callers cannot insert documents already processing.
alter policy documents_insert_own on public.documents with check (
    owner_id = (select auth.uid()) and status in ('ready', 'uploaded')
);

create function public.start_document_extraction(p_document_id uuid) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
    d public.documents%rowtype;
    attempt uuid := gen_random_uuid();
begin
    select * into d from public.documents
        where id = p_document_id and owner_id = auth.uid() for update;
    if not found or d.storage_path is null then
        raise exception 'PDF not found' using errcode = 'P0002';
    end if;
    if d.status = 'processing' and d.processing_started_at > now() - interval '20 minutes' then
        raise exception 'Processing already active' using errcode = '55000';
    end if;
    if d.processing_run_id is not null then
        update public.document_extractions set status = 'failed', finished_at = now(),
            error = 'Processing was interrupted. Please retry.'
            where id = d.processing_run_id and status = 'processing';
    end if;
    insert into public.document_extractions(id, document_id, owner_id, status)
        values (attempt, d.id, d.owner_id, 'processing');
    update public.documents set status = 'processing', processing_run_id = attempt,
        processing_started_at = now(), processing_error = null where id = d.id;
    return attempt;
end $$;

create function public.finish_document_extraction(
    p_run_id uuid, p_status text, p_report jsonb default null
) returns boolean language plpgsql security definer set search_path = '' as $$
declare
    target uuid;
    d public.documents%rowtype;
    attempt public.document_extractions%rowtype;
    failure text;
begin
    if p_status not in ('ready', 'needs_review', 'failed') then
        raise exception 'Invalid processing result' using errcode = '22023';
    end if;
    select document_id into target from public.document_extractions
        where id = p_run_id and owner_id = auth.uid();
    if not found then
        raise exception 'Extraction not found' using errcode = 'P0002';
    end if;
    -- Always lock document before attempt, matching the start operation.
    select * into d from public.documents where id = target and owner_id = auth.uid() for update;
    if not found or d.processing_run_id is distinct from p_run_id then return false; end if;
    select * into attempt from public.document_extractions where id = p_run_id for update;
    if attempt.status <> 'processing' then return false; end if;
    if p_status <> 'failed' and (
        p_report is null or jsonb_typeof(p_report) <> 'object'
        or p_report #>> '{native_extraction,document_id}' is distinct from target::text
    ) then
        raise exception 'Result source mismatch' using errcode = '22023';
    end if;
    failure := case when p_status = 'failed' then 'Extraction could not finish. Please retry.' else null end;
    update public.document_extractions set status = p_status, finished_at = now(),
        report = case when p_status = 'failed' then null else p_report end, error = failure
        where id = p_run_id;
    update public.documents set status = p_status, processing_error = failure where id = target;
    return true;
end $$;

revoke all on function public.start_document_extraction(uuid) from public, anon;
revoke all on function public.finish_document_extraction(uuid, text, jsonb) from public, anon;
grant execute on function public.start_document_extraction(uuid) to authenticated;
grant execute on function public.finish_document_extraction(uuid, text, jsonb) to authenticated;

commit;
