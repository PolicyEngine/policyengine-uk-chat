-- Close the public schema to the Data API's anon and authenticated roles.
--
-- Supabase's Data API serves the public schema, and Supabase's default
-- privileges grant both roles access to each new object. This app reads nothing
-- through them: the backend connects as the tables' owner, billing uses the
-- service role, and the frontend uses Supabase for auth only. Table owners and
-- the service role bypass row-level security, so both keep working. The
-- backend's role must own the tables, inherit their owner's privileges, or have
-- BYPASSRLS; the backend refuses to start otherwise.
--
-- This file revokes what PUBLIC, anon and authenticated hold here, including on
-- the billing tables. The "Users read own" policies in 001_billing.sql
-- stay, so unless another policy admits anon or authenticated, a SELECT grant
-- someone adds back still reaches only the signed-in user's rows.
--
-- Run it in the Supabase SQL editor after `alembic upgrade head`. It changes
-- access only, reads no rows and can run more than once. It changes each object
-- whose owner's privileges the running role holds, and skips any other with a
-- warning naming the object and its owner.
--
-- PostgreSQL records a grantor with each grant. Each REVOKE acts as one role
-- and removes only the grants with that role as grantor. For that role,
-- PostgreSQL picks either the running role or a role that owns the object or
-- holds grant options on it and whose privileges the running role holds; a
-- superuser's REVOKE acts as the object's owner. When several roles qualify,
-- PostgreSQL doesn't specify which one it picks, and its REVOKE reference
-- recommends SET ROLE to choose. A grant with any other grantor stays, so the
-- file warns about each grant that still reaches PUBLIC, anon or authenticated,
-- names its grantor, and gives the statement that revokes it as that role. Run
-- those statements in the order the file prints them.
--
-- Without CASCADE, PostgreSQL refuses to revoke a privilege from a role that
-- has granted it on through its grant option. When anon or authenticated has
-- done that, the file revokes nothing from that role on that object and warns
-- with the CASCADE statement, which also revokes every grant that depends on
-- that option. The file never cascades by itself, since a cascade would also
-- revoke the grants anon or authenticated made to other roles. Either run that
-- statement and then the file again, or run the statements that follow the
-- warning. PostgreSQL can still refuse one of those without CASCADE when anon
-- or authenticated passed a grant option on to a role outside those three, or
-- when the two gave each other one.
--
-- The file removes the running role's default grants to those three in public.
-- Other roles keep theirs. PostgreSQL also applies defaults for every schema in
-- public, so the file warns about each one that belongs to the running role, or
-- to a role whose privileges it holds, and grants anon or authenticated
-- anything, or grants PUBLIC anything on tables or sequences.
--
-- Rerun it after a revision adds a table or a function. The running role's
-- defaults in public give a new table no grant for either role, and the rerun
-- turns on its row-level security. Until the rerun, PUBLIC can call a new
-- function, because PostgreSQL can't revoke that default for one schema, and
-- the backend refuses to start if either role can execute a SECURITY DEFINER
-- function in the schema that belongs to no extension.
--
-- If the file waits more than 5 seconds for a lock, it stops and changes
-- nothing; run it again. It takes an exclusive lock only to turn on a table's
-- row-level security or to drop one of the two billing policies below, so a
-- rerun locks only a table that still lacks row-level security or still has
-- one of those policies.

begin;

set local lock_timeout = '5s';

-- With an empty search path, regclass and regprocedure print each name with its
-- schema, so every statement below and in the warnings names its object
-- exactly, whatever search path a later session uses.
set local search_path = '';

do $$
declare
  object record;
  role_name text;
  kept record;
