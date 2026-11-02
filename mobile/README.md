# SAS PRO Mobile shell

This branch adds a Capacitor launcher for the existing SAS PRO Telegram Mini App. It deliberately does **not** replace Telegram authentication or billing. The app hands off to the existing bot Mini App deep link so Telegram supplies the signed WebApp initData already validated by the backend. Existing user IDs, subscriptions, Stars payments, channel membership, bot, and radar remain unchanged.

## Public app configuration

The build has safe public defaults for the existing SAS PRO deployment:

- Bot username: `SAS_07_bot`
- Telegram Mini App short name: `saspro`
- Website URL: `https://vps-b67ef07b.vps.ovh.ca/`

Optional GitHub Actions repository variables with names `SAS_TELEGRAM_BOT_USERNAME`, `SAS_TELEGRAM_WEBAPP_SHORT_NAME`, and `SAS_APP_URL` can override these defaults. They are public routing values, not secrets.

No bot token, API key, password, or signing credential belongs in the app bundle.

## Build

The workflow runs on pushes to this feature branch that change `mobile/**` or the workflow file. It can also be started manually from **Actions → SAS PRO Mobile Build → Run workflow** on `feature/sas-mobile-app`. Android produces an unsigned debug APK for internal testing. iOS produces an Xcode project; a signed IPA requires Apple signing certificates/provisioning and a configured bundle identifier/team before distribution.

## Important authentication behavior

The current SAS backend authenticates protected API calls using Telegram Mini App initData. A native WebView opened directly at the website does not receive that signed payload. Therefore this first native shell opens the existing Telegram Mini App deep link rather than weakening backend authentication or inventing a second login. Users need Telegram installed and signed in. A fully embedded native session would require a separately designed, server-verified Telegram identity exchange and should be implemented as a separate reviewed phase.

## Safety

- Do not merge this branch into `main` or deploy it to production as part of this work.
- Do not change Telegram polling/webhooks, bot commands, subscription logic, payment logic, radar, or production environment settings.
