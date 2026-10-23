"""Durable Hacker panel configuration and subscriptions. No Discord calls here."""

from dataclasses import dataclass
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class Panel:
    guild_id: int
    channel_id: int
    message_id: int
    emoji: str
    role_id: int


class ReactionRoleStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connect(self):
        # sqlite's own context manager commits/rolls back but does not close.
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                yield db

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS panels (
                guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL, emoji TEXT NOT NULL, role_id INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS selections (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id))""")

    def panels(self):
        with self._connect() as db:
            return [Panel(*row) for row in db.execute("SELECT guild_id, channel_id, message_id, emoji, role_id FROM panels")]

    def save(self, panel: Panel):
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO panels VALUES (?, ?, ?, ?, ?)",
                       (panel.guild_id, panel.channel_id, panel.message_id, panel.emoji, panel.role_id))

    def selected(self, guild_id):
        with self._connect() as db:
            return {row[0] for row in db.execute("SELECT user_id FROM selections WHERE guild_id = ?", (guild_id,))}

    def is_tracked(self, guild_id, user_id):
        with self._connect() as db:
            return db.execute("SELECT 1 FROM selections WHERE guild_id = ? AND user_id = ?",
                              (guild_id, user_id)).fetchone() is not None

    def track(self, guild_id, user_id):
        # Record before assigning so a crash cannot leave an untracked grant.
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO selections VALUES (?, ?)", (guild_id, user_id))

    def forget(self, guild_id, user_id):
        with self._connect() as db:
            db.execute("DELETE FROM selections WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
