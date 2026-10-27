# SAS PRO standalone authentication rollout

## Current state (2026-10-28)

- The standalone authentication API router remains intentionally **unmounted** in `app/main.py`.
- The current Telegram Mini App, Telegram identities, subscriptions, Stars payments, and production entry points are unchanged by this branch.
- CI passed on the latest outbox retry-test fix `613f7cbe5fa9ac143a9c8964c782f18881a73b7c`, including compile, all unit tests, and Mini App JavaScript validation: https://github.com/pq070pq/sas/actions/runs/40590963523
- The retry test now normalizes SQLite's naive datetime result to UTC before comparing it with an aware datetime; the test is still asserting the retry is scheduled after the reference time.
- Latest guard-test CI passed on commit `495613f4842b253db1e47f797e3134b42afc897e`, including compile, unit tests, and Mini App JavaScript validation: https://github.com/pq070pq/sas/actions/runs/40591495136
- The production deploy workflow now explicitly gates its job to `refs/heads/main`; a separate run for the preceding workflow-only commit was cancelled when the newer test commit queued, so use the passing guard-test run above as the latest green CI record.
- CI passed on the latest schema-preflight commit `d1fae1bb1f0e66ea95d385b72718f731ca42d622`, including compile, unit tests, and Mini App JavaScript validation: https://github.com/pq070pq/sas/actions/runs/40590060956
- Earlier CI runs below document incremental validation history; the latest green run is the source of truth for the current branch head.
- CI passed on commit `9893749ca0b0a2d4c92be9b5f044dc1faad78c4c`: https://github.com/pq070pq/sas/actions/runs/40586638444
- CI passed on commit `83af2994b57e6e79e44cd13923e62a6d31d8f9d1`, including the expired-email-lease recovery test: https://github.com/pq070pq/sas/actions/runs/40587057550
- CI passed on the combined commit `58f658578a8c6c7ecddaaed5924165d815e1a27b`, including Python unit tests and Mini App JavaScript syntax checks: https://github.com/pq070pq/sas/actions/runs/40587390704
- Passing branch CI is not a substitute for staging/security review or concurrency tests with multiple independent workers.

## Remaining launch blockers

- [x] Encrypted transactional email outbox stores verification/reset emails in the same transaction as their tokens; retries and expired-lease recovery are implemented. Atomic compare-and-set claiming and a multi-worker concurrency test are covered by green CI on the latest branch head. Staging crash/restart and SMTP tests remain required.
- [x] Isolated browser pages for verification and password reset are implemented on this branch with no-store/referrer/frame safety headers; the pages router remains unmounted.
- [x] Implement isolated ASGI middleware for standalone-route security headers and a 16 KiB streaming request-body cap; its focused tests are added and must pass CI before rollout. Middleware remains uninstalled in the production app.
- [ ] Production database migration/backup and rollback procedure for the additive standalone tables.
- [ ] Configure SMTP credentials and `STANDALONE_PUBLIC_ORIGIN` in deployment secrets; never commit credentials.
- [x] Add multi-worker concurrency test for atomic outbox claims; CI passed on commit `39db885dc7f12db576f30c4c1f393db423e32f50`: https://github.com/pq070pq/sas/actions/runs/40588050983
- [x] Verify latest branch-head CI after schema-preflight column checks: https://github.com/pq070pq/sas/actions/runs/40590060956
- [x] Verify the email retry failure path and timezone-safe assertion on the latest branch head: https://github.com/pq070pq/sas/actions/runs/40590963523
- [ ] Stage with a separate database, restricted email recipient allowlist, and a verified backup/restore procedure.
- [ ] Run Telegram regression checks and verify payment callbacks, subscriptions, and the existing Mini App.
- [ ] Review reverse-proxy configuration and trusted client IP handling before enabling rate limits behind a proxy.

## Security controls already implemented on this branch

- Passwords are PBKDF2-HMAC-SHA256 hashes; raw one-time tokens are not stored.
- Verification and reset tokens are single-use; password reset revokes existing sessions.
- Login requires a verified and active account.
- Public registration/reset responses avoid revealing account existence.
- Session cookie is Secure, HttpOnly, and SameSite=Lax.
- Login/logout require a same-origin `Origin` header.
- Database-backed per-IP and per-identity fixed-window rate limits use hashed bucket keys and persist independently of failed auth transactions.
- Public email links use a configured HTTPS origin, not the request Host header.

## Rollout sequence

1. Keep work on `feature/sas-standalone-next`; do not merge/deploy automatically.
2. Complete outbox crash recovery/concurrency tests and security headers/body-size limits.
3. Require green CI for the exact final commit and inspect the actual test output.
4. Configure SMTP secrets and the fixed public origin in a staging environment.
5. Run end-to-end browser tests and verify no email token or password appears in logs.
6. Verify Telegram bot, Mini App, subscriptions, and payment callbacks are unchanged.
7. Enable behind a feature flag only after explicit approval; keep an immediate standalone-only rollback switch.
8. Production merge/deploy requires explicit user approval.

