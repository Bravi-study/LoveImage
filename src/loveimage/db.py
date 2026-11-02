import json
from pathlib import Path

import asyncpg

MIGRATIONS = Path(__file__).parents[2] / "migrations"
MIGRATION_LOCK = 727002


async def _init(conn):
    await conn.set_type_codec("jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads)


async def create_pool(dsn):
    # A search_path set on the role must not move our tables to another schema.
    return await asyncpg.create_pool(
        dsn, min_size=1, max_size=5, command_timeout=10, init=_init, server_settings={"search_path": "public"}
    )


async def migrate(pool):
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK})")
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name not in done:
                await conn.execute(path.read_text())
                await conn.execute("INSERT INTO schema_migrations VALUES ($1)", path.name)
