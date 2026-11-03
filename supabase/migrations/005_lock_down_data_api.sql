-- Close the public schema to the Data API's anon and authenticated roles.
--
-- Supabase's Data API serves the public schema, and Supabase's default
-- privileges grant both roles access to each new object. This app reads nothing
-- through them: the backend connects as the tables' owner, billing uses the
-- service role, and the frontend uses Supabase for auth only. Table owners and
-- the service role bypass row-level security, so both keep working. The
-- backend's role must own the tables or have BYPASSRLS; the backend refuses to
-- start otherwise.
--
-- Neither role keeps any privilege here, on the billing tables included. The
-- "Users read own" policies in 001_billing.sql stay, so a SELECT grant someone
-- adds back still reaches only the signed-in user's rows.
--
-- Run this in the Supabase SQL editor after `alembic upgrade head`. It changes
-- access only, reads no rows and can run more than once. It changes each object
-- whose owner's privileges the running role holds, and skips any other with a
-- warning naming the object and its owner. It removes the running role's
-- default grants to PUBLIC, anon and authenticated in public; other roles keep
-- theirs.
--
-- Rerun it after a revision adds a table or a function. A table the running
-- role creates later gets no grant for either role, and the rerun turns on its
-- row-level security. A new function stays callable through PUBLIC until the
-- rerun, because PostgreSQL can't revoke that default for one schema; until
-- then the backend refuses to start if either role can execute a SECURITY
-- DEFINER function in the schema that belongs to no extension.

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
      c.relowner::regrole as owner,
      pg_has_role(c.relowner, 'USAGE') as can_alter
    from pg_class as c
    join pg_namespace as n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'p', 'v', 'm', 'f', 'S')
    order by c.relname
  loop
    if not object.can_alter then
      raise warning 'skipped % (owner %): the running role does not own it',
        object.name, object.owner;
      continue;
    end if;

    if object.relkind = 'S' then
      execute format('revoke all on sequence %s from public, anon, authenticated', object.name);
      continue;
    end if;

    if object.relkind in ('r', 'p') then
      execute format('alter table %s enable row level security', object.name);
    end if;
    execute format('revoke all on table %s from public, anon, authenticated', object.name);

    -- 001_billing.sql wrote these two policies for the service role but gave
    -- them no role, so they admit every role. The service role bypasses
    -- row-level security and needs neither.
    if object.relname = 'user_credits' then
      drop policy if exists "Service manages credits" on public.user_credits;
    elsif object.relname = 'token_usage' then
      drop policy if exists "Service inserts usage" on public.token_usage;
    end if;
  end loop;

  for object in
    select
      p.oid::regprocedure as name,
      p.proowner::regrole as owner,
      pg_has_role(p.proowner, 'USAGE') as can_alter
    from pg_proc as p
    join pg_namespace as n on n.oid = p.pronamespace
    where n.nspname = 'public'
    order by p.oid
  loop
    if not object.can_alter then
      raise warning 'skipped % (owner %): the running role does not own it',
        object.name, object.owner;
      continue;
    end if;
    execute format('revoke all on routine %s from public, anon, authenticated', object.name);
  end loop;
end
$$;

-- Revoking from public here undoes only a grant made for this schema. The
-- EXECUTE that PostgreSQL gives PUBLIC on every new function stays.
alter default privileges in schema public revoke all on tables from public, anon, authenticated;
alter default privileges in schema public revoke all on sequences from public, anon, authenticated;
alter default privileges in schema public revoke all on routines from public, anon, authenticated;

notify pgrst, 'reload schema';

commit;
