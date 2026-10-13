# Rushbase on TrueNAS SCALE (custom app via Docker Compose)

TrueNAS SCALE runs apps as containers. Rushbase ships a Compose file that works as a custom
app. The self-updater and the systemd watchdog of the bare-metal install are not available in
this mode; update by pulling a new image/commit and redeploying.

## Prerequisites

- TrueNAS SCALE 24.10 or newer with the Apps service enabled.
- A dataset for the media library (for example `tank/media/rushbase`), shared over SMB **and**
  readable/writable by the app (see "Permissions").
- A dataset for the database (for example `tank/apps/rushbase/postgres`). PostgreSQL must live
  on local storage, never on the SMB share.
- A copy of this repository on the NAS (for example `/mnt/tank/apps/rushbase/src`), so the server
  image can be built: `git clone https://github.com/Elias02345/RushBase.git`.

## Create the custom app

Apps > Discover Apps > Custom App > "Install via YAML". Paste a Compose definition based on
`docker-compose.yml` of this repository, adjusted for host paths:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: rushbase
      POSTGRES_PASSWORD: <generate: python3 -c "import secrets; print(secrets.token_urlsafe(24))">
      POSTGRES_DB: rushbase
    volumes:
      - /mnt/tank/apps/rushbase/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U rushbase -d rushbase"]
      interval: 5s
      timeout: 3s
      retries: 20
    restart: unless-stopped

  server:
    build:
      context: /mnt/tank/apps/rushbase/src
      dockerfile: deploy/docker/server.Dockerfile
    environment:
      RUSHBASE_SECRET_KEY: <generate: python3 -c "import secrets; print(secrets.token_urlsafe(48))">
      RUSHBASE_DATABASE_URL: postgresql+psycopg://rushbase:<db password>@postgres:5432/rushbase
      RUSHBASE_HOST: 0.0.0.0
      RUSHBASE_PORT: "8080"
      RUSHBASE_LIBRARY_ROOT: /library
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - /mnt/tank/media/rushbase:/library
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
```

Do not commit the filled-in file anywhere: it contains secrets. Keep a copy of the secret key
somewhere safe; losing it loses the data it protects.

After the first start, apply the migrations once from the app's shell (Apps > rushbase >
Containers > server > Shell): `alembic -c /app/server/alembic.ini upgrade head`.

## Permissions

The server container runs as UID 10001. Give that user write access to the library dataset:
either an ACL entry for UID 10001 (Datasets > Permissions) or run the SMB share and the app as
the same user. The container user must be able to create files in `<library>/.rushbase/`.

## Reaching the app

Port 8080 is published on `127.0.0.1` only. Expose it with a reverse proxy or tunnel running on
the NAS (for example the Cloudflare Tunnel app); see `deploy/cloudflared/config.yml.example` and
`deploy/caddy/Caddyfile.example`. For cgNAT setups where a bridge network does not work, use
`network_mode: host` as in `docker-compose.hostnet.yml` (PostgreSQL then needs its own port,
for example 15432, to avoid clashing with services on the NAS).

## SMB share caveats

- TrueNAS SMB datasets are case-insensitive by default (immutable after creation). Rushbase
  compares names with NFC + casefold keys and never overwrites on collision (ADR-0005).
- Workers on other machines mount the same share themselves; originals never flow through
  the server.

## Updating

Pull the new commit into the source dataset (`git -C /mnt/tank/apps/rushbase/src pull`),
rebuild and restart the app, then run the migrations again. Take a ZFS snapshot of the
database dataset first; it is the rollback path in this mode.
