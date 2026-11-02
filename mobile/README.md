# SAS PRO Mobile — Standalone App

This feature branch is the isolated workspace for the independent SAS PRO Android/iOS app.

## Approved access model

- Public users do not register or log in. No username, password, email, phone, or Telegram login is required.
- Each installation receives a random installation identifier stored in platform-protected storage where available.
- Server-side access records control whether that installation is active, blocked, expired, or permanent.
- Every new installation automatically receives 30 days of access; the admin can extend or change its expiry at any time.
- A separate protected admin console manages installations, blocks/unblocks access, extends or changes expiry, grants permanent access, and reviews activity.

See docs/standalone-phone-email-auth.md for the full anonymous-access, expiry, admin-security, API, and acceptance requirements. The historical filename is retained to avoid path churn.

## Important identity limitation

An installation ID identifies an app installation, not a verified person. Reinstalling, clearing protected storage, or changing devices can create a new installation identity. Do not use invasive hardware fingerprinting or claim person-level enforcement.

## Current build status

The current APK/Xcode artifacts are an early launcher shell that hands off to the Telegram Mini App. They are not the finished standalone app and must not be distributed as such.

## Implementation plan

1. Add standalone installation-access and admin-session schema, isolated from Telegram models.
2. Implement device registration and server-side access checks.
3. Implement admin APIs for users/installations, block status, duration, permanent access, and audit history.
4. Build a protected Arabic RTL admin console.
5. Build standalone app screens and secure installation-ID storage.
6. Test expiry, blocking, admin authorization, Android/iOS behavior, and Telegram regressions.

## Non-negotiable safety boundaries

- Work only on feature/sas-mobile-app; do not merge into main or deploy production without separate explicit approval.
- Do not modify Telegram bot code, Mini App auth, Telegram Stars, channel automation, webhooks, polling, or existing subscription fulfillment.
- Do not modify radar discovery, scoring, filtering, scheduling, market data providers, or production database.
- Use additive schema changes and test on an isolated database before any deployment.
- Admin secrets remain server-side and must never be embedded in the mobile bundle.
- Do not claim the app is standalone until it passes the acceptance criteria.

## Admin setup

- Open `/admin/standalone` after deploying the feature in a test environment.
- Set `SAS_STANDALONE_ADMIN_USERNAME` and `SAS_STANDALONE_ADMIN_PASSWORD` as server-side secrets before logging in.
- The first successful device registration receives 30 days. The admin can extend by 30/90/custom days, set an exact expiry, make access permanent, or block/unblock the installation.
- The admin console is independent from Telegram admin login. It does not change Telegram users or subscriptions.


## Independent mobile application

- The Android/iOS app is now a standalone SAS PRO interface and does not open Telegram.
- On first native launch, the server issues an opaque installation token. The app stores it with `@aparajita/capacitor-secure-storage` using Android Keystore-backed encryption and iOS Keychain.
- The token is sent in `X-SAS-Installation`. Browser preview refuses to use insecure browser storage for this credential.
- Build configuration requires the repository variable `SAS_API_BASE` to point to a separately isolated staging API. The workflow refuses to build against the current production SAS host.
- Protected standalone data routes expose market status, ticker, movers, news, and saved radar signals. Existing Telegram routes and guards remain unchanged.
- Do not distribute builds until the matching backend version and admin environment secrets have been deployed and verified in a separate test environment.


- The native client creates a 48-byte cryptographically random token and stores it in Keychain/Keystore before registering. The server stores only its SHA-256 digest; no hardware identifiers are collected.
- Admin secrets are read from `SAS_STANDALONE_ADMIN_USERNAME` and `SAS_STANDALONE_ADMIN_PASSWORD` (or their unprefixed equivalents) in the server environment only.


**Release safety:** The standalone client must not be built or distributed against the production API until a separate staging deployment, database, and admin secret set have been verified. The GitHub mobile workflow now requires an explicit staging `SAS_API_BASE` and blocks the known production hostname.
