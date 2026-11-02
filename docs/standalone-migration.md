# SAS PRO Standalone Migration

## Goal

Deliver SAS PRO as a standalone web app plus Android and iOS apps, with independent SAS accounts and notifications. The approved sign-in method is username and password; email and phone are not required. Existing Telegram subscriptions remain usable and Telegram stays operational.

## Safety boundaries

- Work only on `feature/sas-mobile-app`; do not merge to `main` or deploy production during this migration work.
- Do not change radar discovery, scoring, filtering, scheduling, provider fallback, or market-session logic as part of the app conversion.
- Keep the existing Telegram webhook, bot, Telegram Stars payments, and channel automation operational.
- Back up the production database before any schema migration. Use reviewed additive changes; do not drop or rename Telegram-specific columns.
- Never store payment provider secrets or signing keys in the frontend.

## Current repository findings

- Backend: FastAPI in `app/main.py`.
- Frontend: Arabic RTL HTML/CSS/JS under `web/`.
- Existing persistence: SQLAlchemy models in `app/db.py`.
- Existing user, subscription, payment, admin, and invite models rely on Telegram IDs and/or Telegram Stars.
- App startup configures Telegram and starts the radar scheduler in the same service. Standalone authentication must be added without making Telegram configuration mandatory or modifying radar startup.
- The frontend currently loads Telegram Web App JavaScript and has Telegram-specific subscription and channel UI. These must be separated behind authentication and entitlement adapters before standalone clients can use the core app.

## Proposed architecture

1. Keep the existing FastAPI service and radar workers intact initially.
2. Add an internal immutable SAS account UUID and identity-link table; preserve Telegram IDs as a separate legacy identity.
3. Add standalone username/password registration and authentication, hashed recovery codes, rate limits, revocable sessions, and secure password changes.
4. Refactor entitlement reads through the internal SAS account mapping while keeping a compatibility path for existing Telegram subscribers.
5. Keep billing fulfillment and Telegram Stars unchanged; do not introduce new billing providers as part of this login decision.
6. Add Capacitor Android/iOS packaging only after the client can load core app screens without Telegram WebApp initData.
7. Add push notification registration and per-device token storage; retain server-side scheduled radar execution.
8. Keep the responsive web app as the shared web client.

## Delivery phases and acceptance checks

### Phase 0 — Baseline and isolation
- [x] Create a feature branch from the existing application.
- [ ] Capture baseline health, radar diagnostics, and subscription counts before any production migration.
- [ ] Add regression tests for radar scheduling, market gate, and existing Telegram entitlement checks.

### Phase 1 — Platform-neutral identity
- [ ] Add internal SAS user IDs and identity links using reviewed additive schema changes.
- [ ] Add standalone username/password registration and login, logout, password change, and recovery codes.
- [ ] Verify existing Telegram users still work unchanged.
- [ ] Verify unauthenticated requests cannot access subscriber-only APIs.

### Phase 2 — Entitlements
- [ ] Resolve standalone entitlement reads through SAS account IDs with a Telegram compatibility adapter.
- [ ] Preserve existing paid-through dates and trial history exactly when accounts are linked.
- [ ] Prevent duplicate trials and duplicate entitlement grants.

### Phase 3 — Shared web/mobile client
- [ ] Remove hard dependencies on Telegram WebApp for standalone routes without changing Telegram routes.
- [ ] Add Capacitor Android/iOS shells and platform-specific configuration.
- [ ] Verify charts, navigation, session expiry, deep links, and responsive layouts on physical devices.

### Phase 4 — Notifications
- [ ] Add device registration and revocation.
- [ ] Add push notification provider and permission handling.
- [ ] Verify notification delivery while app is backgrounded/closed.

### Phase 5 — Controlled release
- [ ] Invite a small test cohort and compare radar/API health against baseline.
- [ ] Link accounts only after proof of ownership and preserve existing subscription entitlements.
- [ ] Keep Telegram available as a parallel client.
- [ ] Deploy or release only after explicit approval.

## Release gates

- No unexplained increase in radar errors or change in candidate/pass counts under equivalent market data.
- Existing active subscribers retain their paid-through date.
- Session and recovery-code protections pass security tests.
- Database backup and tested rollback procedure exist before migration.
- Android/iOS release signing and store compliance are verified before public release.
