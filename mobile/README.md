# SAS PRO Mobile — Standalone App Migration

This feature branch is the isolated workspace for turning SAS PRO into a standalone Android/iOS app. The approved sign-in method is **SAS username and password**. Email and phone are not required.

## Product separation

- The standalone app must open and authenticate without Telegram installed.
- The existing Telegram Mini App, bot, channel, Telegram Stars billing, and Telegram login remain operational and unchanged.
- The standalone client uses a separate server-side authentication/session flow. It must never forge or bypass Telegram WebApp initData.
- Existing Telegram subscribers may link accounts only after proving control of both identities. Subscription time and trial history must be preserved exactly.

## Current build status

The current APK/Xcode artifacts are an early launcher shell, not the finished standalone application. The launcher currently hands off to the Telegram Mini App and therefore does not meet the standalone requirement. Do not distribute it as the final independent app.

## Implementation plan

See `docs/standalone-phone-email-auth.md` for the approved username/password identity, recovery-code, account-linking, security, compatibility, and acceptance requirements. (The historical filename is retained to avoid unnecessary path churn.)

1. Additive SAS identity schema for normalized usernames, password hashes, and hashed single-use recovery codes.
2. Standalone registration, login, logout, password change, and recovery-code flows with abuse controls.
3. Standalone API authentication separated from Telegram authentication.
4. Verified account linking and shared entitlement reads without duplicate trials or payments.
5. Shared responsive client support for native standalone sessions while retaining the Telegram Mini App path.
6. Native Android/iOS secure token storage, push notifications, device testing, and signed release builds.

## Non-negotiable safety boundaries

- Work only on `feature/sas-mobile-app`; do not merge into `main` or deploy production without separate explicit approval.
- Do not modify Telegram bot code, Mini App auth, Telegram Stars, channel automation, webhooks, polling, or existing subscription fulfillment.
- Do not modify radar discovery, scoring, filtering, scheduling, market data providers, or production database.
- Use additive migrations only after review, backup, and rehearsal on a production-like copy.
- Keep signing keys and long-lived secrets in server-side secrets, never in the mobile bundle.
- Do not claim the app is standalone until it passes the acceptance criteria in the authentication specification.
