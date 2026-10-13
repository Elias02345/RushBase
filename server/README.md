# rushbase-server

FastAPI coordinator: API, database, scanner, job queue, backups. Requires `RUSHBASE_SECRET_KEY` and
`RUSHBASE_DATABASE_URL` (see `.env.example`; every setting is documented there).

```sh
rushbase-server                      # start (add --force-takeover to override a live server lease)
rushbase-server migrate              # alembic upgrade head
rushbase-server backup now|list      # pg_dump into <library>/.rushbase/backups/
rushbase-server backup restore FILE  # restore into an empty database, then migrate
```

`pg_dump`/`pg_restore` come from `PATH` (the server image contains them), from
`RUSHBASE_PG_DUMP`/`RUSHBASE_PG_RESTORE`, or, in development only, from `docker exec` into
`RUSHBASE_PG_DOCKER_CONTAINER`.

The schema is described in `docs/architecture/data-model.md`.
