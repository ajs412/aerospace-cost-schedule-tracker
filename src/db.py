"""Thin database helper shared by all analytics modules."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from src.config import DB_PATH


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()