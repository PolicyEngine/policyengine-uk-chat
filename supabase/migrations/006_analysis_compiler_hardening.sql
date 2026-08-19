-- Durable state for the version-two analysis compiler. Every statement is
-- repeatable so local reset, continuous integration, and staged deployment use
-- the same migration.

create table if not exists analysis_workflows (
  session_id text primary key,
  schema_version int not null default 2,
  state_version int not null default 0,
  phase text not null,
  active_bound_request_id text,
  active_execution_id text,
  pending_plan_id text,
  snapshot_json text not null,
  updated_at timestamptz not null
);

alter table analysis_workflows
  add column if not exists active_bound_request_id text,
  add column if not exists active_execution_id text,
  add column if not exists pending_plan_id text;

create table if not exists analysis_request_revisions (
  revision_id text primary key,
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  schema_version int not null default 2,
  revision_number int not null,
  turn_id text not null,
  payload_json text not null,
  created_at timestamptz not null,
  constraint uq_analysis_revision_number unique (session_id, revision_number)
);

create table if not exists analysis_bound_requests (
  bound_request_id text primary key,
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  request_revision_id text not null references analysis_request_revisions(revision_id),
  schema_version int not null default 2,
  capability_version text not null,
  payload_json text not null,
  created_at timestamptz not null
);

create table if not exists analysis_clarifications (
  question_id text primary key,
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  request_revision_id text not null references analysis_request_revisions(revision_id),
  schema_version int not null default 2,
  payload_json text not null,
  created_at timestamptz not null
);

create table if not exists analysis_clarification_resolutions (
  resolution_id text primary key,
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  question_id text not null references analysis_clarifications(question_id),
  request_revision_id text not null references analysis_request_revisions(revision_id),
  resolving_turn_id text not null,
  schema_version int not null default 2,
  outcome text not null,
  payload_json text not null,
  created_at timestamptz not null,
  constraint uq_analysis_clarification_resolution unique (session_id, question_id)
);

create table if not exists analysis_plans (
  plan_id text primary key,
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  request_revision_id text not null references analysis_request_revisions(revision_id),
  bound_request_id text not null
    constraint fk_analysis_plans_bound_request
    references analysis_bound_requests(bound_request_id),
  schema_version int not null default 2,
  plan_hash text not null,
  status text not null default 'ready',
  payload_json text not null,
  created_at timestamptz not null
);

alter table analysis_plans
  add column if not exists bound_request_id text;

create table if not exists analysis_execution_attempts (
  execution_id text primary key,
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  request_revision_id text not null references analysis_request_revisions(revision_id),
  bound_request_id text not null references analysis_bound_requests(bound_request_id),
  plan_id text not null references analysis_plans(plan_id),
  plan_hash text not null,
  token_hash text not null,
  schema_version int not null default 2,
  status text not null,
  worker_id text not null,
  payload_json text not null,
  claimed_at timestamptz not null,
  heartbeat_at timestamptz not null,
  lease_expires_at timestamptz not null,
  completed_at timestamptz
);

create unique index if not exists uq_analysis_active_attempt_session
  on analysis_execution_attempts(session_id)
  where status in ('claimed', 'running', 'cancellation_requested');

create table if not exists analysis_turn_receipts (
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  turn_id text not null,
  schema_version int not null default 2,
  request_hash text not null,
  state_version int not null,
  status text not null,
  outcome_category text,
  response_content text,
  response_metadata_json text not null default '{}',
  usage_id text,
  response_checksum text,
  created_at timestamptz not null,
  primary key (session_id, turn_id)
);

alter table analysis_turn_receipts
  add column if not exists outcome_category text,
  add column if not exists response_metadata_json text not null default '{}',
  add column if not exists usage_id text,
  add column if not exists response_checksum text;

create table if not exists analysis_model_usage (
  usage_entry_id text primary key,
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  turn_id text not null,
  schema_version int not null default 2,
  operation text not null,
  model text not null,
  input_tokens int not null default 0,
  output_tokens int not null default 0,
  cache_creation_input_tokens int not null default 0,
  cache_read_input_tokens int not null default 0,
  created_at timestamptz not null,
  constraint uq_analysis_model_usage_entry
    unique (session_id, turn_id, operation, usage_entry_id)
);

create table if not exists analysis_billing_intents (
  billing_intent_id text primary key,
  session_id text not null references analysis_workflows(session_id) on delete cascade,
  turn_id text not null,
  user_id text,
  schema_version int not null default 2,
  status text not null,
  payload_json text not null,
  created_at timestamptz not null,
  constraint uq_analysis_billing_turn unique (session_id, turn_id)
);

alter table analysis_billing_intents
  add column if not exists user_id text;

-- Existing deployments may already have the parent tables. Add the new
-- relationships separately so the migration remains additive and repeatable.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'fk_analysis_plans_bound_request'
  ) then
    alter table analysis_plans
      add constraint fk_analysis_plans_bound_request
      foreign key (bound_request_id)
      references analysis_bound_requests(bound_request_id)
      not valid;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'fk_analysis_workflows_active_bound_request'
  ) then
    alter table analysis_workflows
      add constraint fk_analysis_workflows_active_bound_request
      foreign key (active_bound_request_id)
      references analysis_bound_requests(bound_request_id)
      deferrable initially deferred
      not valid;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'fk_analysis_workflows_active_execution'
  ) then
    alter table analysis_workflows
      add constraint fk_analysis_workflows_active_execution
      foreign key (active_execution_id)
      references analysis_execution_attempts(execution_id)
      deferrable initially deferred
      not valid;
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'fk_analysis_workflows_pending_plan'
  ) then
    alter table analysis_workflows
      add constraint fk_analysis_workflows_pending_plan
      foreign key (pending_plan_id)
      references analysis_plans(plan_id)
      deferrable initially deferred
      not valid;
  end if;
end
$$;

create index if not exists ix_analysis_workflows_active_bound_request_id
  on analysis_workflows(active_bound_request_id);
create index if not exists ix_analysis_workflows_active_execution_id
  on analysis_workflows(active_execution_id);
create index if not exists ix_analysis_workflows_pending_plan_id
  on analysis_workflows(pending_plan_id);
create index if not exists ix_analysis_bound_requests_revision
  on analysis_bound_requests(request_revision_id);
create index if not exists ix_analysis_plans_bound_request
  on analysis_plans(bound_request_id);
create index if not exists ix_analysis_execution_attempts_lease
  on analysis_execution_attempts(lease_expires_at);
create index if not exists ix_analysis_model_usage_turn
  on analysis_model_usage(session_id, turn_id);
create index if not exists ix_analysis_billing_intents_user_status
  on analysis_billing_intents(user_id, status);
