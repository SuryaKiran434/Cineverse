"""Regression tests for clear-text logging of sensitive data (CWE-312/532).

``GET /recommendations`` used to carry four ``print()`` calls left over from
development. One of them dumped a whole TMDB payload::

    print(f"Details for movie {movie_id}:", movie_details)

``movie_details`` is built inside ``get_movie_details`` from the body of

    https://api.themoviedb.org/3/movie/<id>?api_key=<TMDB_API_KEY>

so every value in it is, as far as data flow is concerned, derived from a
request URL that embeds the TMDB credential -- which is why CodeQL reports the
line as logging sensitive data in clear text. Independently of the credential,
dumping a third-party API payload into stdout on every request is exactly the
habit that puts secrets in logs the first time such a payload happens to
contain one.

The router now logs through ``logging`` at DEBUG and records only movie ids
and counts -- values that come from our own database or are aggregates -- so
the log still answers "which movies did we resolve, and did they yield
genres?" without reproducing anything that came back over the wire.

Everything here is offline, following ``tests/conftest.py`` and
``tests/test_watchlist_authz.py``: the ``database`` module is stubbed in
``sys.modules``, the connection is an in-memory fake, and ``watchlist.requests``
is monkeypatched so no HTTP request ever leaves the process.
"""

import ast
import logging
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_helpers
import watchlist

from conftest import REPO_ROOT


USER_EMAIL = "viewer@example.com"
USER_ID = 1

# The credential CodeQL identifies as the source of the tainted flow. It is
# substituted for the real key for the duration of a test so we can assert on
# an exact string.
SENTINEL_API_KEY = "tmdb-api-key-that-must-never-be-logged"

# A movie already on the user's lists, and the TMDB payload describing it.
SEED_MOVIE_ID = 550
SEED_MOVIE_PAYLOAD = {
    "id": SEED_MOVIE_ID,
    "title": "Fight Club",
    "overview": "A ticking-time-bomb insomniac and a slippery soap salesman.",
    "poster_path": "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
    "genres": [{"name": "Drama"}, {"name": "Thriller"}],
}

GENRE_LIST_PAYLOAD = {"genres": [{"id": 18, "name": "Drama"}, {"id": 53, "name": "Thriller"}]}

RECOMMENDED_MOVIE_ID = 680
DISCOVER_PAYLOAD = {
    "results": [
        {
            "id": RECOMMENDED_MOVIE_ID,
            "title": "Pulp Fiction",
            "poster_path": "/d5iIlFn5s0ImszYzBPb8JPIfbXD.jpg",
        }
    ]
}

# Strings that only ever exist inside a TMDB response body. None of them may
# appear in a log record.
PAYLOAD_MARKERS = [
    "Fight Club",
    "Pulp Fiction",
    "ticking-time-bomb",
    "pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK",
    "d5iIlFn5s0ImszYzBPb8JPIfbXD",
]


