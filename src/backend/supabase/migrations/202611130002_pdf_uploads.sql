begin;

-- The private documents bucket is created through the Storage dashboard.
do $$
begin
    if not exists (select 1 from storage.buckets where id = 'documents' and not public) then
        raise exception 'Create a private documents bucket before applying this migration';
    end if;
end $$;

alter table public.documents
    add column status text not null default 'ready' check (status in ('ready', 'uploaded')),
    add column storage_path text unique,
    add column original_filename text,
    add column file_size_bytes bigint,
    add column page_count integer;

alter table public.documents drop constraint documents_content_check;
alter table public.documents add constraint documents_content_check
    check (char_length(btrim(content)) > 0 or status = 'uploaded');
alter table public.documents add constraint documents_pdf_metadata_check check ((
    (storage_path is null and original_filename is null and file_size_bytes is null and page_count is null and status = 'ready')
    or
    (storage_path = owner_id::text || '/' || id::text || '/original.pdf'
     and original_filename is not null
     and file_size_bytes between 1 and 31457280
     and page_count between 1 and 1000)
) is true);

grant insert (id, status, storage_path, original_filename, file_size_bytes, page_count)
    on public.documents to authenticated;

create policy documents_files_insert_own on storage.objects
    for insert to authenticated with check (
        bucket_id = 'documents' and (storage.foldername(name))[1] = (select auth.uid())::text
    );
create policy documents_files_read_own on storage.objects
    for select to authenticated using (
        bucket_id = 'documents' and (storage.foldername(name))[1] = (select auth.uid())::text
    );
create policy documents_files_cleanup_own on storage.objects
    for delete to authenticated using (
        bucket_id = 'documents' and (storage.foldername(name))[1] = (select auth.uid())::text
        and not exists (select 1 from public.documents d where d.storage_path = name)
    );

commit;
