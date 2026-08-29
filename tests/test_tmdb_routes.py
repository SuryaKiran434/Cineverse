"""Route-registration tests for the TMDB proxy.

Two defects motivated this suite, both found by a documentation audit:

* ``/tmdb/search`` was declared **twice** -- once in ``auth.py`` and once in
  ``tmdb.py`` -- with two different response shapes. FastAPI serves whichever
  router is mounted first, so ``auth.py``'s won and ``tmdb.py``'s was
  unreachable. Silently-dead routes are the kind of thing a reader of the
  source cannot see, so the tests below assert on the *composed* application
  rather than on either module in isolation.

* ``cineverse.py`` was an unmounted second ``FastAPI()`` instance that raised
  ``NameError`` the moment anything imported it, because it called
  ``Security(...)`` without importing it. Nothing imported it, so nothing
  noticed.

Everything here is offline, following the pattern already established by
``tests/conftest.py``: the ``database`` module is stubbed in ``sys.modules``,
``SECRET_KEY`` and the TMDB credentials come from the environment the conftest
sets up, and the one test that exercises a request monkeypatches
``tmdb.requests.get`` so no HTTP call ever leaves the process.
"""

import importlib
import inspect
import os
import pkgutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import main
import tmdb

from conftest import REPO_ROOT


# A trimmed-down but faithful slice of a real TMDB /search/movie response.
# The detail that matters is `poster_path`: a relative path, not a full URL.
RAW_TMDB_PAYLOAD = {
    "page": 1,
    "results": [
        {
            "id": 550,
            "title": "Fight Club",
            "overview": "A ticking-time-bomb insomniac...",
            "release_date": "1999-10-15",
            "poster_path": "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
            "vote_average": 8.4,
        }
    ],
    "total_pages": 1,
    "total_results": 1,
}


@pytest.fixture
def client():
    """The real composed application, exactly as main.py builds it."""
    return TestClient(main.app)


@pytest.fixture
def fake_tmdb(monkeypatch):
    """Swap tmdb.requests.get for something that never touches the network."""

    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return RAW_TMDB_PAYLOAD

    def fake_get(url, headers=None, params=None, **kwargs):
        calls.append({"url": url, "headers": headers or {}, "params": params or {}})
        return FakeResponse()

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    return calls


def _routes(app_or_router):
    """Every (method, path) pair an app or router actually declares.

    Deliberately not ``app.openapi()["paths"]``: the OpenAPI schema is keyed by
    path, so it collapses a duplicate declaration into one entry -- which is
    precisely the bug these tests exist to catch.

    Since FastAPI 0.116, ``include_router`` no longer copies the child routes
    into ``app.routes``; it appends a single ``_IncludedRouter`` proxy that
    holds the original router. Descend through those so the composed app is
    seen the way the router actually resolves it. Falls back to plain
    iteration on older FastAPI, where the routes are already flattened.
    """
    pairs = []

    def walk(container, prefix=""):
        for route in getattr(container, "routes", []):
            included = getattr(route, "original_router", None)
            if included is not None:
                walk(included, prefix + getattr(included, "prefix", ""))
                continue
            methods = getattr(route, "methods", None)
            path = getattr(route, "path", None)
            if not methods or path is None:
                continue
            for method in methods:
                pairs.append((method, prefix + path))

    walk(app_or_router)
    return pairs


# ---------------------------------------------------------------------------
# The duplicate route
# ---------------------------------------------------------------------------


def test_tmdb_search_is_declared_exactly_once():
    """The whole point: one declaration, so mount order cannot decide behaviour."""
    declarations = [p for m, p in _routes(main.app) if p == "/tmdb/search"]
    assert declarations == ["/tmdb/search"], (
        f"expected exactly one /tmdb/search route, found {len(declarations)}"
    )


def test_no_route_is_registered_more_than_once():
    """Blanket guard: no (method, path) pair may be shadowed anywhere in the app.

    Broader than the test above on purpose -- a second duplicate introduced on
    some other path would be just as invisible, and just as wrong.
    """
    seen = {}
    duplicates = []
    for method, path in _routes(main.app):
        key = (method, path)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            duplicates.append(f"{method} {path}")

    assert duplicates == [], "route declared more than once: " + "; ".join(duplicates)


def test_tmdb_search_belongs_to_the_tmdb_router():
    """All /tmdb/* routes live in tmdb.py; auth.py is back to being about auth."""
    assert "/tmdb/search" in [p for _, p in _routes(tmdb.router)]
    assert [p for _, p in _routes(auth.router) if p.startswith("/tmdb")] == []


