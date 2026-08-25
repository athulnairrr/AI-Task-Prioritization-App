-- 0001_init.sql
-- Core MVP schema: profiles, tenants (workspaces), tenant membership,
-- tasks, AI prioritization results, plans, schedule items, Google Calendar
-- integration tables, and usage records.
--
-- Design notes (see /docs/architecture.md and /docs/decisions.md ADR-006/007):
--   * Every tenant-owned table carries a `tenant_id` and is protected by RLS.
--   * `auth.users` (Supabase Auth) is the identity source of truth; `profiles`
--     is a 1:1 public-schema mirror so the rest of the schema never has to
--     reference the `auth` schema directly.
--   * A new user automatically gets a personal tenant + owner membership via
--     the `handle_new_user` trigger below -- the same shape supports adding
--     teammates to a tenant later without a schema change.
--   * RLS policy shape: authenticated users may SELECT rows in tenants they
--     are a member of. Mutating writes to business-logic tables (tasks,
--     plans, schedule items, AI results, calendar data, usage) go through
--     the FastAPI backend using the Supabase service role key, which
--     bypasses RLS -- clients query directly (e.g. for realtime) but never
--     write directly to tables that require server-side validation.
--     `tenants`/`tenant_members`/`profiles` allow limited client-side writes
--     (self-service profile edits, future manual team creation) because
--     those don't require business logic.

create extension if not exists pgcrypto;

-- ============================================================================
-- Enums
-- ============================================================================

do $$ begin
  create type tenant_role as enum ('owner', 'admin', 'member');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type task_status as enum ('pending', 'in_progress', 'done', 'cancelled');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type plan_status as enum ('draft', 'active', 'completed', 'archived');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type schedule_item_status as enum ('scheduled', 'completed', 'skipped', 'cancelled');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type calendar_sync_status as enum ('pending', 'synced', 'failed');
exception when duplicate_object then null;
end $$;

-- ============================================================================
-- profiles (1:1 mirror of auth.users)
-- ============================================================================

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  full_name text,
  avatar_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ============================================================================
-- tenants (workspaces) -- personal by default, teams are the same shape
-- ============================================================================

create table if not exists public.tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  is_personal boolean not null default true,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_tenants_owner_id on public.tenants (owner_id);

-- ============================================================================
-- tenant_members -- join table, same shape supports teams/managers later
-- ============================================================================

create table if not exists public.tenant_members (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  role tenant_role not null default 'member',
  created_at timestamptz not null default now(),
  unique (tenant_id, user_id)
);

create index if not exists idx_tenant_members_tenant_id on public.tenant_members (tenant_id);
create index if not exists idx_tenant_members_user_id on public.tenant_members (user_id);

-- ============================================================================
-- tasks
-- ============================================================================

create table if not exists public.tasks (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  created_by uuid not null references public.profiles (id),
  title text not null,
  description text,
  raw_input text,
  status task_status not null default 'pending',
  due_at timestamptz,
  estimated_minutes int,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_tasks_title_not_blank check (btrim(title) <> ''),
  constraint chk_tasks_estimated_minutes_positive check (estimated_minutes is null or estimated_minutes > 0)
);

create index if not exists idx_tasks_tenant_id on public.tasks (tenant_id);
create index if not exists idx_tasks_tenant_status on public.tasks (tenant_id, status);
create index if not exists idx_tasks_tenant_due_at on public.tasks (tenant_id, due_at);
create index if not exists idx_tasks_created_by on public.tasks (created_by);

-- ============================================================================
-- task_ai_results -- one row per AI prioritization pass over a task
-- ============================================================================

create table if not exists public.task_ai_results (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references public.tasks (id) on delete cascade,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  model text not null,
  priority_score numeric(5, 2),
  urgency text,
  effort_estimate_minutes int,
  category text,
  reasoning text,
  raw_response jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint chk_task_ai_results_priority_score check (
    priority_score is null or (priority_score >= 0 and priority_score <= 100)
  )
);

create index if not exists idx_task_ai_results_task_id on public.task_ai_results (task_id);
create index if not exists idx_task_ai_results_tenant_id on public.task_ai_results (tenant_id);

-- ============================================================================
-- plans
-- ============================================================================

