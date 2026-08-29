# Supabase-owned migrations

This directory is the migration history for Supabase-specific database objects.

- `001_billing.sql` and `003_token_usage_model_fields.sql` own billing tables
  that depend on Supabase Auth and row-level security behavior.
- `002_sharing.sql` and `004_conversation_session_unique.sql` are immutable
  historical files from before Alembic adoption. Their resulting conversation
  columns and indexes are represented by Alembic revision `0001`; do not use
  these files as the schema authority for a new database.

Alembic owns `chat_conversations` and every `capability_*` or
`waiting_capability_invocations` table. Do not add SQLModel table, column,
index, or constraint changes here. Follow
`docs/engineering/skills/database-migrations.md` instead.
