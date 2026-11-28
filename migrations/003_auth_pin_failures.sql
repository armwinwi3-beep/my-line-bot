-- Wrong admin-PIN attempts per client (SHA-256 of the client IP), so the lockout
-- works when each request may run on a fresh serverless instance (Vercel).
-- Only the backend's service-role key touches this table; RLS with no policies
-- blocks the anon key.
begin;
create table if not exists public.auth_pin_failures (
  client_key text primary key,
  count integer not null default 0,
  since double precision not null
);
alter table public.auth_pin_failures enable row level security;
commit;