create table if not exists public.plans (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  created_by uuid not null references public.profiles (id),
  name text not null default 'My Plan',
  status plan_status not null default 'draft',
  starts_on date,
  ends_on date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_plans_date_range check (
    starts_on is null or ends_on is null or ends_on >= starts_on
  )
);

create index if not exists idx_plans_tenant_status on public.plans (tenant_id, status);

-- ============================================================================
-- schedule_items -- where a task lands on the plan's timeline
-- ============================================================================

create table if not exists public.schedule_items (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  plan_id uuid not null references public.plans (id) on delete cascade,
  task_id uuid not null references public.tasks (id) on delete cascade,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  status schedule_item_status not null default 'scheduled',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_schedule_items_time_range check (ends_at > starts_at)
);

create index if not exists idx_schedule_items_tenant_id on public.schedule_items (tenant_id);
create index if not exists idx_schedule_items_plan_id on public.schedule_items (plan_id);
create index if not exists idx_schedule_items_task_id on public.schedule_items (task_id);
create index if not exists idx_schedule_items_tenant_starts_at on public.schedule_items (tenant_id, starts_at);

-- ============================================================================
-- google_calendar_connections -- one per (tenant, user, calendar)
-- ============================================================================

create table if not exists public.google_calendar_connections (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  google_account_email text not null,
  -- NOTE: tokens are stored as-is for MVP; production should encrypt these
  -- at rest (e.g. pgsodium/Vault) before real user tokens are stored here.
  access_token text not null,
  refresh_token text not null,
  token_expires_at timestamptz,
  calendar_id text not null default 'primary',
  connected_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, user_id, calendar_id)
);

create index if not exists idx_gcal_connections_tenant_id on public.google_calendar_connections (tenant_id);
create index if not exists idx_gcal_connections_user_id on public.google_calendar_connections (user_id);

-- ============================================================================
-- google_calendar_event_mappings -- schedule_item <-> Google Calendar event
-- ============================================================================

create table if not exists public.google_calendar_event_mappings (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  schedule_item_id uuid not null references public.schedule_items (id) on delete cascade,
  connection_id uuid not null references public.google_calendar_connections (id) on delete cascade,
  google_event_id text not null,
  sync_status calendar_sync_status not null default 'pending',
  last_synced_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (schedule_item_id),
  unique (connection_id, google_event_id)
);

create index if not exists idx_gcal_event_mappings_tenant_id on public.google_calendar_event_mappings (tenant_id);
create index if not exists idx_gcal_event_mappings_connection_id on public.google_calendar_event_mappings (connection_id);

-- ============================================================================
-- usage_records -- lightweight metering (AI calls, calendar syncs, etc.)
-- ============================================================================

