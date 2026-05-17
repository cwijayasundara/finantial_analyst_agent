"""Thin wrapper around the official neo4j driver.

Singleton driver per process (the driver is itself a connection pool;
creating multiple defeats its purpose). Sessions are context-managed
and bound to the configured database.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from neo4j import GraphDatabase, Driver, Session

from cookbooks._shared.config import load_settings


_driver: Driver | None = None


def driver() -> Driver:
    """Return the process-wide singleton driver. Build it on first call."""
    global _driver
    if _driver is None:
        s = load_settings().neo4j
        _driver = GraphDatabase.driver(s.url, auth=(s.user, s.password))
    return _driver


def close_driver() -> None:
    """Tear down the driver. Tests use this to keep instances isolated."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


@contextmanager
def session(read_only: bool = False) -> Iterator[Session]:
    """Yield a Session against the configured database.

    `read_only=True` hints to the driver to prefer a follower replica
    (no-op in single-instance Community; harmless to set).
    """
    s = load_settings().neo4j
    mode = "READ" if read_only else "WRITE"
    with driver().session(database=s.database, default_access_mode=mode) as sess:
        yield sess
