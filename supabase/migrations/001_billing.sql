-- Token usage log: one row per chat turn
create table if not exists token_usage (
  id bigint generated always as identity primary key,
  user_id uuid references auth.users(id),
  session_id text not null,
  input_tokens int not null default 0,
  output_tokens int not null default 0,
  cost_gbp numeric(12,6) not null default 0,
  created_at timestamptz not null default now()
);

-- User credits: one row per user
create table if not exists user_credits (
  id bigint generated always as identity primary key,
  user_id uuid unique not null references auth.users(id),
  balance_gbp numeric(12,6) not null default 0,
  free_tier_used_gbp numeric(12,6) not null default 0,
  free_tier_reset_at timestamptz not null default date_trunc('month', now()),
  created_at timestamptz not null default now()
);

-- RLS: users can read their own data only
alter table token_usage enable row level security;
alter table user_credits enable row level security;

create policy "Users read own usage" on token_usage
  for select using (auth.uid() = user_id);

create policy "Users read own credits" on user_credits
  for select using (auth.uid() = user_id);

-- Service role (backend) can insert/update anything
create policy "Service inserts usage" on token_usage
  for insert with check (true);

create policy "Service manages credits" on user_credits
  for all using (true) with check (true);

-- Indexes
create index if not exists idx_token_usage_user on token_usage(user_id, created_at desc);
create index if not exists idx_user_credits_user on user_credits(user_id);
