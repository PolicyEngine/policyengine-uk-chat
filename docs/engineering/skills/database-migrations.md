# Database migrations

Use this guide whenever changing a SQLModel table, column, index, or constraint,
creating an Alembic revision, adopting an existing database into Alembic, or
changing schema migration behavior in local or deployed environments.

## Schema ownership

Alembic is the only migration authority for SQLModel-owned tables:

- `chat_conversations`;
- `conversation_contexts`;
- `capability_artifacts`;
- `waiting_capability_invocations`;
- `capability_invocation_traces`;
- `capability_turn_receipts`; and
- `capability_call_receipts`.

Supabase Auth objects, `token_usage`, `user_credits`, row-level security
policies, and other provider-specific objects remain under
`supabase/migrations/`. Superseded `analysis_*` tables are unmanaged historical
objects. Alembic must ignore both groups and must never propose dropping them.

Do not introduce a second schema mutation path. Application startup must not
call `SQLModel.metadata.create_all`, run `ALTER TABLE`, or create indexes.
Temporary databases owned by unit tests may use `create_all` as fixture setup.

## Data API access

Supabase's Data API serves the `public` schema, and Supabase's default
privileges grant `anon` and `authenticated` access to each new object. Neither
role needs application tables: the backend connects as the tables' owner,
billing uses the service role, and the frontend uses Supabase for auth only.

`supabase/migrations/005_lock_down_data_api.sql` closes `public` to both roles.
Run it in the Supabase SQL editor after `alembic upgrade head`, as the role that
owns the tables. It changes every object the running role owns, directly or
through a role it inherits, and skips any other object with a warning that
names the object and its owner. It turns on row-level security for each table
it changes and revokes what PUBLIC, `anon` and `authenticated` hold on each
object it changes.

PostgreSQL records a grantor with each grant. Each REVOKE acts as one role and
removes only the grants with that role as grantor. For that role, PostgreSQL
picks either the running role or a role that owns the object or holds grant
options on it and whose privileges the running role holds; a superuser's
REVOKE acts as the object's owner. When several roles qualify, PostgreSQL
doesn't specify which one it picks, and its REVOKE reference recommends
`SET ROLE` to choose. A grant with any other grantor stays. 005 warns about
each grant that still reaches PUBLIC, `anon` or `authenticated`, names its
grantor, and gives SQL to revoke it as that role. If the grantor still holds
the necessary grant option in its own name, the warning gives:

```sql
set role <grantor>; revoke <privileges> on <object> from <grantee>; reset role;
```

A column grant's statement lists the column after the privilege; a direct
table-level option also authorizes a column revoke. Otherwise, 005 prints one
transaction for all the grants that grantor made to PUBLIC, `anon` and
`authenticated` on that object. The transaction acts as the owner to grant the
missing options, acts as the grantor to revoke the kept grants, then acts as
the owner to restore the grantor's prior ACL entries. It removes each temporary
privilege entirely unless the owner already granted that privilege to the
grantor on that object or column, in which case it removes only the option.
Because a table revoke also affects column privileges, a table restore then
reinstates the owner's prior column grants and their grant options. Recovery
orders downstream grantors before the grantor whose option they used.

Run the SQL in the order 005 prints it, from a session that can `SET ROLE` to
each named role. If the grantor made another grant that depends on a temporary
option, the restore fails with SQLSTATE 2BP01 and PostgreSQL rolls back the
whole transaction. The startup check below reports any table, view or
SECURITY DEFINER routine the remaining grants leave open.

Without CASCADE, PostgreSQL refuses to revoke a privilege from a role that has
granted it on through its grant option. When `anon` or `authenticated` has done
that, 005 revokes nothing from that role on that object and warns with the
CASCADE statement, which also revokes dependent grants.
005 never cascades by itself. Either run that statement and then 005 again, or
run the SQL that follows the warning. A column grant can outlive that CASCADE;
the rerun then prints the recovery transaction for it. PostgreSQL can still
refuse a revoke or restore without CASCADE when `anon` or `authenticated`
passed a grant option on to a role outside those three, or when the two gave
each other one.

005 also warns once for each direct membership through which `anon` or
`authenticated` inherits another role's privileges, and prints a quoted
`REVOKE <role> FROM <member>` statement. On PostgreSQL 16 and later it checks
the membership's `inherit_option` and adds `GRANTED BY <grantor>`, because from
16 a revoke removes only a membership that its own role granted; only a
superuser or a role with the grantor's privileges can run that statement.
Earlier versions check the member's `rolinherit`. Run each statement after
checking the membership's purpose: removing it affects every database on the
server. 005 never revokes memberships itself.

005 also removes the running role's default grants to those three in `public`.
On `user_credits` and `token_usage` it drops "Service manages credits" and
"Service inserts usage", two policies that admit every role. The "Users read
own" policies on both tables stay, so unless another policy admits `anon` or
`authenticated`, a SELECT grant someone adds back still reaches only the
signed-in user's rows.