create table if not exists public.usage_records (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  user_id uuid references public.profiles (id) on delete set null,
  event_type text not null,
  quantity int not null default 1,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_usage_records_tenant_created_at on public.usage_records (tenant_id, created_at);
create index if not exists idx_usage_records_tenant_event_type on public.usage_records (tenant_id, event_type);

-- ============================================================================
-- updated_at trigger
-- ============================================================================

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

do $$
declare
  t text;
begin
  foreach t in array array[
    'profiles', 'tenants', 'tasks', 'plans', 'schedule_items',
    'google_calendar_connections', 'google_calendar_event_mappings'
  ]
  loop
    execute format(
      'drop trigger if exists set_updated_at on public.%I; ' ||
      'create trigger set_updated_at before update on public.%I ' ||
      'for each row execute procedure public.set_updated_at();',
      t, t
    );
  end loop;
end $$;

-- ============================================================================
-- New user provisioning: personal tenant + owner membership
-- ============================================================================

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  new_tenant_id uuid;
  display_name text;
begin
  insert into public.profiles (id, email, full_name)
  values (new.id, new.email, new.raw_user_meta_data ->> 'full_name')
  on conflict (id) do nothing;

  display_name := coalesce(new.raw_user_meta_data ->> 'full_name', split_part(new.email, '@', 1));

  insert into public.tenants (name, slug, is_personal, owner_id)
  values (display_name || '''s workspace', 'user-' || replace(new.id::text, '-', ''), true, new.id)
  returning id into new_tenant_id;

  insert into public.tenant_members (tenant_id, user_id, role)
  values (new_tenant_id, new.id, 'owner');

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- ============================================================================
-- Row Level Security
-- ============================================================================

alter table public.profiles enable row level security;
alter table public.tenants enable row level security;
alter table public.tenant_members enable row level security;
alter table public.tasks enable row level security;
alter table public.task_ai_results enable row level security;
alter table public.plans enable row level security;
alter table public.schedule_items enable row level security;
alter table public.google_calendar_connections enable row level security;
alter table public.google_calendar_event_mappings enable row level security;
alter table public.usage_records enable row level security;

-- Helper functions (security definer so they don't recurse into RLS on
-- tenant_members when called from another table's policy).

create or replace function public.is_tenant_member(target_tenant_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.tenant_members tm
    where tm.tenant_id = target_tenant_id
      and tm.user_id = auth.uid()
  );
$$;

create or replace function public.tenant_role_for(target_tenant_id uuid)
returns tenant_role
language sql
stable
security definer
set search_path = public
as $$
  select tm.role
  from public.tenant_members tm
  where tm.tenant_id = target_tenant_id
    and tm.user_id = auth.uid();
$$;

-- profiles: users see their own profile and profiles of people who share a
-- tenant with them; they may only edit their own profile.
create policy profiles_select on public.profiles
  for select to authenticated
  using (
    id = auth.uid()
    or exists (
      select 1
      from public.tenant_members tm1
      join public.tenant_members tm2 on tm1.tenant_id = tm2.tenant_id
      where tm1.user_id = auth.uid() and tm2.user_id = profiles.id
    )
  );

create policy profiles_update_self on public.profiles
  for update to authenticated
  using (id = auth.uid())
  with check (id = auth.uid());

-- tenants: members can see their tenants; a user may create additional
-- (non-personal) tenants they own -- foundation for future team creation.
create policy tenants_select on public.tenants
  for select to authenticated
  using (public.is_tenant_member(id));

create policy tenants_insert_own on public.tenants
  for insert to authenticated
  with check (owner_id = auth.uid() and is_personal = false);

create policy tenants_update_admin on public.tenants
  for update to authenticated
  using (public.tenant_role_for(id) in ('owner', 'admin'))
  with check (public.tenant_role_for(id) in ('owner', 'admin'));

create policy tenants_delete_owner on public.tenants
  for delete to authenticated
  using (public.tenant_role_for(id) = 'owner');

-- tenant_members: members can see co-members; owners manage membership,
-- and a member may remove themselves (leave a tenant).
create policy tenant_members_select on public.tenant_members
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

create policy tenant_members_delete on public.tenant_members
  for delete to authenticated
  using (public.tenant_role_for(tenant_id) = 'owner' or user_id = auth.uid());

-- Tenant-owned business data: members can read; all writes go through the
-- backend's service role (which bypasses RLS), so no insert/update/delete
-- policy is defined for `authenticated` on these tables.
create policy tasks_select on public.tasks
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

create policy task_ai_results_select on public.task_ai_results
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

create policy plans_select on public.plans
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

create policy schedule_items_select on public.schedule_items
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

create policy google_calendar_connections_select on public.google_calendar_connections
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

create policy google_calendar_event_mappings_select on public.google_calendar_event_mappings
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

create policy usage_records_select on public.usage_records
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

-- ============================================================================
-- Grants
-- ============================================================================
-- `anon` gets nothing on these tables -- every route into this data requires
-- a signed-in user. `authenticated` gets exactly the privileges the policies
-- above allow; RLS still filters rows. `service_role` bypasses RLS by
-- default on the Supabase platform and needs no additional grants here.

revoke all on
  public.profiles, public.tenants, public.tenant_members, public.tasks,
  public.task_ai_results, public.plans, public.schedule_items,
  public.google_calendar_connections, public.google_calendar_event_mappings,
  public.usage_records
from anon;

grant select, update on public.profiles to authenticated;
grant select, insert, update, delete on public.tenants to authenticated;
grant select, delete on public.tenant_members to authenticated;
grant select on
  public.tasks, public.task_ai_results, public.plans, public.schedule_items,
  public.google_calendar_connections, public.google_calendar_event_mappings,
  public.usage_records
to authenticated;
