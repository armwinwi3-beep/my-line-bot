-- Run once in the Supabase SQL Editor as the project owner.
-- Existing transaction rows are not modified.
begin;
create table if not exists public.monthly_budgets (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  month date not null check (extract(day from month) = 1),
  category text not null check (length(category) between 1 and 120),
  amount numeric(14,2) not null check (amount > 0),
  unique(user_id, month, category)
);
create table if not exists public.recurring_rules (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  type text not null check (type in ('รายรับ', 'รายจ่ายต้องชำระต่อเดือน')),
  category text not null,
  account text not null,
  amount numeric(14,2) not null check (amount > 0),
  note text not null default '-',
  day_of_month integer not null check (day_of_month between 1 and 31),
  start_date date not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create index if not exists recurring_rules_user on public.recurring_rules(user_id);
-- Retain occurrence records even if a generated transaction is deleted manually.
create table if not exists public.recurring_occurrences (
  rule_id uuid not null references public.recurring_rules(id),
  month date not null,
  generated_at timestamptz not null default now(),
  primary key(rule_id, month)
);
alter table public.monthly_budgets enable row level security;
alter table public.recurring_rules enable row level security;
alter table public.recurring_occurrences enable row level security;
revoke all on public.monthly_budgets, public.recurring_rules, public.recurring_occurrences from anon, authenticated;
grant all on public.monthly_budgets, public.recurring_rules, public.recurring_occurrences to service_role;

create or replace function public.finance_generate_due(p_user_id text default null)
returns integer language plpgsql security definer set search_path = public, pg_temp as $$
declare
  rule public.recurring_rules%rowtype;
  month_start date;
  due_date date;
  today date := (now() at time zone 'Asia/Bangkok')::date;
  claimed integer;
  generated integer := 0;
begin
  for rule in select * from public.recurring_rules where active and (p_user_id is null or user_id = p_user_id)
  loop
    month_start := date_trunc('month', rule.start_date)::date;
    while month_start <= today loop
      due_date := month_start + (least(rule.day_of_month, extract(day from (month_start + interval '1 month - 1 day'))::integer) - 1);
      if due_date >= rule.start_date and due_date <= today then
        insert into public.recurring_occurrences(rule_id, month) values(rule.id, month_start) on conflict do nothing;
        get diagnostics claimed = row_count;
        if claimed = 1 then
          insert into public.transactions(user_id, date, time, type, amount, category, account, note, status)
          values(rule.user_id, to_char(due_date, 'DD/MM/YYYY'), '00:00:00', rule.type, rule.amount,
                 rule.category, rule.account, rule.note,
                 case when rule.type = 'รายรับ' then 'จ่ายแล้ว' else 'ยังไม่จ่าย' end);
          generated := generated + 1;
        end if;
      end if;
      month_start := (month_start + interval '1 month')::date;
    end loop;
  end loop;
  return generated;
end;
$$;
revoke all on function public.finance_generate_due(text) from public, anon, authenticated;
grant execute on function public.finance_generate_due(text) to service_role;
commit;

-- Database time is UTC; checking hourly also catches up after interruptions.
create extension if not exists pg_cron with schema pg_catalog;
select cron.schedule('moneybase-recurring-hourly', '0 * * * *', $$select public.finance_generate_due();$$);
