alter table token_usage
  add column if not exists model text,
  add column if not exists cache_creation_input_tokens int not null default 0,
  add column if not exists cache_read_input_tokens int not null default 0;
