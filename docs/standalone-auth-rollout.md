# SAS PRO standalone authentication rollout

## Current state (2026-10-28)

- The standalone authentication API router remains intentionally **unmounted** in `app/main.py`.
- The current Telegram Mini App, Telegram identities, subscriptions, Stars payments, and production entry points are unchanged by this branch.
- The branch CI passed on commit `0d6902e9169d3a594020a83390190904a9bf11a3`: https://github.com/pq070pq/sas/actions/runs/40583190868
- Standalone API tests cover account lifecycle, email-verification/reset tokens, session revocation, Origin checks, and database-backed rate limits. Passing branch CI is not a substitute for staging/security review.

## Remaining launch blockers

- [ ] Durable transactional email outbox with retry/idempotency. Current SMTP delivery happens inline and is not crash-safe; do not expose registration/reset endpoints publicly until this is addressed.
- [ ] Browser pages for the verification and password-reset links sent in email.
- [ ] Security headers and explicit request-body size limits for the standalone endpoints.
- [ ] Production database migration/backup and rollback procedure for the additive standalone tables.
- [ ] Configure SMTP credentials and `STANDALONE_PUBLIC_ORIGIN` in deployment secrets; never commit credentials.
- [ ] Stage with a separate database and restricted email recipient allowlist.
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
2. Implement the durable email outbox and isolated browser pages, with tests.
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