Rerun 005 after a revision that adds a table or a function. The running role's
default privileges in `public` give a new table no grant for either role, and
the rerun turns on its row-level security. Until the rerun, other roles'
default privileges and default privileges for every schema still reach new
objects in `public`. 005 warns about each default for every schema that belongs
to the running role, or to a role whose privileges it holds, and grants `anon`
or `authenticated` anything, or grants PUBLIC anything on tables or sequences.
The startup check reports the tables, views and SECURITY DEFINER routines those
defaults open. PUBLIC can also call a new function until then: PostgreSQL
grants EXECUTE on each new function to PUBLIC, and revoking a default privilege
for one schema can't remove that grant.

005 waits at most 5 seconds for each lock. If a long query holds a table past
that, 005 stops and changes nothing, so run it again. It takes an exclusive
lock only to turn on row-level security or drop one of those two billing
policies, so a rerun locks only a table that still lacks row-level security or
still has one of them.

`verify_database_schema` refuses to start the backend when any of these holds:

- Row-level security would hide rows from the backend's role. A table has
  row-level security and either forces it or belongs to a role whose
  privileges the backend's role doesn't hold, and the backend's role lacks both
  superuser status and BYPASSRLS.
- The database's Alembic revision differs from the repository head.
- `anon` or `authenticated` can use the backend's schema and holds any
  privilege on a table, view, materialized view or foreign table in it, or on
  one of its columns, directly or through PUBLIC.
- Either role can use that schema and execute a SECURITY DEFINER function or
  procedure in it that belongs to no extension.

The check reads catalogs and the Alembic revision, never application rows.
Serving a table through the Data API takes a new Supabase migration with an
explicit policy and a matching change to that check.

## Revision creation

Alembic revision files MUST be autogenerated through the CLI:

```bash
ALEMBIC_DATABASE_URL=postgresql://... \
  python -m alembic -c backend/alembic.ini revision --autogenerate \
  -m "describe the schema change"
```

Never start by writing a revision file manually or by copying SQL into one. If
autogeneration proposes incorrect operations, correct the SQLModel metadata and
regenerate. Manual changes to generated operations are limited to:

- index or dialect details that metadata cannot express correctly;
- downgrade reversibility; or
- another objectively incorrect generated operation that cannot be corrected
  safely in metadata.

Record any such edit in the revision comment. Review every generated operation,
including its downgrade, before applying it.

Use `ALEMBIC_DATABASE_URL` for migration commands and `DATABASE_URL` for the
application. They may refer to the same database locally. Deployed migration
credentials may hold schema-change permissions that the runtime credential
lacks, but the runtime role must still own the tables, inherit their owner's
privileges, or have BYPASSRLS. Otherwise row-level security hides the tables'
rows from it, and the backend refuses to start. Never commit or print either
credential.

## Verification

Run migration lifecycle checks against disposable PostgreSQL, not SQLite:

```bash
ALEMBIC_DATABASE_URL=postgresql://... \
  python -m alembic -c backend/alembic.ini upgrade head
ALEMBIC_DATABASE_URL=postgresql://... \
  python -m alembic -c backend/alembic.ini check
ALEMBIC_DATABASE_URL=postgresql://... \
  python -m alembic -c backend/alembic.ini downgrade <previous-revision>
ALEMBIC_DATABASE_URL=postgresql://... \
  python -m alembic -c backend/alembic.ini upgrade head
```

Also run the focused migration and repository tests. A schema change is not
complete until a fresh upgrade, drift check, downgrade, and re-upgrade succeed,
and unmanaged Supabase and `analysis_*` objects remain unchanged.

## Existing database adoption

Before stamping an existing database, back it up and inspect its tables,
columns, indexes, constraints, and current row counts. Reconcile only the
SQLModel-owned objects to the exact baseline schema. Then stamp the matching
revision and upgrade normally. Never stamp merely to suppress a mismatch, and
never alter separately managed or historical objects as part of adoption.

Deployed migration orchestration may automate only the exact pre-branch
adoption case. When no Alembic revision is recorded, it compares the
SQLModel-owned structure with revision `0001` without reading conversation row
values. It stamps `0001` only when `chat_conversations` is the sole managed
table and its columns, types, nullability, primary key, and indexes match the
baseline exactly. An empty managed schema is upgraded normally. Any mismatch or
partial later schema fails before stamping.

Pull-request previews set `DATABASE_SCHEMA=uk_chat_pr_<number>`. Migration and
runtime engines use SQLAlchemy schema translation to qualify every unscoped
SQLModel or Alembic table with that validated schema. This does not rely on a
connection pool or database proxy honoring PostgreSQL startup options. Preview
tables and rows therefore cannot read or modify application tables in the
production schema even when the current infrastructure supplies the same server
credential. The Alembic connection also selects the same schema after connecting
because some generated alteration operations use unqualified table names.
Cleanup may remove only names matching that strict prefix.
Production and local development leave `DATABASE_SCHEMA` unset and continue to
use their configured default schema. A separately credentialed preview database
remains preferable when one is provisioned.