begin
  for object in
    select *
    from (
      select
        c.oid,
        c.oid::regclass::text as name,
        case c.relkind when 'S' then 'sequence' else 'table' end as keyword,
        c.relname,
        c.relkind,
        c.relrowsecurity,
        c.relowner::regrole as owner,
        pg_has_role(c.relowner, 'USAGE') as can_alter
      from pg_class as c
      join pg_namespace as n on n.oid = c.relnamespace
      where n.nspname = 'public'
        and c.relkind in ('r', 'p', 'v', 'm', 'f', 'S')
      union all
      select
        p.oid,
        p.oid::regprocedure::text,
        'routine',
        null,
        null,
        null,
        p.proowner::regrole,
        pg_has_role(p.proowner, 'USAGE')
      from pg_proc as p
      join pg_namespace as n on n.oid = p.pronamespace
      where n.nspname = 'public'
    ) as objects
    order by keyword = 'routine', relname, oid
  loop
    if not object.can_alter then
      raise warning 'skipped % (owner %): the running role does not own it',
        object.name, object.owner;
      continue;
    end if;

    if object.relkind in ('r', 'p') and not object.relrowsecurity then
      execute format('alter table %s enable row level security', object.name);
    end if;

    -- One statement for each role, so a grant option that one of them has
    -- passed on stops only that role's revoke.
    foreach role_name in array array['public', 'anon', 'authenticated'] loop
      begin
        execute format('revoke all on %s %s from %s', object.keyword, object.name, role_name);
      exception when dependent_objects_still_exist then
        raise warning '% has passed on a grant option on %, so this run revoked nothing from % there; this statement also revokes every grant that depends on that option: set role %; revoke all on % % from % cascade; reset role;',
          role_name, object.name, role_name, quote_ident(current_user), object.keyword,
          object.name, role_name;
      end;
    end loop;

    -- 001_billing.sql wrote these two policies for the service role but gave
    -- them no role, so they admit every role. The service role bypasses
    -- row-level security and needs neither.
    if object.relname = 'user_credits' then
      drop policy if exists "Service manages credits" on public.user_credits;
    elsif object.relname = 'token_usage' then
      drop policy if exists "Service inserts usage" on public.token_usage;
    end if;

    for kept in
      with acl as (
        select null::name as column_name, x.grantor, x.grantee, x.privilege_type
        from pg_class as c
        cross join lateral aclexplode(c.relacl) as x
        where object.keyword <> 'routine'
          and c.oid = object.oid
        union all
        select a.attname, x.grantor, x.grantee, x.privilege_type
        from pg_attribute as a
        cross join lateral aclexplode(a.attacl) as x
        where object.keyword <> 'routine'
          and a.attrelid = object.oid
          and not a.attisdropped
        union all
        select null, x.grantor, x.grantee, x.privilege_type
        from pg_proc as p
        cross join lateral aclexplode(p.proacl) as x
        where object.keyword = 'routine'
          and p.oid = object.oid
      )
      select
        case when acl.grantee = 0 then 'PUBLIC' else acl.grantee::regrole::text end
          as grantee,
        acl.grantor::regrole::text as grantor,
        string_agg(
          case
            when acl.column_name is null then acl.privilege_type
            else format('%s (%I)', acl.privilege_type, acl.column_name)
          end,
          ', ' order by acl.privilege_type
        ) as privileges
      from acl
      where acl.grantee = 0
        or acl.grantee in (
          select r.oid from pg_roles as r where r.rolname in ('anon', 'authenticated')
        )
      group by acl.column_name, acl.grantee, acl.grantor
      -- Without CASCADE, PostgreSQL refuses to revoke a privilege from a role
      -- while grants that role made through its grant option remain. Grants
      -- that anon and authenticated made come first, and within each group,
      -- grants to a role that made no grant here come first. Each statement
      -- then runs after the statements for the grants that depend on it, unless
      -- anon and authenticated gave each other a grant option.
      order by
        acl.grantor in (
          select r.oid from pg_roles as r where r.rolname in ('anon', 'authenticated')
        ) desc,
        acl.grantee in (select made.grantor from acl as made),
        acl.column_name nulls first,
        1,
        2
    loop
      raise warning '% granted % % on %, and this run kept it; revoke it with: set role %; revoke % on % % from %; reset role;',
        kept.grantor, kept.grantee, kept.privileges, object.name,
        kept.grantor, kept.privileges, object.keyword, object.name, kept.grantee;
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
