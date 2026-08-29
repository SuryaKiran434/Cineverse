"""Regression tests for information exposure through exceptions (CWE-209).

``GET /test-db`` used to be written as::

    try:
        ...
    except Exception as e:
        return {"error": str(e)}

so any failure inside the handler was serialised straight back to the caller.
That is a real leak rather than a theoretical one: the exceptions that reach
this handler come from ``mysql.connector``, and their ``str()`` carries the
host, the port, the database name, the account the app connects as and the
server version -- everything an attacker needs to fingerprint the backend.

The fix is a single ``@app.exception_handler(Exception)`` on the application
rather than a ``try``/``except`` per endpoint, so the guarantee also covers
routes added later and the three routers mounted from ``auth``, ``protected``,
``watchlist`` and ``tmdb``. These tests pin down both halves of that handler:
the client sees a fixed generic message, and the detail (with its traceback)
is still written to the server log.

Everything is offline in the style of ``tests/conftest.py``: the ``database``
module is stubbed in ``sys.modules``, and each test swaps
``main.get_db_connection`` for an in-memory fake, so no MySQL connection is
ever attempted.
"""

import ast
import asyncio
import logging
import os

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import main

from conftest import REPO_ROOT


# Stand-in for the kind of text a real mysql-connector error carries. If any of
# this reaches an HTTP response the test suite should fail loudly.
LEAKY_DETAIL = (
    "2003 (HY000): Can't connect to MySQL server on 'db.internal.example:3306' "
    "(111 Connection refused) [user=cineverse_app, schema=cineverse_prod]"
)


class ExplodingConnection:
    """A connection whose first use raises, the way a dropped socket would."""

    def __init__(self):
        self.closed = False

    def cursor(self, dictionary=False):
        raise RuntimeError(LEAKY_DETAIL)

    def close(self):
        self.closed = True


class WorkingConnection:
    def __init__(self, db_name="cineverse"):
        self.db_name = db_name
        self.closed = False

    def cursor(self, dictionary=False):
        return self

    def execute(self, query, params=()):
        assert query.strip().lower().startswith("select database()")

    def fetchone(self):
        return (self.db_name,)

    def close(self):
        self.closed = True


@pytest.fixture
def client():
    """A client that returns the 500 rather than re-raising it.

    Starlette's ``ServerErrorMiddleware`` re-raises after running an
    ``Exception`` handler so the ASGI server can log it. ``TestClient``
    honours that by default, which would hide the very response under test,
    hence ``raise_server_exceptions=False``.
    """
    return TestClient(main.app, raise_server_exceptions=False)


# --------------------------------------------------------------------------
# The leak itself
# --------------------------------------------------------------------------


def test_driver_error_does_not_reach_the_client(client, monkeypatch):
    connection = ExplodingConnection()
    monkeypatch.setattr(main, "get_db_connection", lambda: connection)

    response = client.get("/test-db")

    assert response.status_code == 500
    body = response.text
    assert LEAKY_DETAIL not in body
    assert "db.internal.example" not in body
    assert "cineverse_prod" not in body
    assert "RuntimeError" not in body
    assert "Traceback" not in body
    assert response.json() == {"error": "Internal server error"}


def test_the_detail_is_still_written_to_the_server_log(client, monkeypatch, caplog):
    """Suppressing the response must not mean losing the diagnostic."""
    connection = ExplodingConnection()
    monkeypatch.setattr(main, "get_db_connection", lambda: connection)

    with caplog.at_level(logging.ERROR, logger=main.__name__):
        client.get("/test-db")

    assert caplog.records, "the unhandled error was swallowed without a log entry"
    record = caplog.records[-1]
    assert record.exc_info is not None, "the traceback was not captured"
    assert "/test-db" in record.getMessage()
    assert LEAKY_DETAIL in logging.Formatter().format(record)


def test_the_handler_is_registered_on_the_application(client):
    """The guarantee is app-wide, not one endpoint's ``try``/``except``."""
    assert Exception in main.app.exception_handlers


def test_the_handler_returns_the_same_generic_body_for_any_exception():
    """The handler itself, exercised directly rather than through a route.

    Whatever the exception type or how much detail its message carries, the
    body it produces is a fixed constant.
    """
    handler = main.app.exception_handlers[Exception]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/anything",
        "headers": [],
        "query_string": b"",
    }

    result = asyncio.run(handler(Request(scope), ValueError(LEAKY_DETAIL)))

    assert result.status_code == 500
    assert LEAKY_DETAIL.encode() not in result.body
    assert result.body == b'{"error":"Internal server error"}'


# --------------------------------------------------------------------------
# The endpoint still does its job
# --------------------------------------------------------------------------


def test_test_db_happy_path_is_unchanged(client, monkeypatch):
    connection = WorkingConnection("cineverse")
    monkeypatch.setattr(main, "get_db_connection", lambda: connection)

    response = client.get("/test-db")

    assert response.status_code == 200
    assert response.json() == {"message": "Connected to database: cineverse"}
    assert connection.closed is True


def test_a_missing_connection_is_still_reported_as_before(client, monkeypatch):
    """``get_db_connection`` returning ``None`` is an expected outcome, not a
    crash, and its message contains nothing sensitive."""
    monkeypatch.setattr(main, "get_db_connection", lambda: None)

    response = client.get("/test-db")

    assert response.status_code == 200
    assert response.json() == {"error": "Database connection failed"}


def test_the_connection_is_closed_even_when_the_query_explodes(client, monkeypatch):
    """The handler no longer swallows the error, so it must not leak the socket."""

    class FailingQuery(WorkingConnection):
        def execute(self, query, params=()):
            raise RuntimeError(LEAKY_DETAIL)

    connection = FailingQuery()
    monkeypatch.setattr(main, "get_db_connection", lambda: connection)

    response = client.get("/test-db")

    assert response.status_code == 500
    assert connection.closed is True


# --------------------------------------------------------------------------
# Audit guard: no handler anywhere may return an exception's text
# --------------------------------------------------------------------------


def _application_modules():
    for name in sorted(os.listdir(REPO_ROOT)):
        if name.endswith(".py") and not name.startswith("_"):
            yield os.path.join(REPO_ROOT, name)


def test_no_module_returns_the_text_of_a_caught_exception():
    """Catches the ``except Exception as e: return {"error": str(e)}`` shape
    coming back anywhere in the app, not just in ``main.py``.

    Walks every ``except ... as <name>`` block and fails if the bound name is
    reachable from a ``return`` inside it. Raising ``HTTPException`` with a
    message the endpoint chose is fine; handing back the exception is not.
    """
    offenders = []

    for path in _application_modules():
        with open(path, encoding="utf-8") as source:
            tree = ast.parse(source.read(), filename=path)

        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler) or not handler.name:
                continue
            bound = handler.name
            for node in ast.walk(handler):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                for inner in ast.walk(node.value):
                    if isinstance(inner, ast.Name) and inner.id == bound:
                        offenders.append(
                            f"{os.path.basename(path)}:{node.lineno}: "
                            f"returns the caught exception {bound!r}"
                        )

    assert offenders == [], (
        "exception detail must be logged, not returned to the client: "
        + "; ".join(offenders)
    )
