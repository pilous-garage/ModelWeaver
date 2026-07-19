"""Bases d'echange distantes (community.db, user.db).

Ces bases sont VIDES par defaut et ne recoivent RIEN des bases locales
(modelweaver.db, catalogue.db local) sans un flux opt-in explicite.

Regle de securite centrale : aucune fuite local -> distant par defaut.
- modelweaver.db (usage/clés) ne sort JAMAIS.
- catalogue.db local est un miroir distant -> local (sens unique).
- community.db : données ANONYMISEES futures (usage, budget_inféré...).
- user.db      : données NON anonymes futures (recettes, certifs humaines).

On ne cree ici que la coquille (table meta témoin) pour matérialiser la
frontiere. Le contenu réel viendra plus tard, et uniquement via un push
volontaire.
"""

import sqlite3
from pathlib import Path

from .db import _default_community_db, _default_user_db


_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
"""


def _init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_META_SCHEMA)
    # Témoin : base vide, destinée à l'échange distant, aucun push auto.
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
        ("purpose", "exchange-db: empty by default, no local->distant push"),
    )
    conn.commit()
    return conn


class ExchangeDB:
    """Coquille pour une base d'echange (community/user). Vide, témoin."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.conn = _init_db(self.db_path)

    def purpose(self) -> str:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='purpose'"
        ).fetchone()
        return row[0] if row else ""

    def close(self):
        self.conn.close()


def get_community_db() -> ExchangeDB:
    return ExchangeDB(_default_community_db())


def get_user_db() -> ExchangeDB:
    return ExchangeDB(_default_user_db())
