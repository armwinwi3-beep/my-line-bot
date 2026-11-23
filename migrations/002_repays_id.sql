-- Link a repayment (ได้คืนจากลูกหนี้) to the loan (ให้ยืมเงิน) it pays off.
-- Run once in the Supabase SQL Editor before deploying the backend that writes repays_id.
-- Existing rows are not modified; old repayments stay unlinked and are matched oldest-loan-first.
begin;
alter table public.transactions
  add column if not exists repays_id bigint
  references public.transactions(id) on delete set null;
create index if not exists transactions_repays_id on public.transactions(repays_id);
commit;
