# Billing and credits

The `billing` router (`routes/billing.py`) prices each chat turn by token usage,
tracks per-user credit in Supabase, and tops up balances through Stripe.

## Cost model

Cost is computed from Anthropic token usage and converted to GBP:

```text
cost_usd = input_tokens        × input_rate
         + output_tokens       × output_rate
         + cache_creation_tok  × cache_write_rate
         + cache_read_tok      × cache_read_rate      (per million tokens)

cost_gbp = cost_usd × USD_TO_GBP × (1 + MARKUP_RATE)
```

Rates are defined per model in `MODEL_PRICING_USD_PER_MTOK` (USD per **million**
tokens). The table includes cache-write and cache-read rates, which matter
because the large `reference.md` reference is prompt-cached:

```{list-table}
:header-rows: 1

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

`calculate_cost_gbp(...)` performs the conversion. An unknown model name falls
back to the default billing model's pricing.

## Configuration

```{list-table}
:header-rows: 1

* - Variable
  - Default
  - Meaning
* - `BILLING_MARKUP_RATE`
  - `0.0`
  - Multiplier added on top of raw cost.
* - `BILLING_FREE_TIER_GBP`
  - `5.0`
  - Free credit granted per period.
* - `BILLING_USD_TO_GBP`
  - `0.79`
  - USD→GBP conversion rate.
* - `ANTHROPIC_DEFAULT_MODEL`
  - `claude-haiku-4-5`
  - Pricing fallback model.
```

## Credits

Credit state lives in the Supabase `user_credits` table (accessed with the
service-role key, bypassing RLS). `get_or_create_credits(user_id)`:

- creates a row on first use,
- tracks `balance_gbp` and `free_tier_used_gbp`,
- **resets the free tier monthly** (via `free_tier_reset_at` + one month).

The chatbot route calls into these helpers to debit usage as conversations run.

## Endpoints

```{list-table}
:header-rows: 1
:widths: 30 70

* - Endpoint
  - Description
* - `GET /billing/balance`
  - Current credit balance for the user.
* - `GET /billing/usage`
  - Usage and cost history.
* - `POST /billing/checkout`
  - Create a Stripe checkout session to add credit.
* - `POST /billing/webhook`
  - Stripe webhook receiver; confirms payment and credits the account.
```

```{note}
Stripe keys (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`) are optional for local
development — checkout simply won't function without them, but the rest of the
app runs.
```
