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
-- file warns about grants that still reach PUBLIC, anon or authenticated and
-- names their grantor. When that grantor holds the needed grant options in its
-- own name, it gives a REVOKE as that role. Otherwise it gives one transaction
-- for all that grantor's kept grants on the object: the owner temporarily gives
-- it the missing options, it revokes the grants, and the owner restores its
-- prior privileges, including column grants a table-level restore removes.
-- If another grant depends on a temporary option, the restore
-- fails with 2BP01 and PostgreSQL rolls the entire transaction back. Run the
-- printed statements in order, from a session that can SET ROLE as they name.
--
-- Without CASCADE, PostgreSQL refuses to revoke a privilege from a role that
-- has granted it on through its grant option. When anon or authenticated has
-- done that, the file revokes nothing from that role on that object and warns
-- with the CASCADE statement, which also revokes every grant that depends on
-- that option. The file never cascades by itself, since a cascade would also
-- revoke the grants anon or authenticated made to other roles. Either run that
-- statement and then the file again, or run the statements that follow the
-- warning. A column grant can outlive the CASCADE; a rerun then prints the
-- transaction for it. PostgreSQL can still refuse a printed statement without
-- CASCADE when a grant to another role depends on the option, or when anon and
-- authenticated gave each other one.
--
-- The file also warns about each direct role membership through which anon or
-- authenticated inherits privileges, and prints the REVOKE for it. It never
-- revokes a membership itself: memberships reach every database on the server.
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
  kept_grant record;
  needed_option record;
  prior_column record;
  warned_grantors oid[];
  remedy text;
  restore text;
  restore_columns text;
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

    warned_grantors := array[]::oid[];
    for kept in
      with acl as (
        select null::name as column_name, x.grantor, x.grantee,
          x.privilege_type, x.is_grantable
        from pg_class as c
        cross join lateral aclexplode(c.relacl) as x
        where object.keyword <> 'routine'
          and c.oid = object.oid
        union all
        select a.attname, x.grantor, x.grantee, x.privilege_type, x.is_grantable
        from pg_attribute as a
        cross join lateral aclexplode(a.attacl) as x
        where object.keyword <> 'routine'
          and a.attrelid = object.oid
          and not a.attisdropped
        union all
        select null, x.grantor, x.grantee, x.privilege_type, x.is_grantable
        from pg_proc as p
        cross join lateral aclexplode(p.proacl) as x
        where object.keyword = 'routine'
          and p.oid = object.oid
      ), needed as (
        select
          acl.column_name,
          acl.grantor as grantor_oid,
          acl.grantor::regrole::text as grantor,
          case when acl.grantee = 0 then 'PUBLIC' else acl.grantee::regrole::text end
            as grantee,
          acl.privilege_type,
          case
            when acl.column_name is null then acl.privilege_type
            else format('%s (%I)', acl.privilege_type, acl.column_name)
          end as privilege,
          -- An inherited option does not make SET ROLE choose this grantor.
          -- A table-level option does, however, cover a column privilege.
          acl.grantor = object.owner::oid or exists (
            select 1 from acl as own
            where own.grantee = acl.grantor
              and own.privilege_type = acl.privilege_type
              and own.is_grantable
              and (own.column_name is null or own.column_name = acl.column_name)
          ) as has_direct_option,
          -- Restore only what the owner had granted at this exact ACL level.
          -- A table privilege is not a pre-existing column ACL entry.
          exists (
            select 1 from acl as prior
            where prior.grantee = acl.grantor
              and prior.grantor = object.owner::oid
              and prior.privilege_type = acl.privilege_type
              and prior.column_name is not distinct from acl.column_name
          ) as had_owner_privilege,
          acl.grantor in (
            select r.oid from pg_roles as r where r.rolname in ('anon', 'authenticated')
          ) as grantor_is_data_api,
          exists (
            select 1 from acl as given
            where given.grantor = acl.grantor
              and given.is_grantable
              and given.grantee <> given.grantor
              and given.grantee in (
                select r.oid from pg_roles as r where r.rolname in ('anon', 'authenticated')
              )
          ) as grantor_has_option_dependents,
          acl.grantee in (select made.grantor from acl as made) as grantee_made_grants
        from acl
        where acl.grantee = 0
          or acl.grantee in (
            select r.oid from pg_roles as r where r.rolname in ('anon', 'authenticated')
          )
      ), grouped as (
        select
          column_name, grantor_oid, grantor, grantee,
          grantor_is_data_api, grantor_has_option_dependents, grantee_made_grants,
          string_agg(privilege, ', ' order by privilege_type) as privileges,
          bool_or(not has_direct_option) as needs_option,
          jsonb_agg(jsonb_build_object(
            'privilege', privilege,
            'has_direct_option', has_direct_option,
            'had_owner_privilege', had_owner_privilege
          )) as details
        from needed
        group by column_name, grantor_oid, grantor, grantee,
          grantor_is_data_api, grantor_has_option_dependents, grantee_made_grants
      )
      select
        grouped.*,
        bool_or(needs_option) over (partition by grantor_oid) as needs_transaction,
        jsonb_agg(to_jsonb(grouped)) over (partition by grantor_oid) as grants
      from grouped
      -- Without CASCADE, PostgreSQL refuses to revoke a privilege from a role
      -- while grants that role made through its grant option remain. Grants
      -- that anon and authenticated made come first, and within each group,
      -- grants to a role that made no grant here come first. Each statement
      -- then runs after the statements for the grants that depend on it, unless
      -- anon and authenticated gave each other a grant option.
      -- Put the downstream grantor first across all columns: an upstream
      -- transaction can also revoke its table-level option, which must stay
      -- until it has revoked every column grant it made through that option.
      order by
        grantor_is_data_api desc,
        grantor_has_option_dependents,
        grantee_made_grants,
        column_name nulls first,
        grantee,
        grantor
    loop
      if not kept.needs_transaction then
        raise warning '% granted % % on %, and this run kept it; revoke it with: set role %; revoke % on % % from %; reset role;',
          kept.grantor, kept.grantee, kept.privileges, object.name,
          kept.grantor, kept.privileges, object.keyword, object.name, kept.grantee;
      elsif not kept.grantor_oid = any(warned_grantors) then
        warned_grantors := array_append(warned_grantors, kept.grantor_oid);
        remedy := format('begin; set role %s;', object.owner);
        restore := '';
        restore_columns := '';
        for needed_option in
          select distinct
            p.entry->>'privilege' as privilege,
            (p.entry->>'had_owner_privilege')::boolean as had_owner_privilege
          from jsonb_array_elements(kept.grants) as g(entry)
          cross join lateral jsonb_array_elements(g.entry->'details') as p(entry)
          where not (p.entry->>'has_direct_option')::boolean
          order by 1, 2
        loop
          remedy := remedy || format(' grant %s on %s %s to %s with grant option;',
            needed_option.privilege, object.keyword, object.name, kept.grantor);
          restore := restore || format(' revoke %s%s on %s %s from %s;',
            case when needed_option.had_owner_privilege then 'grant option for ' else '' end,
            needed_option.privilege, object.keyword, object.name, kept.grantor);
          -- A table-level REVOKE also removes the owner's corresponding
          -- column grants or options. Re-grant exactly those prior entries
          -- after all the basic restores; grants from other grantors stay.
          -- The unadorned privilege name matches only a table-level option,
          -- since a column option's printed privilege includes its column.
          for prior_column in
            select a.attname, x.privilege_type, x.is_grantable
            from pg_attribute as a
            cross join lateral aclexplode(a.attacl) as x
            where object.keyword = 'table'
              and a.attrelid = object.oid
              and not a.attisdropped
              and x.grantor = object.owner::oid
              and x.grantee = kept.grantor_oid
              and x.privilege_type = needed_option.privilege
            order by a.attname, x.privilege_type
          loop
            restore_columns := restore_columns || format(' grant %s (%I) on table %s to %s%s;',
              prior_column.privilege_type, prior_column.attname, object.name, kept.grantor,
              case when prior_column.is_grantable then ' with grant option' else '' end);
          end loop;
        end loop;
        remedy := remedy || format(' reset role; set role %s;', kept.grantor);
        -- Every kept grant by this grantor belongs in the same transaction.
        -- Restoring an option between sibling grants could fail with 2BP01.
        for kept_grant in
          select *
          from jsonb_to_recordset(kept.grants) as g(
            column_name text, grantee text, privileges text, grantee_made_grants boolean
          )
          order by grantee_made_grants, column_name nulls first, grantee
        loop
          remedy := remedy || format(' revoke %s on %s %s from %s;',
            kept_grant.privileges, object.keyword, object.name, kept_grant.grantee);
        end loop;
        remedy := remedy || format(' reset role; set role %s;%s%s reset role; commit;',
          object.owner, restore, restore_columns);
        raise warning '% made grants to PUBLIC, anon or authenticated on %, and this run kept them. This transaction temporarily supplies its missing grant options; if other grants on the object depend on an option, restoring its prior privileges fails with 2BP01 and PostgreSQL rolls the whole transaction back, so nothing changes; revoke it with: %',
          kept.grantor, object.name, remedy;
      end if;
    end loop;
  end loop;
end
$$;

-- Role memberships are server-wide. Report inherited memberships without
-- changing them, including PostgreSQL 16+'s per-membership INHERIT setting.
do $$
declare
  membership record;
  inheritance_condition text;
begin
  if current_setting('server_version_num')::integer >= 160000 then
    inheritance_condition := 'm.inherit_option';
  else
    inheritance_condition := 'member_role.rolinherit';
  end if;
  for membership in execute format($query$
    select distinct member_role.rolname as member_name, granted_role.rolname as role_name
    from pg_auth_members as m
    join pg_roles as member_role on member_role.oid = m.member
    join pg_roles as granted_role on granted_role.oid = m.roleid
    where member_role.rolname in ('anon', 'authenticated')
      and %s
    order by 1, 2
  $query$, inheritance_condition)
  loop
    raise warning '% inherits privileges from % through a direct membership; memberships apply to every database on the server, so this run kept it; revoke it with: %',
      quote_ident(membership.member_name), quote_ident(membership.role_name),
      format('revoke %I from %I;', membership.role_name, membership.member_name);
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
