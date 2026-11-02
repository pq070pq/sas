# SAS PRO isolated Staging environment

## Scope and safety

This stack is an isolated test backend for the standalone SAS PRO mobile app. It is not a production deployment procedure.

- Work on `feature/sas-mobile-app` only.
- Do not merge this branch into `main` or run the production deployment workflow.
- The staging Compose project uses its own service/container, Compose network, SQLite database file, named volume, and loopback-only host port.
- Staging startup is explicitly gated by `SAS_STAGING_MODE=true`. In this mode FastAPI initializes only its own database and does **not** start Telegram configuration, Telegram polling, the main scheduler, or radar-health workers.
- The staging Compose file supplies empty Telegram settings even if a host shell has similarly named variables.
- Never mount the production data volume, reuse the production `.env`, copy production bot credentials, or expose port 8001 publicly.
- The production `docker-compose.yml`, production service, and existing production volume are not modified by this setup.

## Files

- `docker-compose.staging.yml`: independent Compose stack.
- `.env.staging.example`: safe template; copy it to the untracked `.env.staging` on the host and fill only staging credentials.
- Staging container: `saspro-staging`.
- Staging host bind: `127.0.0.1:8001 -> 8000`.
- Staging volume: `saspro_staging_data`.
- Staging database: `/app/data/saspro-staging.db`.
- Staging API origin: configure a dedicated HTTPS hostname such as `https://staging.YOUR-DOMAIN.example` in the reverse proxy and GitHub repository variable `SAS_API_BASE`. Do not point it at the production hostname.

## Host-side preparation (NOT YET EXECUTED)

Before any deployment, inspect the live host read-only and confirm available disk/RAM/CPU, Docker Compose version, current container/volume names, firewall, reverse-proxy configuration, TLS certificate availability, and whether the proposed subdomain resolves to the host. Confirm that `saspro_staging_data` does not already exist or is known to be disposable. Do not run `docker compose down`, `docker compose up`, `docker volume prune`, or any production deployment script during inspection.

After that separate review and explicit deployment approval:

1. In a dedicated checkout of this feature branch, copy `.env.staging.example` to `.env.staging`. Set unique random admin credentials and optional staging-only data provider keys. Do not add Telegram bot/channel credentials.
2. Validate the Compose configuration without starting containers:
   ```bash
   docker compose -p saspro-staging -f docker-compose.staging.yml --env-file .env.staging config
   ```
3. Build and start only the staging project:
   ```bash
   docker compose -p saspro-staging -f docker-compose.staging.yml --env-file .env.staging up -d --build
   ```
4. Verify `docker compose -p saspro-staging -f docker-compose.staging.yml ps`, inspect staging logs, and check `curl -fsS http://127.0.0.1:8001/health`.
5. Configure a separate HTTPS reverse-proxy virtual host to proxy only the staging subdomain to `127.0.0.1:8001`. Do not edit the production virtual host in place; use a separate file and validate the proxy configuration before reload.
6. Set GitHub repository variable `SAS_API_BASE` to the dedicated HTTPS staging origin. This is a public base URL, not a secret. Keep credentials in server environment secrets, never in GitHub source files or mobile bundles.
7. Build Android/iOS artifacts only after the staging API is live and its database/admin isolation is verified.

## Acceptance checks

- The staging container is the only container started by the staging Compose project.
- The staging database path is under the staging volume and is not the production database.
- Startup logs explicitly state staging mode and that Telegram/scheduler/radar-health workers are disabled.
- No Telegram API requests, webhook configuration, polling, channel actions, or production radar cycles originate from staging.
- The standalone registration, access expiry, admin login, and standalone market/radar read APIs work against staging only.
- Android/iOS build configuration points to the staging HTTPS origin.
- Production health, containers, volumes, database, Telegram bot, and radar are unchanged.

## Current state

The repository changes only prepare the isolated staging configuration and runtime guard. No SSH connection, host inspection, container build/start, DNS change, reverse-proxy change, GitHub variable change, or deployment has been performed.
