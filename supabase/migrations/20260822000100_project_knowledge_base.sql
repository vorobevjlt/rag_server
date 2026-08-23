create extension if not exists pgcrypto;
create extension if not exists vector;

-- The frontend schema uses these clearer project column names. Make databases
-- created from the original RAG migration converge without dropping data.
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'projects' and column_name = 'clerk_id'
  ) and not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'projects' and column_name = 'owner_clerk_id'
  ) then
    alter table public.projects rename column clerk_id to owner_clerk_id;
  end if;

  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'projects' and column_name = 'description'
  ) and not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'projects' and column_name = 'context'
  ) then
    alter table public.projects rename column description to context;
  end if;
end;
$$;

create index if not exists projects_owner_created_at_idx
  on public.projects (owner_clerk_id, created_at desc);

alter table public.project_settings
  add column if not exists rag_enabled boolean not null default true,
  add column if not exists answer_mode text not null default 'combined';

alter table public.project_settings alter column embedding_model set default 'text-embedding-3-large';
alter table public.project_settings alter column rag_strategy set default 'hybrid';
alter table public.project_settings alter column agent_type set default 'simple';
alter table public.project_settings alter column chunks_per_search set default 20;
alter table public.project_settings alter column final_context_size set default 6;
alter table public.project_settings alter column similarity_threshold set default 0.3;
alter table public.project_settings alter column number_of_queries set default 3;
alter table public.project_settings alter column reranking_enabled set default false;
alter table public.project_settings alter column reranking_model set default 'rerank-english-v3.0';
alter table public.project_settings alter column vector_weight set default 0.7;
alter table public.project_settings alter column keyword_weight set default 0.3;

alter table public.project_settings
  drop constraint if exists project_settings_answer_mode_check;
alter table public.project_settings
  add constraint project_settings_answer_mode_check
  check (answer_mode in ('combined', 'knowledge_only'));

alter table public.project_documents
  add column if not exists enabled boolean not null default true;

alter table public.document_chunks
  add column if not exists source_metadata jsonb not null default '{}'::jsonb;

create index if not exists project_documents_project_status_idx
  on public.project_documents (project_id, processing_status, enabled);

create unique index if not exists messages_trace_id_unique
  on public.messages (trace_id)
  where trace_id is not null;

insert into public.project_settings (project_id)
select p.id
from public.projects p
where not exists (
  select 1 from public.project_settings ps where ps.project_id = p.id
);

create or replace function public.create_default_project_settings()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.project_settings (project_id)
  values (new.id)
  on conflict (project_id) do nothing;
  return new;
end;
$$;

drop trigger if exists projects_create_default_settings on public.projects;
create trigger projects_create_default_settings
after insert on public.projects
for each row execute function public.create_default_project_settings();

drop function if exists public.vector_search_document_chunks(vector, uuid[], double precision, integer);
create function public.vector_search_document_chunks(
  query_embedding vector,
  filter_document_ids uuid[],
  match_threshold double precision default 0.3,
  chunks_per_search integer default 20
)
returns table(
  id uuid,
  document_id uuid,
  content text,
  chunk_index integer,
  created_at timestamptz,
  page_number integer,
  char_count integer,
  type jsonb,
  original_content jsonb,
  source_metadata jsonb,
  embedding vector
)
language sql
stable
as $$
  select
    dc.id,
    dc.document_id,
    dc.content,
    dc.chunk_index,
    dc.created_at,
    dc.page_number,
    dc.char_count,
    dc.type::jsonb,
    dc.original_content::jsonb,
    dc.source_metadata,
    dc.embedding
  from public.document_chunks dc
  where dc.document_id = any(filter_document_ids)
    and dc.embedding is not null
    and (1 - (dc.embedding <=> query_embedding)) > match_threshold
  order by dc.embedding <=> query_embedding asc
  limit chunks_per_search;
$$;

drop function if exists public.keyword_search_document_chunks(text, uuid[], integer);
create function public.keyword_search_document_chunks(
  query_text text,
  filter_document_ids uuid[],
  chunks_per_search integer default 20
)
returns table(
  id uuid,
  document_id uuid,
  content text,
  chunk_index integer,
  created_at timestamptz,
  page_number integer,
  char_count integer,
  type jsonb,
  original_content jsonb,
  source_metadata jsonb,
  embedding vector
)
language sql
stable
as $$
  select
    dc.id,
    dc.document_id,
    dc.content,
    dc.chunk_index,
    dc.created_at,
    dc.page_number,
    dc.char_count,
    dc.type::jsonb,
    dc.original_content::jsonb,
    dc.source_metadata,
    dc.embedding
  from public.document_chunks dc
  where dc.fts @@ websearch_to_tsquery('english', query_text)
    and dc.document_id = any(filter_document_ids)
  order by ts_rank_cd(dc.fts, websearch_to_tsquery('english', query_text)) desc
  limit chunks_per_search;
$$;

alter table public.users enable row level security;
alter table public.projects enable row level security;
alter table public.project_settings enable row level security;
alter table public.project_documents enable row level security;
alter table public.document_chunks enable row level security;
alter table public.chats enable row level security;
alter table public.messages enable row level security;

revoke all on table
  public.users,
  public.projects,
  public.project_settings,
  public.project_documents,
  public.document_chunks,
  public.chats,
  public.messages
from anon, authenticated;

grant usage on schema public to service_role;
grant select, insert, update, delete on table
  public.users,
  public.projects,
  public.project_settings,
  public.project_documents,
  public.document_chunks,
  public.chats,
  public.messages
to service_role;
grant execute on function public.vector_search_document_chunks(vector, uuid[], double precision, integer) to service_role;
grant execute on function public.keyword_search_document_chunks(text, uuid[], integer) to service_role;
