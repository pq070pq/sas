# SAS PRO Standalone Migration

## Goal

Deliver SAS PRO as a standalone web app plus Android and iOS apps. Public app users enter without registration or login. A server-side installation record controls access duration and block status through a separate protected admin console. Telegram remains a parallel, unchanged product.

## Safety boundaries

- Work only on feature/sas-mobile-app; do not merge into main or deploy production during migration work.
- Do not change radar discovery, scoring, filtering, scheduling, provider fallback, or market-session logic.
- Keep the existing Telegram webhook, bot, Telegram Stars payments, channel automation, Telegram admin workflows, and subscription fulfillment operational and unchanged.
- Do not migrate Telegram users into anonymous installation records.
- Use reviewed additive schema changes; never run migrations against production during feature development.
- Never store admin credentials or signing keys in the frontend/mobile bundle.

## Current repository findings

- Backend: FastAPI in app/main.py.
- Frontend: Arabic RTL HTML/CSS/JS under web/.
- Persistence: SQLAlchemy models in app/db.py.
- Existing Telegram users, subscriptions, payments, and admin roles are Telegram-ID based.
- App startup configures Telegram and starts the radar scheduler in the same service. Standalone access must be added without changing Telegram startup or radar startup.
- The current web client has Telegram-specific dependencies. Standalone screens and APIs must be separated without breaking Telegram routes.

## Proposed architecture

1. Keep the existing FastAPI service and radar workers intact initially.
2. Add a standalone installation record with a random opaque installation token, token hash, first/last seen timestamps, access start/expiry, status, and audit metadata.
3. Add a separate standalone admin identity/session mechanism. Public users have no account credentials; admin authentication remains mandatory.
4. Enforce access expiry and blocked status server-side for standalone APIs.
5. Apply a fixed 30-day initial access period to every new installation. Provide admin controls to extend/reduce each installation's access, set an exact expiry, grant permanent access, block/unblock, and review the audit log.
6. Keep Telegram login, Mini App, Stars, subscriptions, and admin flows unchanged.
7. Add Capacitor Android/iOS packaging only after standalone core screens work without Telegram WebApp initData.
8. Add push notification registration later without coupling it to Telegram.

## Delivery phases and acceptance checks

### Phase 0 — Isolation
- [x] Use the dedicated feature/sas-mobile-app branch.
- [ ] Capture baseline health and Telegram/radar regression tests before integration.
- [ ] Confirm standalone APIs do not require Telegram initData.

### Phase 1 — Anonymous installation access
- [ ] Add installation and standalone access schema using additive changes.
- [ ] Generate random installation IDs; do not use hardware identifiers or fingerprinting.
- [ ] Implement server-authoritative registration, expiry, and blocked-state checks.
- [ ] Apply 30 days of access automatically to every new installation.

### Phase 2 — Admin management
- [ ] Add protected standalone admin authentication and revocable sessions.
- [ ] Add list/search/filter and detail views for installations.
- [ ] Add expiry extensions/reductions by chosen duration, exact expiry changes, permanent access, block/unblock, and audit history.
- [ ] Add a protected Arabic RTL admin console.

### Phase 3 — Standalone web/mobile client
- [ ] Separate standalone routes from Telegram WebApp dependencies without changing Telegram routes.
- [ ] Add Capacitor Android/iOS shells.
- [ ] Add access-expired and blocked states.
- [ ] Test secure installation-ID storage and reinstall/device-change behavior.

### Phase 4 — Controlled release
- [ ] Test all access and admin flows on an isolated environment.
- [ ] Verify Telegram Mini App, bot, channel, Stars, subscriptions, admin, and radar regression tests.
- [ ] Review backup and rollback plans.
- [ ] Release only after explicit approval.

## Release gates

- Expiry and blocked state are enforced by the server, not only by UI.
- Anonymous clients cannot access admin APIs or alter their own expiry.
- Admin changes are audited and revocable.
- Installation identity limitations are documented; no invasive hardware fingerprinting is used.
- Existing Telegram and radar behavior remains unchanged.
- No production deployment without separate explicit approval.

## Implemented in the feature branch

- Additive tables for anonymous installation access, standalone admin sessions, and standalone admin audit history.
- Public registration returns a random installation token once; only its hash is persisted.
- Server-side access checks enforce 30-day expiry, block, and revoke states.
- A separate Arabic admin console and APIs support search, status counts, expiry extension, exact expiry, permanent access, block/unblock, and audit history.
- Admin login is configured only through server environment secrets. No default admin password is shipped.
- Remaining integration: standalone client screens must register/store the token and attach it to protected standalone API requests. Existing Telegram APIs are deliberately untouched.
