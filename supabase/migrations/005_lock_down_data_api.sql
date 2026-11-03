-- Close the public schema to the Data API's anon and authenticated roles.
--
-- Supabase's Data API serves the public schema, and Supabase's default
-- privileges grant both roles access to each new object. This app reads nothing
-- through them: the backend connects as the table owner, billing uses the
-- service role, and the frontend uses Supabase for auth only. Table owners and
-- the service role bypass row-level security, so both keep working.
--
-- Signed-in users keep read access to their own token_usage and user_credits
-- rows through the "Users read own" policies in 001_billing.sql.
--
-- Run this in the Supabase SQL editor after `alembic upgrade head`. It changes
-- access only, reads no rows and can run more than once. Rerun it after a
-- revision adds a table: the revoked default privileges keep that table closed,
-- and rerunning turns on its row-level security. A new function in public stays
-- callable through PUBLIC until a rerun, because PostgreSQL can't revoke that
-- default for one schema.

begin;

do $$
declare
  object record;
begin
  for object in
    select
      c.oid::regclass as name,
      c.relname,
      c.relkind,
      pg_has_role(c.relowner, 'USAGE') as can_alter
    from pg_class as c
    join pg_namespace as n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'p', 'v', 'm', 'f', 'S')
    order by c.relname
  loop
    if not object.can_alter then
      raise warning 'skipped %: the running role does not own it', object.name;
      continue;
    end if;

    if object.relkind = 'S' then
      execute format('revoke all on sequence %s from anon, authenticated', object.name);
      continue;
    end if;

    if object.relkind in ('r', 'p') then
      execute format('alter table %s enable row level security', object.name);
    end if;
    execute format('revoke all on table %s from anon, authenticated', object.name);

    -- 001_billing.sql wrote these two policies for the service role but gave
    -- them no role, so they admit every role. The service role bypasses
    -- row-level security and needs neither.
    if object.relname = 'user_credits' then
      drop policy if exists "Service manages credits" on public.user_credits;
      grant select on public.user_credits to authenticated;
    elsif object.relname = 'token_usage' then
      drop policy if exists "Service inserts usage" on public.token_usage;
      grant select on public.token_usage to authenticated;
    end if;
  end loop;

  for object in
    select
      p.oid::regprocedure as name,
      pg_has_role(p.proowner, 'USAGE') as can_alter
    from pg_proc as p
    join pg_namespace as n on n.oid = p.pronamespace
    where n.nspname = 'public'
    order by p.oid
  loop
    if not object.can_alter then
      raise warning 'skipped %: the running role does not own it', object.name;
      continue;
    end if;
    execute format('revoke all on routine %s from public, anon, authenticated', object.name);
  end loop;
end
$$;

alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on routines from anon, authenticated;

notify pgrst, 'reload schema';

commit;
