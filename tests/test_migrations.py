from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

from friday.persistence.database import _driver_url

ROOT = Path(__file__).resolve().parents[1]


def test_0003_backfills_existing_devices_with_no_scopes(test_db_url: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", _driver_url(test_db_url).replace("%", "%%"))
    command.downgrade(cfg, "0002")
    try:
        with psycopg.connect(test_db_url) as conn:
            conn.execute("TRUNCATE users CASCADE")
            conn.execute(
                "INSERT INTO users (id, display_name) "
                "VALUES ('11111111-1111-1111-1111-111111111111', 'old')"
            )
            conn.execute(
                "INSERT INTO devices (id, user_id, name, device_type, token_hash) VALUES "
                "('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111',"
                " 'legacy', 'cli', %s)",
                ("a" * 64,),
            )
        command.upgrade(cfg, "head")
        with psycopg.connect(test_db_url) as conn:
            row = conn.execute("SELECT scopes FROM devices WHERE name = 'legacy'").fetchone()
            conn.execute("TRUNCATE users CASCADE")
        assert row == ([],)
    finally:
        command.upgrade(cfg, "head")