def test_auth_module_no_longer_talks_to_tmdb():
    """auth.py should not import `requests` or carry TMDB credentials any more."""
    source = inspect.getsource(auth)
    assert "requests" not in source
    assert "TMDB" not in source


# ---------------------------------------------------------------------------
# The response shape that survived, and why
# ---------------------------------------------------------------------------


def test_search_returns_tmdbs_raw_shape_with_poster_path(client, fake_tmdb):
    """cineverse-frontend's Search.jsx depends on this exact shape.

    It does ``setMovies(response.data.results)`` and then expands the poster
    itself with ``https://image.tmdb.org/t/p/w500${movie.poster_path}``. So the
    response must carry ``results[].poster_path`` as a *relative* path. The
    trimmed shape that used to sit (unreachable) in tmdb.py returned a
    pre-expanded ``poster_url`` and no ``poster_path``, which would have made
    every poster in the client fall back to /noimage.jpg.
    """
    response = client.get("/tmdb/search", params={"query": "fight club"})

    assert response.status_code == 200
    body = response.json()

    movie = body["results"][0]
    assert movie["poster_path"] == "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg"
    assert movie["poster_path"].startswith("/"), "client prepends the image host itself"
    assert "poster_url" not in movie, "the trimmed shape must not come back"
    assert movie["title"] == "Fight Club"
    assert movie["id"] == 550


def test_search_authenticates_to_tmdb_with_the_bearer_access_token(client, fake_tmdb):
    """The surviving implementation uses TMDB_ACCESS_TOKEN, not ?api_key=."""
    client.get("/tmdb/search", params={"query": "fight club"})

    (call,) = fake_tmdb
    assert call["url"].endswith("/search/movie")
    assert call["headers"]["Authorization"] == f"Bearer {os.environ['TMDB_ACCESS_TOKEN']}"
    assert call["params"]["query"] == "fight club"
    assert "api_key" not in call["params"]


def test_search_rejects_an_empty_query(client, fake_tmdb):
    assert client.get("/tmdb/search", params={"query": ""}).status_code == 422
    assert client.get("/tmdb/search").status_code == 422
    assert fake_tmdb == [], "no TMDB request should be made for an invalid query"


def test_search_reports_a_missing_api_key_rather_than_calling_tmdb(
    client, fake_tmdb, monkeypatch
):
    monkeypatch.setattr(tmdb, "TMDB_API_KEY", None)

    response = client.get("/tmdb/search", params={"query": "fight club"})

    assert response.status_code == 500
    assert response.json()["detail"] == "TMDB API key is missing"
    assert fake_tmdb == []


# ---------------------------------------------------------------------------
# Dead code that used to fail on import
# ---------------------------------------------------------------------------


def test_cineverse_module_is_gone():
    """cineverse.py was removed; it must not come back by accident."""
    assert not os.path.exists(os.path.join(REPO_ROOT, "cineverse.py"))


def test_only_main_constructs_a_fastapi_application():
    """Every other module must expose an APIRouter, not its own app.

    A second `FastAPI()` instance cannot be mounted with `include_router`, so
    it is dead the moment it is written -- which is exactly how cineverse.py
    accumulated a NameError nobody hit.
    """
    offenders = []
    for name in _top_level_modules():
        path = os.path.join(REPO_ROOT, f"{name}.py")
        source = open(path, encoding="utf-8").read()
        if "FastAPI(" in source and name != "main":
            offenders.append(name)

    assert offenders == [], (
        "these modules build their own FastAPI app instead of an APIRouter: "
        + ", ".join(offenders)
    )


def _top_level_modules():
    """Application modules at the repo root, excluding tests and the DB stub."""
    skip = {"database"}  # stubbed in sys.modules by conftest; importing the real one
                         # would read the developer's .env and pull in mysql.connector
    return sorted(
        m.name
        for m in pkgutil.iter_modules([REPO_ROOT])
        if not m.ispkg and m.name not in skip
    )


@pytest.mark.parametrize("module_name", _top_level_modules())
def test_every_module_imports_cleanly(module_name):
    """Import each module and assert it does not explode.

    This is the regression test for the cineverse.py defect in its general
    form: it called `Security(...)` without importing the name, so `import
    cineverse` raised NameError. Nothing imported it, so no test, no CI run and
    no request ever found out.
    """
    importlib.import_module(module_name)
