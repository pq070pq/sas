# SAS PRO Standalone Migration

## Goal

Deliver SAS PRO as a standalone web app plus Android and iOS apps, with independent accounts, subscriptions, and notifications. Telegram remains available during migration and is retired only after the cutover criteria below pass.

## Safety boundaries

- Work only on `feature/sas-standalone-app`; do not merge to `main` or deploy production during the migration work.
- Do not change radar discovery, scoring, filtering, scheduling, provider fallback, or market-session logic as part of the app conversion.
- Keep the existing Telegram webhook, bot, Telegram Stars payments, and channel automation operational until user migration and payment reconciliation are complete.
- Back up the production database before any schema migration. Use additive migrations; do not drop or rename Telegram-specific columns during the transition.
- Never store payment provider secrets or signing keys in the frontend.

## Current repository findings

- Backend: FastAPI in `app/main.py`.
- Frontend: Arabic RTL HTML/CSS/JS under `web/`.
- Existing persistence: SQLAlchemy models in `app/db.py`.
- Existing user, subscription, payment, admin, and invite models rely on Telegram IDs and/or Telegram Stars.
- App startup currently configures Telegram and starts the radar scheduler in the same service. Standalone authentication must be added without making Telegram configuration mandatory or modifying radar startup.
- The frontend currently loads Telegram Web App JavaScript and has Telegram-specific subscription and channel UI. These must be abstracted behind platform-neutral account, billing, and notification interfaces before Telegram can be retired.

## Proposed architecture

1. Keep the existing FastAPI service and radar workers intact initially.
2. Add an internal immutable SAS user ID and identity-link table; preserve Telegram IDs as optional legacy identity links.
3. Add secure email-based account flows (verification, password reset, rate limits, hashed passwords, secure sessions) before enabling standalone sign-in.
4. Refactor subscription entitlement checks to use the internal SAS user ID, while keeping a compatibility path for existing Telegram subscribers during migration.
5. Introduce separate billing adapters: web payment provider and platform store purchases. Validate webhooks/receipts server-side and make fulfillment idempotent.
6. Add Capacitor packaging for Android/iOS after the web UI no longer requires Telegram WebApp initData to load core app screens.
7. Add push notification registration and per-device token storage; retain server-side scheduled radar execution.
8. Keep the responsive web app as the shared web client.

## Delivery phases and acceptance checks

### Phase 0 — Baseline and isolation
- [x] Create a feature branch from `main`.
- [ ] Capture baseline health, radar diagnostics, and subscription counts before any production migration.
- [ ] Add automated regression tests for radar scheduling, market gate, and existing Telegram entitlement checks.

### Phase 1 — Platform-neutral identity
- [ ] Add internal SAS user IDs and identity links using additive schema changes.
- [ ] Add standalone sign-in and account recovery.
- [ ] Verify existing Telegram users still work unchanged.
- [ ] Verify unauthenticated requests cannot access subscriber-only APIs.

### Phase 2 — Entitlements and billing
- [ ] Refactor entitlements to SAS user IDs with a temporary Telegram compatibility adapter.
- [ ] Add web checkout and server-side verified billing events.
- [ ] Add Apple/Google in-app purchase verification where required by platform policy.
- [ ] Reconcile historical Telegram Stars payments and prevent duplicate entitlement grants.

### Phase 3 — Shared web/mobile client
- [ ] Remove hard dependencies on Telegram WebApp for standalone routes.
- [ ] Add Capacitor Android/iOS shells and platform-specific configuration.
- [ ] Verify charts, navigation, session expiry, deep links, and responsive layouts on physical devices.

### Phase 4 — Notifications
- [ ] Add device registration and revocation.
- [ ] Add push notification provider and permission handling.
- [ ] Verify notification delivery while app is backgrounded/closed.

### Phase 5 — Controlled migration and retirement
- [ ] Invite a small test cohort and compare radar/API health against baseline.
- [ ] Migrate accounts and remaining subscription time with an auditable mapping.
- [ ] Keep Telegram as a fallback until all acceptance checks pass.
- [ ] Disable Telegram only after explicit cutover approval, then remove Telegram-specific code in a separate change.

## Release gates

- No unexplained increase in radar errors or change in candidate/pass counts under equivalent market data.
- Existing active subscribers retain their paid-through date.
- Billing webhook retries cannot grant duplicate subscriptions.
- Database backup and tested rollback procedure exist.
- Android/iOS release signing and store compliance are verified before public release.
