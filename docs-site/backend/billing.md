# Billing and credits

The `billing` package (`backend/billing/`) prices each chat turn by its token
usage, tracks per-user credit in Supabase, and tops up balances through Stripe.
Billing is opt-in and currently disabled in production and preview deploys.
It is split across four modules:

```{list-table}
:header-rows: 1
:widths: 25 75

* - Module
  - Responsibility
* - `routes.py`
  - HTTP endpoints (balance, usage, checkout, webhook).
* - `pricing.py`
  - Cost model — converts token counts to a GBP cost.
* - `credits.py`
  - Credit state in Supabase (balance, free tier, usage records).
* - `stripe.py`
  - Stripe checkout sessions and webhook handling.
```

## Cost model

Defined in `backend/billing/pricing.py`. Each turn's cost is derived from the
four Anthropic token counters, priced per million tokens, then converted to GBP
with the currency rate and markup applied:

```text
cost_usd = input_tokens       × input_rate
         + output_tokens      × output_rate
         + cache_creation_tok × cache_write_rate
         + cache_read_tok     × cache_read_rate     (rates are per million tokens)

cost_gbp = cost_usd × BILLING_USD_TO_GBP × (1 + BILLING_MARKUP_RATE)
```

The conversion is performed by `calculate_cost_gbp(...)`. An unknown model name
falls back to the default billing model's pricing.

```{note}
The compute system prompt and tool definitions are prompt-cached. Cache-write
and cache-read rates therefore remain part of per-turn billing.
```

### Pricing table

USD per **million** tokens:

```{list-table}
:header-rows: 1
:widths: 28 18 18 18 18

* - Model
  - Input
  - Output
  - Cache write (5m)
  - Cache read
* - `claude-haiku-4-5`
  - $1.00
  - $5.00
  - $1.25
  - $0.10
* - `claude-sonnet-4-6`
  - $3.00
  - $15.00
  - $3.75
  - $0.30
```

## Configuration

```{list-table}
:header-rows: 1
:widths: 32 18 50

* - Environment variable
  - Default
  - Meaning
* - `BILLING_ENABLED`
  - `false`
  - Enables credit enforcement, usage writes, and billing HTTP endpoints.
* - `BILLING_MARKUP_RATE`
  - `0.0`
  - Multiplier added on top of the raw cost.
* - `BILLING_FREE_TIER_GBP`
  - `5.0`
  - Free credit granted per period (monthly).
* - `BILLING_USD_TO_GBP`
  - `0.79`
  - USD→GBP conversion rate.
* - `ANTHROPIC_DEFAULT_MODEL`
  - `claude-haiku-4-5`
  - Pricing fallback model.
```

## Credits

Credit state lives in the Supabase `user_credits` table, accessed with the
service-role key (bypassing RLS). The key functions in
`backend/billing/credits.py` are:

- `get_or_create_credits(user_id)` — creates a row on first use and tracks
  `balance_gbp` and `free_tier_used_gbp`. It **resets the free tier monthly**,
  when `now >= free_tier_reset_at + 1 month`.
- `record_usage(...)` — writes a row to the `token_usage` table and deducts the
  cost from the free tier first, then from the paid balance.

When `BILLING_ENABLED=true`, the public chat service calls these to debit usage
as conversations run. The per-turn `done` SSE event then reports the turn's
`cost_gbp` and the remaining balance. With billing disabled, the service does
not create a Supabase client, enforce credit, or record usage.

## Endpoints

All defined in `backend/billing/routes.py` and mounted at the root:

```{list-table}
:header-rows: 1
:widths: 32 68

* - Endpoint
  - Description
* - `GET /billing/balance`
  - Current credit balance summary for the user (requires `user_id`).
* - `GET /billing/usage`
  - Token usage / cost history from the `token_usage` table (optional `limit`,
    default 50).
* - `POST /billing/checkout`
  - Create a Stripe checkout session to add credit.
* - `POST /billing/webhook`
  - Stripe webhook receiver — verifies the signature and credits the account.
```

When billing is disabled, every `/billing` endpoint returns `404`. Modal secret
sync also omits the backend Supabase and Stripe credentials, even if they remain
stored in GitHub Actions.

```{note}
The Stripe keys (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`) are optional for
local development. Checkout won't function without them, but the rest of the app
runs.
```
