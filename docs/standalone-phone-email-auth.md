# SAS PRO Standalone Authentication — Username and Password

## Approved product decision

The standalone Android, iOS, and web experience authenticates independently using:
- A unique SAS username.
- A password protected with a modern password-hashing algorithm.

Email address and mobile phone number are not required for sign-in or account creation. Do not add SMS or email OTP as a dependency for the primary login flow.

Telegram remains a separate, fully supported client. Do not remove or alter Telegram Mini App authentication, bot commands, Telegram Stars, channel membership, webhooks, polling, or Telegram subscription flows.

## Identity and entitlement model

- Use an immutable internal SAS account UUID as the platform-neutral principal.
- Store a normalized, case-insensitive unique username.
- Store only a password hash (prefer Argon2id; use a reviewed equivalent if deployment constraints require it). Never store plaintext passwords.
- Email and phone may be absent; they are not required identity keys.
- Keep Telegram user IDs in the legacy users table and represent account-to-Telegram association through an explicit identity-link record.
- Do not auto-link accounts solely because a user types a Telegram username, SAS username, phone number, email address, or Telegram user ID.
- Linking an existing Telegram subscriber requires proof of control of the Telegram account through a currently authenticated, signed Telegram Mini App session, plus proof of control of the SAS account. Use a short-lived, single-use linking challenge and audit the operation.
- Resolve subscription entitlements through a platform-neutral account mapping while retaining compatibility for existing Telegram-only users. Preserve remaining paid-through time and trial history exactly; never create a second trial or duplicate entitlement during linking.

## Password recovery

Because email and phone are not required, provide recovery codes:
- Generate a set of high-entropy, single-use recovery codes after account creation or during verified account linking.
- Display the codes once and require the user to save them securely.
- Store only hashes of recovery codes; consume each code atomically on use.
- Allow authenticated password changes after verifying the current password.
- Do not claim that support can recover a password without a valid recovery code or another independently verified proof of account ownership.
- Do not silently fall back to Telegram username or a user-entered Telegram ID as recovery proof.

## Authentication and session security

- Use Argon2id password hashing with per-password salts and parameters appropriate to the server.
- Enforce sensible password length and reject commonly compromised passwords where a local safe mechanism is available; do not impose arbitrary composition rules.
- Apply per-account and per-IP rate limits, progressive delays, and generic login errors that do not disclose whether a username exists.
- Use revocable, expiring server sessions with cryptographically secure opaque tokens stored hashed server-side, or an equivalently reviewed secure token design.
- Native clients store session credentials in iOS Keychain / Android Keystore through secure platform storage.
- Require HTTPS, strict CORS, CSRF protection for cookie-based web sessions, session rotation, logout/revocation, and audit events for credential and identity changes.
- Never place password hashes, recovery-code hashes, signing keys, or long-lived secrets in the mobile bundle.

## Compatibility and isolation

- The existing Telegram `validate_init_data` dependency remains unchanged for Telegram routes.
- Add a distinct standalone authentication dependency for standalone routes. Never accept client-supplied Telegram IDs as proof of identity.
- Keep Telegram-specific routes and standalone API namespaces separate.
- Do not change the radar, market data providers, scheduler, subscription fulfillment, production configuration, or production database during this feature-branch phase.
- Any future database migration must be additive where possible, reviewed, backed up, and tested against a production-like copy before deployment. Existing Telegram users and tables must remain valid.

## Delivery sequence

1. Inspect current account, subscription, migration, and API dependencies; document compatibility points.
2. Update the additive SAS account schema for a normalized unique username, password hash, and hashed single-use recovery codes. Make email optional rather than a required login identifier.
3. Implement standalone registration, login, logout, password change, and recovery-code endpoints with abuse controls.
4. Implement secure account linking from a verified Telegram session, without automatic matching.
5. Add standalone entitlement reads through the compatibility adapter.
6. Refactor the shared frontend to support standalone sessions while retaining the Telegram Mini App path unchanged.
7. Build native Android/iOS clients with secure token storage and test on devices.
8. Complete security review, migration rehearsal, backup/rollback plan, and explicit approval before any production deployment.

## Acceptance criteria

- A user can create an independent SAS account and sign in with username and password without Telegram installed.
- Email and phone are not required to create or use the account.
- Recovery codes are shown once, stored only as hashes, single-use, and tested.
- Existing Telegram Mini App login, bot, Stars payments, subscriptions, and channel access continue unchanged.
- Linking an existing subscriber preserves the exact entitlement expiry and trial history.
- Brute force, replay, session theft, username enumeration, recovery-code reuse, and account takeover tests pass.
- No production deployment or Telegram change occurs without separate explicit approval.