class FakeCursor:
    """Understands only the two statements ``/recommendations`` issues."""

    def __init__(self, db):
        self.db = db
        self._rows = []

    def execute(self, query, params=()):
        normalized = " ".join(query.split()).lower()

        if normalized.startswith("select id from users"):
            self._rows = [(USER_ID,)]
            return

        if "union" in normalized:
            self._rows = [{"movie_id": movie_id} for movie_id in self.db.movie_ids]
            return

        raise AssertionError(f"unexpected query in test: {query!r}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, movie_ids=(SEED_MOVIE_ID,)):
        self.movie_ids = list(movie_ids)
        self.closed = False

    def cursor(self, dictionary=False):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture
def tmdb(monkeypatch):
    """Serve the three TMDB endpoints ``/recommendations`` calls, offline.

    ``get_movie_details`` is left running for real -- the point of the test is
    the value it returns and where that value ends up -- so only the transport
    underneath it is replaced. Every URL it builds is recorded, which is also
    how the test confirms the sentinel key really was in play.
    """
    monkeypatch.setattr(watchlist, "TMDB_API_KEY", SENTINEL_API_KEY)
    requested_urls = []

    def fake_get(url, *args, **kwargs):
        requested_urls.append(url)
        if "/genre/movie/list" in url:
            return FakeResponse(GENRE_LIST_PAYLOAD)
        if "/discover/movie" in url:
            return FakeResponse(DISCOVER_PAYLOAD)
        if "/movie/" in url:
            return FakeResponse(SEED_MOVIE_PAYLOAD)
        raise AssertionError(f"unexpected TMDB request in test: {url!r}")

    monkeypatch.setattr(watchlist.requests, "get", fake_get)
    return requested_urls


@pytest.fixture
def db(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(watchlist, "get_db_connection", lambda: connection)
    return connection


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(watchlist.router)
    return TestClient(app)


def bearer(email):
    return {"Authorization": f"Bearer {auth_helpers.create_access_token(email)}"}


def logged_text(caplog):
    """Every record the request produced, rendered the way a handler would."""
    return "\n".join(record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------
# The leak itself
# --------------------------------------------------------------------------


def test_the_tmdb_credential_never_reaches_the_logs(db, client, tmdb, caplog, capsys):
    with caplog.at_level(logging.DEBUG):
        response = client.get("/recommendations", headers=bearer(USER_EMAIL))

    assert response.status_code == 200

    # The key really was in the URLs, so the flow CodeQL describes was live.
    assert any(SENTINEL_API_KEY in url for url in tmdb)

    assert SENTINEL_API_KEY not in logged_text(caplog)
    assert SENTINEL_API_KEY not in capsys.readouterr().out


def test_no_tmdb_payload_is_written_to_the_logs(db, client, tmdb, caplog, capsys):
    """Nothing reached through a TMDB response body may be logged."""
    with caplog.at_level(logging.DEBUG):
        client.get("/recommendations", headers=bearer(USER_EMAIL))

    text = logged_text(caplog)
    streams = capsys.readouterr()

    for marker in PAYLOAD_MARKERS:
        assert marker not in text, f"{marker!r} came from a TMDB payload and was logged"
        assert marker not in streams.out
        assert marker not in streams.err


def test_nothing_is_printed_to_stdout_at_all(db, client, tmdb, capsys):
    """The debug output goes through ``logging``, so it is off by default."""
    client.get("/recommendations", headers=bearer(USER_EMAIL))

    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------
# The log is still worth having
# --------------------------------------------------------------------------


def test_the_log_still_identifies_which_movie_was_resolved(db, client, tmdb, caplog):
    """A safe identifier replaced the payload; it did not simply disappear."""
    with caplog.at_level(logging.DEBUG):
        client.get("/recommendations", headers=bearer(USER_EMAIL))

    text = logged_text(caplog)

    assert str(SEED_MOVIE_ID) in text, "the movie id is the debugging handle and must stay"
    assert "2 genre(s)" in text, "the genre count is the other half of the signal"


def test_recommendations_still_work(db, client, tmdb):
    """The endpoint's actual output is unaffected by the logging change."""
    response = client.get("/recommendations", headers=bearer(USER_EMAIL))

    assert response.status_code == 200
    assert response.json() == {
        "recommendations": [
            {
                "movie_id": RECOMMENDED_MOVIE_ID,
                "title": "Pulp Fiction",
                "poster": "https://image.tmdb.org/t/p/w500/d5iIlFn5s0ImszYzBPb8JPIfbXD.jpg",
            }
        ]
    }
    assert db.closed is True


# --------------------------------------------------------------------------
# Audit guard
# --------------------------------------------------------------------------


def test_the_watchlist_router_contains_no_print_calls():
    """``print`` is what put the payload in the log in the first place.

    Debug output belongs behind a logger, where it is off unless somebody asks
    for it and where a deployment can route it away from anywhere untrusted.
    """
    path = os.path.join(REPO_ROOT, "watchlist.py")
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read(), filename=path)

    offenders = [
        f"watchlist.py:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]

    assert offenders == [], "use logger.debug(...) instead of print(): " + "; ".join(offenders)