## Safety invariants

- Existing Telegram `users`, `subscriptions`, and `payments` remain unchanged.
- Standalone routes remain unmounted until all blockers are resolved.
- Never report production readiness based only on unit tests.


## Staging checklist (do not use production credentials)

Before mounting either standalone router in any environment:

- [ ] Create an isolated staging database and verify the existing Telegram tables are not copied into a writable shared staging database.
- [ ] Take a database backup and perform a restore into a separate disposable database; record the result and rollback command.
- [ ] Set `STANDALONE_PUBLIC_ORIGIN` to the exact HTTPS staging origin.
- [ ] Set SMTP host, port, username, password, sender address, and TLS options using the supported `STANDALONE_` environment variables in the deployment secret store.
- [ ] Generate a dedicated Fernet key for `STANDALONE_EMAIL_FERNET_KEY`; store it separately in the secret store and document key rotation/recovery. Never reuse a key from another environment.
- [ ] Restrict staging email delivery to an allowlist of test recipients at the SMTP/provider layer.
- [ ] Configure proxy and firewall rules so rate limiting uses the actual trusted peer address; do not trust arbitrary forwarded headers.
- [ ] Apply additive standalone-table migrations only in staging, then verify backup/restore and rollback before considering production.
- [ ] Test registration, verification expiry/reuse, login, logout, reset expiry/reuse, session revocation, email retry, worker restart during send, and two-worker contention.
- [ ] Verify no passwords, one-time tokens, SMTP credentials, or decrypted email bodies appear in application/proxy logs.
- [ ] Run Telegram bot, Mini App, Stars payment, subscription renewal, and callback regression checks.
- [ ] Keep standalone routes unmounted until the entire checklist is signed off and explicit approval is given.


## Database migration and rollback review

The application currently uses SQLAlchemy `Base.metadata.create_all` inside `init_db()`; there is no Alembic migration directory in this repository. That means a future deployment/restart of a revision containing the additive standalone models may create their tables automatically, even while the standalone routers are unmounted. Do not treat “routers unmounted” as “no database schema change.”

Before any production deployment containing these models:

1. Confirm the exact production database URL and database engine without printing credentials into logs or chat.
2. Take a consistent backup using the database engine's supported procedure. For SQLite, stop or quiesce writers and back up the database together with its WAL state, or use SQLite's online backup API; do not copy only the main `.db` file while WAL writes may be active.
3. Restore that backup into a separate disposable database and verify row counts for `users`, `subscriptions`, and `payments`, plus integrity checks.
4. Deploy only the additive schema/model revision with standalone routes still unmounted; inspect that the existing tables and rows are unchanged and that only expected `sas_*` tables/indexes were added.
5. Re-run Telegram login, Mini App, Stars payment callbacks, subscription checks, and radar smoke tests.
6. Rollback application code without deleting the additive `sas_*` tables. Do not drop tables or restore an old backup over live writes as a routine rollback; preserve new data and perform a separately reviewed recovery.
7. Since there is no versioned migration framework yet, add a formal, reviewed migration mechanism before schema changes that modify existing tables or become operationally complex.

This is a documented procedure, not a claim that a backup, restore, or production migration has been performed.


## Read-only schema preflight command

A read-only schema inventory is available on the work branch:

```bash
python -m app.standalone_schema_preflight
```

It reports whether the required legacy Telegram/payment tables and columns exist and whether the additive standalone tables are present. It does not create or alter tables. A successful exit only means the required legacy columns were found; the report deliberately keeps `safe_to_enable_routes` false because the command cannot validate backups, restored data, SMTP delivery, reverse-proxy trust, or end-to-end behavior. Run it against staging first and preserve its output without database credentials.


## GitHub-only workflow (no laptop or SSH access)

The owner may continue this work from a phone using GitHub only. For each code change:
- Keep changes on `feature/sas-standalone-next`.
- Production deploy job has an explicit `github.ref == 'refs/heads/main'` condition to block manual dispatch from feature branches; the test suite asserts this guard remains present.
- Use GitHub Actions as the automated compile/unit-test/Mini App syntax check.
- Inspect the exact run and job-step conclusions for the latest commit before describing it as passed.
- Keep routers unmounted and do not deploy or merge to `main`.
- Do not paste database credentials, SMTP passwords, Fernet keys, bot tokens, or production database contents into issues, commits, workflow logs, or chat.

GitHub Actions currently validates code in its CI environment; it does not prove the live server database was backed up/restored, production SMTP works, or the Telegram payment flow works in production. Those remain explicit rollout blockers until a safe staging environment is available.
