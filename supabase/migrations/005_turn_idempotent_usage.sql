alter table token_usage
  add column if not exists turn_id text;

create unique index if not exists uq_token_usage_session_turn
  on token_usage(session_id, turn_id)
  where turn_id is not null;

create or replace function record_chat_usage_idempotent(
  p_user_id uuid,
  p_session_id text,
  p_turn_id text,
  p_model text,
  p_input_tokens int,
  p_output_tokens int,
  p_cache_creation_input_tokens int,
  p_cache_read_input_tokens int,
  p_cost_gbp numeric,
  p_free_tier_gbp numeric
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  inserted_id bigint;
  credits user_credits%rowtype;
  free_remaining numeric;
  overflow numeric;
begin
  if p_turn_id is null or btrim(p_turn_id) = '' then
    raise exception 'turn identifier is required';
  end if;

  insert into token_usage (
    user_id,
    session_id,
    turn_id,
    model,
    input_tokens,
    output_tokens,
    cache_creation_input_tokens,
    cache_read_input_tokens,
    cost_gbp
  ) values (
    p_user_id,
    p_session_id,
    p_turn_id,
    p_model,
    p_input_tokens,
    p_output_tokens,
    p_cache_creation_input_tokens,
    p_cache_read_input_tokens,
    p_cost_gbp
  )
  on conflict (session_id, turn_id) where turn_id is not null do nothing
  returning id into inserted_id;

  if inserted_id is null then
    return false;
  end if;

  if p_user_id is null then
    return true;
  end if;

  insert into user_credits (
    user_id,
    balance_gbp,
    free_tier_used_gbp,
    free_tier_reset_at
  ) values (p_user_id, 0, 0, now())
  on conflict (user_id) do nothing;

  select * into credits
  from user_credits
  where user_id = p_user_id
  for update;

  if now() >= credits.free_tier_reset_at + interval '1 month' then
    credits.free_tier_used_gbp := 0;
    credits.free_tier_reset_at := now();
  end if;

  free_remaining := greatest(0, p_free_tier_gbp - credits.free_tier_used_gbp);
  if p_cost_gbp <= free_remaining then
    update user_credits
    set free_tier_used_gbp = credits.free_tier_used_gbp + p_cost_gbp,
        free_tier_reset_at = credits.free_tier_reset_at
    where user_id = p_user_id;
  else
    overflow := p_cost_gbp - free_remaining;
    update user_credits
    set free_tier_used_gbp = p_free_tier_gbp,
        balance_gbp = greatest(0, credits.balance_gbp - overflow),
        free_tier_reset_at = credits.free_tier_reset_at
    where user_id = p_user_id;
  end if;
  return true;
end;
$$;

revoke execute on function record_chat_usage_idempotent(
  uuid, text, text, text, int, int, int, int, numeric, numeric
) from public, anon, authenticated;

grant execute on function record_chat_usage_idempotent(
  uuid, text, text, text, int, int, int, int, numeric, numeric
) to service_role;
