# SAS PRO Mobile shell

This branch adds a Capacitor launcher for the existing SAS PRO Telegram Mini App. It deliberately does **not** replace Telegram authentication or billing. The app hands off to the existing bot Mini App deep link so Telegram supplies the signed WebApp initData already validated by the backend. Existing user IDs, subscriptions, Stars payments, channel membership, bot, and radar remain unchanged.

## Required GitHub repository variables

Set these under **Settings → Secrets and variables → Actions → Variables** (public configuration values, not secrets):

- `SAS_TELEGRAM_BOT_USERNAME`: bot username without `@`.
- `SAS_TELEGRAM_WEBAPP_SHORT_NAME`: configured Telegram Mini App short name.
- `SAS_APP_URL`: current HTTPS SAS website URL, used as a fallback link.

No bot token, API key, password, or signing credential belongs in these variables or in the app bundle.

## Build

Run **Actions → SAS PRO Mobile Build → Run workflow** on `feature/sas-mobile-app`. Android produces an unsigned debug APK for internal testing. iOS produces an Xcode project; a signed IPA requires Apple signing certificates/provisioning and a configured bundle identifier/team before distribution.

## Important authentication behavior

The current SAS backend authenticates protected API calls using Telegram Mini App initData. A native WebView opened directly at the website does not receive that signed payload. Therefore this first native shell opens the existing Telegram Mini App deep link rather than weakening backend authentication or inventing a second login. Users need Telegram installed and signed in. A fully embedded native session would require a separately designed, server-verified Telegram identity exchange and should be implemented as a separate reviewed phase.

## Safety

- Do not merge this branch into `main` or deploy it to production as part of this work.
- Do not change Telegram polling/webhooks, bot commands, subscription logic, payment logic, radar, or production environment settings.
