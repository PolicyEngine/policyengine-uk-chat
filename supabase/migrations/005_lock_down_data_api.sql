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
-- This file revokes what PUBLIC, anon and authenticated hold here, on the
-- billing tables included. The "Users read own" policies in 001_billing.sql
-- stay, so a SELECT grant someone adds back still reaches only the signed-in
-- user's rows.
--
-- Run it in the Supabase SQL editor after `alembic upgrade head`. It changes
-- access only, reads no rows and can run more than once. It changes each object
-- whose owner's privileges the running role holds, and skips any other with a
-- warning naming the object and its owner.
--
-- PostgreSQL lets a role revoke only the grants it made. A grant that another
-- role made, for example through a grant option, stays, so the file warns about
-- each one that still reaches PUBLIC, anon or authenticated and names the role
-- that made it.
--
-- The file removes the running role's default grants to those three in public.
-- Other roles keep theirs. PostgreSQL also applies defaults for every schema in
-- public, so the file warns about each one that belongs to the running role, or
-- to a role whose privileges it holds, and grants anon or authenticated
-- anything, or grants PUBLIC anything on tables or sequences.
--
-- Rerun it after a revision adds a table or a function. The running role's
-- defaults in public give a new table no grant for either role, and the rerun
-- turns on its row-level security. A new function stays callable through
-- PUBLIC until the rerun, because PostgreSQL can't revoke that default for one
-- schema; until then the backend refuses to start if either role can execute a
-- SECURITY DEFINER function in the schema that belongs to no extension.
--
-- If the file waits more than 5 seconds for a lock, it stops and changes
-- nothing; run it again. It takes an exclusive lock only to turn on a table's
-- row-level security or to drop one of the two billing policies below, so a
-- rerun locks only a table that still lacks row-level security or still has
-- one of those policies.

begin;

set local lock_timeout = '5s';

do $$
declare
  object record;
  kept record;
begin
  for object in
    select
      c.oid,
      c.oid::regclass as name,
      c.relname,
      c.relkind,
      c.relrowsecurity,
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
    else
      if object.relkind in ('r', 'p') and not object.relrowsecurity then
        execute format('alter table %s enable row level security', object.name);
      end if;
      execute format('revoke all on table %s from public, anon, authenticated', object.name);
    end if;

    -- 001_billing.sql wrote these two policies for the service role but gave
    -- them no role, so they admit every role. The service role bypasses
    -- row-level security and needs neither.
    if object.relname = 'user_credits' then
      drop policy if exists "Service manages credits" on public.user_credits;
    elsif object.relname = 'token_usage' then
      drop policy if exists "Service inserts usage" on public.token_usage;
    end if;

    for kept in
      select
        case
          when acl.column_name is null then object.name::text
          else format('%s.%I', object.name, acl.column_name)
        end as target,
        case when acl.grantee = 0 then 'PUBLIC' else acl.grantee::regrole::text end
          as grantee,
        acl.grantor::regrole::text as grantor,
        string_agg(acl.privilege_type, ', ' order by acl.privilege_type) as privileges
      from (
        select null::name as column_name, x.grantor, x.grantee, x.privilege_type
        from pg_class as c
        cross join lateral aclexplode(c.relacl) as x
        where c.oid = object.oid
        union all
        select a.attname, x.grantor, x.grantee, x.privilege_type
        from pg_attribute as a
        cross join lateral aclexplode(a.attacl) as x
        where a.attrelid = object.oid
          and not a.attisdropped
      ) as acl
      where acl.grantee = 0
        or acl.grantee in (
          select r.oid from pg_roles as r where r.rolname in ('anon', 'authenticated')
        )
      group by acl.column_name, acl.grantee, acl.grantor
      order by acl.column_name nulls first, 2, 3
    loop
      raise warning '% keeps % on %, granted by %: revoke it as that role',
        kept.grantee, kept.privileges, kept.target, kept.grantor;
    end loop;
  end loop;

  for object in
    select
      p.oid,
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

    for kept in
      select
        case when x.grantee = 0 then 'PUBLIC' else x.grantee::regrole::text end
          as grantee,
        x.grantor::regrole::text as grantor,
        string_agg(x.privilege_type, ', ' order by x.privilege_type) as privileges
      from pg_proc as p
      cross join lateral aclexplode(p.proacl) as x
      where p.oid = object.oid
        and (
          x.grantee = 0
          or x.grantee in (
            select r.oid from pg_roles as r where r.rolname in ('anon', 'authenticated')
          )
        )
      group by x.grantee, x.grantor
      order by 1, 2
    loop
      raise warning '% keeps % on %, granted by %: revoke it as that role',
        kept.grantee, kept.privileges, object.name, kept.grantor;
    end loop;
  end loop;
end
$$;

-- A revoke from public here undoes only a default grant for this schema. The
-- EXECUTE that PostgreSQL gives PUBLIC on every new function stays.
alter default privileges in schema public revoke all on tables from public, anon, authenticated;
alter default privileges in schema public revoke all on sequences from public, anon, authenticated;
alter default privileges in schema public revoke all on routines from public, anon, authenticated;

-- PostgreSQL adds a schema's defaults to the defaults for every schema, so the
-- revokes above can't remove a grant that a default for every schema makes.
do $$
declare
  kept record;
begin
  for kept in
    select
      named.creator,
      case named.object_type
        when 'r' then 'table'
        when 'S' then 'sequence'
        else 'function'
      end as kind,
      string_agg(named.grantee, ', ' order by named.grantee collate "C") as grantees
    from (
      select distinct
        d.defaclrole::regrole::text as creator,
        d.defaclobjtype as object_type,
        case when x.grantee = 0 then 'PUBLIC' else x.grantee::regrole::text end
          as grantee
      from pg_default_acl as d
      cross join lateral aclexplode(d.defaclacl) as x
      where d.defaclnamespace = 0
        and d.defaclobjtype in ('r', 'S', 'f')
        and pg_has_role(d.defaclrole, 'USAGE')
        and (
          x.grantee in (
            select r.oid from pg_roles as r where r.rolname in ('anon', 'authenticated')
          )
          -- PostgreSQL's own default gives PUBLIC EXECUTE on each new function.
          or (x.grantee = 0 and d.defaclobjtype in ('r', 'S'))
        )
    ) as named
    group by named.creator, named.object_type
    order by 1, 2
  loop
    raise warning '% grants % privileges on each % it creates in any schema; only a revoke without IN SCHEMA removes that default',
      kept.creator, kept.grantees, kept.kind;
  end loop;
end
$$;

notify pgrst, 'reload schema';

commit;
