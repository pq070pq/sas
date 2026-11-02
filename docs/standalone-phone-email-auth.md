# SAS PRO Standalone Authentication — Phone or Email

## Approved product decision

The standalone Android, iOS, and web experience must authenticate independently using either:
- A verified mobile phone number (one-time code by SMS).
- A verified email address (one-time code by email).

Telegram remains a separate, fully supported client. Do not remove or alter Telegram Mini App authentication, bot commands, Telegram Stars, channel membership, webhooks, polling, or Telegram subscription flows.

## Identity and entitlement model

- Use an immutable internal SAS account UUID as the platform-neutral principal.
- Store phone and email as independently normalized, uniquely indexed identifiers. Either may be absent, but at least one must be verified before an account can authenticate.
- Store verification state and timestamps separately for phone and email.
- Keep Telegram user IDs in the legacy users table and represent account-to-Telegram association through an explicit identity-link record.
- Do not auto-link accounts solely because a user types a Telegram username, phone number, email address, or Telegram user ID.
- Linking an existing Telegram subscriber requires proof of control of the Telegram account (existing signed Mini App session or a one-time, short-lived, single-use linking challenge initiated from that authenticated session) plus proof of control of the phone/email account.
- Resolve subscription entitlements through a platform-neutral account mapping while retaining a compatibility adapter for existing Telegram-only users. Preserve remaining paid-through time and trial history; never create a second trial or duplicate entitlement during linking.

## Authentication requirements

- Separate OTP flows for SMS and email, with cryptographically secure random codes, short expiry, single use, attempt limits, resend cooldowns, IP/account rate limits, and generic responses that do not disclose whether an identifier is registered.
- Store only a keyed hash of OTP challenges; never log OTP values.
- Verify delivery-provider webhooks where applicable. Keep provider credentials in server-side environment secrets only.
- Issue revocable, expiring server sessions using secure random opaque tokens stored hashed server-side, or an equivalently reviewed secure token design. Native clients store session credentials in iOS Keychain / Android Keystore through secure platform storage.
- Require HTTPS, CSRF protection for cookie-based web sessions, strict CORS, audit events for account linking, and session revocation on credential changes.
- Do not put OTPs, signing keys, provider API keys, or long-lived secrets in the mobile bundle.

## Compatibility and isolation

- The existing Telegram `validate_init_data` dependency remains unchanged for Telegram routes.
- Add a distinct standalone authentication dependency for standalone routes. Never accept client-supplied Telegram IDs as proof of identity.
- Keep Telegram-specific routes and the standalone API namespaces separate.
- Do not change the radar, market data providers, scheduler, subscription fulfillment, production configuration, or production database during this feature-branch phase.
- Any future database migration must be additive, reviewed, backed up, and tested against a production-like copy before deployment.

## Delivery sequence

1. Inspect current account, subscription, migration, and API dependencies; document compatibility points.
2. Add additive phone/email identity and OTP challenge/session schema plus unit tests.
3. Implement SMS/email delivery adapters and standalone OTP endpoints with rate limits and abuse controls.
4. Implement secure account linking from a verified Telegram session, with no automatic matching.
5. Add standalone entitlement reads through the compatibility adapter.
6. Refactor the shared frontend to support Telegram and standalone session adapters without changing the Telegram flow.
7. Build native Android/iOS clients and test on devices.
8. Complete security review, migration rehearsal, backup/rollback plan, and explicit approval before any production deployment.

## Acceptance criteria

- A user can sign in using either verified phone or verified email.
- A user can use both identifiers on the same account after separately verifying them.
- Existing Telegram Mini App login, bot, Stars payments, subscriptions, and channel access continue unchanged.
- Linking an existing subscriber preserves the exact entitlement expiry and does not issue a second trial.
- OTP brute force, replay, enumeration, session theft, and account takeover tests pass.
- No production deployment or Telegram change occurs without separate explicit approval.