# SAS PRO Mobile — Standalone App Migration

This feature branch is the isolated workspace for turning SAS PRO into a standalone Android/iOS app. The approved sign-in methods are **verified phone number or verified email**.

## Product separation

- The standalone app must open and authenticate without Telegram installed.
- The existing Telegram Mini App, bot, channel, Telegram Stars billing, and Telegram login remain operational and unchanged.
- The standalone client will use a separate server-side authentication/session flow. It must never forge or bypass Telegram WebApp initData.
- Existing Telegram subscribers may link accounts only after proving control of both identities. Subscription time and trial history must be preserved exactly.

## Current build status

The current APK/Xcode artifacts are an early launcher shell, not the finished standalone application. The launcher currently hands off to the Telegram Mini App and therefore does not meet the new standalone requirement yet. Do not distribute it as the final independent app.

## Implementation plan

See `docs/standalone-phone-email-auth.md` for the approved identity, OTP, account-linking, security, compatibility, and acceptance requirements.

1. Additive identity schema for phone/email, OTP challenges, and revocable sessions.
2. SMS and email one-time-code delivery with abuse controls and secure verification.
3. Standalone API authentication separated from Telegram authentication.
4. Verified account linking and shared entitlement reads without duplicate trials or payments.
5. Shared responsive client support for native standalone sessions while retaining the Telegram Mini App path.
6. Native Android/iOS secure token storage, push notifications, device testing, and signed release builds.

## Non-negotiable safety boundaries

- Work only on `feature/sas-mobile-app`; do not merge into `main` or deploy production without separate explicit approval.
- Do not modify Telegram bot code, Mini App auth, Telegram Stars, channel automation, webhooks, polling, or existing subscription fulfillment.
- Do not modify radar discovery, scoring, filtering, scheduling, market data providers, or production database.
- Use additive migrations only after review, backup, and rehearsal on a production-like copy.
- Keep SMS/email provider credentials and signing keys in server-side secrets, never in the mobile bundle.
- Do not claim the app is standalone until it passes the acceptance criteria in the migration specification.