# Finance features

1. In the existing Supabase project's SQL Editor, run `migrations/001_finance.sql`. Enable the `pg_cron` integration before its final scheduling statement. The new tables are private; no anonymous access policies are added.
2. On the existing backend service, set `SUPABASE_SERVICE_ROLE_KEY` to this project's service-role key. Keep it on the backend only. Set `ADMIN_PIN` to the PIN used by the current admin login. Do not paste either value into GitHub or chat.
3. Deploy the backend before the frontend. The new routes verify LINE access tokens against LINE's profile API; admin requests require the server's PIN. Existing routes remain compatible.
4. Confirm `select * from cron.job where jobname = 'moneybase-recurring-hourly';` reports an active job. Confirm the latest `cron.job_run_details` record succeeds after the next hourly run.

The scheduler creates income records and unpaid bills in Bangkok time, clamps dates 29–31 to the month's last day, and atomically records each occurrence so concurrent scheduler/browser requests cannot duplicate a month. Paused schedules do not backfill paused months when resumed. Deleting a generated transaction does not regenerate it.

Reminders are in the web app; this release does not send LINE messages or browser push notifications. Budget spending includes paid ordinary expenses and paid monthly bills, excluding transfers and loans. Budgets are specific to the selected month and can be edited by saving the same category again.

Validate with `python -m unittest discover -s tests` in the backend and `node --test src/*.test.js` plus `npm run build` in the frontend. For a database smoke test, create one explicitly disposable schedule, check the generated row, run the function twice, and verify the second call returns zero. Do not use real account data for this test.
