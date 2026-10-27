# SAS PRO standalone authentication rollout

## Current state (2026-10-28)

- The standalone authentication API router remains intentionally **unmounted** in `app/main.py`.
- The current Telegram Mini App, Telegram identities, subscriptions, Stars payments, and production entry points are unchanged by this branch.
- CI passed on commit `9893749ca0b0a2d4c92be9b5f044dc1faad78c4c`: https://github.com/pq070pq/sas/actions/runs/40586638444
- CI passed on commit `83af2994b57e6e79e44cd13923e62a6d31d8f9d1`, including the expired-email-lease recovery test: https://github.com/pq070pq/sas/actions/runs/40587057550
- CI passed on the combined commit `58f658578a8c6c7ecddaaed5924165d815e1a27b`, including Python unit tests and Mini App JavaScript syntax checks: https://github.com/pq070pq/sas/actions/runs/40587390704
- Passing branch CI is not a substitute for staging/security review or concurrency tests with multiple independent workers.

## Remaining launch blockers

- [x] Encrypted transactional email outbox stores verification/reset emails in the same transaction as their tokens; retries and expired-lease recovery are implemented. Atomic compare-and-set claiming is added but needs a green CI run on the final commit and concurrency testing before release.
- [x] Isolated browser pages for verification and password reset are implemented on this branch with no-store/referrer/frame safety headers; the pages router remains unmounted.
- [x] Implement isolated ASGI middleware for standalone-route security headers and a 16 KiB streaming request-body cap; its focused tests are added and must pass CI before rollout. Middleware remains uninstalled in the production app.
- [ ] Production database migration/backup and rollback procedure for the additive standalone tables.
- [ ] Configure SMTP credentials and `STANDALONE_PUBLIC_ORIGIN` in deployment secrets; never commit credentials.
- [ ] Add multi-worker concurrency testing for atomic outbox claims, then stage with a separate database and restricted email recipient allowlist.
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
