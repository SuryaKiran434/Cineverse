"""Authorization tests for the watchlist router.

These cover the broken-object-level-authorization (BOLA / IDOR) bug that used
to live in ``POST /watchlist/add``: the endpoint took ``user_id`` straight out
of the request body and had no authentication dependency at all, so anybody
could write rows into anybody else's watchlist.

The rule the router must now obey, and that these tests pin down:

    the watchlist that gets written is *always* the one belonging to the
    subject of the verified JWT, and never one named by the client.

Everything here is offline. ``tests/conftest.py`` already installs a stub
``database`` module in ``sys.modules`` and sets a deterministic ``SECRET_KEY``,
so importing the app never opens a MySQL connection and never reads the real
``.env``. On top of that, each test swaps ``watchlist.get_db_connection`` and
``watchlist.get_movie_details`` for in-memory fakes, so no query and no TMDB
request ever leaves the process.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_helpers
import watchlist


# Two users that exist in the fake "users" table.
VICTIM_EMAIL = "victim@example.com"
VICTIM_ID = 1
ATTACKER_EMAIL = "attacker@example.com"
ATTACKER_ID = 2

USERS_BY_EMAIL = {VICTIM_EMAIL: VICTIM_ID, ATTACKER_EMAIL: ATTACKER_ID}

MOVIE_ID = 550


class FakeCursor:
    """The thinnest thing that behaves like a mysql-connector cursor.

    It understands only the handful of statements the watchlist router issues:
    the users lookup, the two "is it already there?" SELECTs, and the INSERT.
    Rows land in ``db.watchlist`` / ``db.watched`` so a test can assert on
    exactly whose list was written.
    """

    def __init__(self, db):
        self.db = db
        self._result = None

    def execute(self, query, params=()):
        normalized = " ".join(query.split()).lower()

        if normalized.startswith("select id from users"):
            (email,) = params
            user_id = USERS_BY_EMAIL.get(email)
            self._result = None if user_id is None else (user_id,)
            return

        if normalized.startswith("select * from watched"):
            user_id, movie_id = params
            self._result = (user_id, movie_id) if (user_id, movie_id) in self.db.watched else None
            return

        if normalized.startswith("select * from watchlist"):
            user_id, movie_id = params
            self._result = (user_id, movie_id) if (user_id, movie_id) in self.db.watchlist else None
            return

        if normalized.startswith("insert into watchlist"):
            user_id, movie_id, title = params
            self.db.watchlist[(user_id, movie_id)] = title
            self._result = None
            return

        raise AssertionError(f"unexpected query in test: {query!r}")

    def fetchone(self):
        return self._result

    def fetchall(self):  # pragma: no cover - not used by these tests
        return []


class FakeConnection:
    def __init__(self):
        self.watchlist = {}
        self.watched = {}
        self.commits = 0
        self.closed = False

    def cursor(self, dictionary=False):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


@pytest.fixture
def db(monkeypatch):
    """In-memory stand-in for the MySQL connection used by the router."""
    connection = FakeConnection()
    monkeypatch.setattr(watchlist, "get_db_connection", lambda: connection)
    monkeypatch.setattr(
        watchlist,
        "get_movie_details",
        lambda movie_id: {"title": "Fight Club", "poster": None, "genres": ["Drama"]},
    )
    return connection


@pytest.fixture
def client():
    """A FastAPI app carrying only the watchlist router."""
    app = FastAPI()
    app.include_router(watchlist.router)
    return TestClient(app)


def bearer(email):
    return {"Authorization": f"Bearer {auth_helpers.create_access_token(email)}"}


# --------------------------------------------------------------------------
# The vulnerability itself
# --------------------------------------------------------------------------


def test_cannot_write_to_another_users_watchlist(db, client):
    """The core regression test for the BOLA bug.

    The attacker authenticates as themselves but asks for the movie to be
    filed under the victim's ``user_id``. Whatever the endpoint does with the
    request, the victim's watchlist must be untouched.
    """
    response = client.post(
        "/watchlist/add",
        json={"user_id": VICTIM_ID, "movie_id": MOVIE_ID},
        headers=bearer(ATTACKER_EMAIL),
    )

    assert (VICTIM_ID, MOVIE_ID) not in db.watchlist, (
        "attacker managed to write into the victim's watchlist"
    )

    # Either the request is rejected outright, or it is honoured but scoped to
    # the caller's own watchlist. Both are safe; silently writing to the
    # victim is not.
    if response.status_code == 200:
        assert db.watchlist == {(ATTACKER_ID, MOVIE_ID): "Fight Club"}
    else:
        assert response.status_code in (401, 403, 422)
        assert db.watchlist == {}


def test_body_user_id_is_not_trusted_even_for_a_nonexistent_user(db, client):
    """A ``user_id`` that matches nobody must not change who gets written."""
    client.post(
        "/watchlist/add",
        json={"user_id": 9999, "movie_id": MOVIE_ID},
        headers=bearer(ATTACKER_EMAIL),
    )

    assert (9999, MOVIE_ID) not in db.watchlist
    assert set(db.watchlist) <= {(ATTACKER_ID, MOVIE_ID)}


def test_add_requires_authentication(db, client):
    """The endpoint used to be completely unauthenticated."""
    response = client.post("/watchlist/add", json={"user_id": VICTIM_ID, "movie_id": MOVIE_ID})

    assert response.status_code in (401, 403)
    assert db.watchlist == {}


def test_add_rejects_an_invalid_token(db, client):
    response = client.post(
        "/watchlist/add",
        json={"movie_id": MOVIE_ID},
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401
    assert db.watchlist == {}


def test_user_id_is_no_longer_part_of_the_request_model():
    """The redundant field is gone from the schema, not merely unused."""
    assert "user_id" not in watchlist.WatchlistRequest.model_fields
    assert "movie_id" in watchlist.WatchlistRequest.model_fields


# --------------------------------------------------------------------------
# The happy path still works
# --------------------------------------------------------------------------


def test_same_user_add_still_works(db, client):
    response = client.post(
        "/watchlist/add",
        json={"movie_id": MOVIE_ID},
        headers=bearer(VICTIM_EMAIL),
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Movie added to watchlist", "title": "Fight Club"}
    assert db.watchlist == {(VICTIM_ID, MOVIE_ID): "Fight Club"}
    assert db.commits == 1
    assert db.closed is True


def test_existing_frontend_payload_still_works_and_is_scoped_to_the_caller(db, client):
    """The shipped frontend still sends ``user_id``; that must not 422.

    It sends its *own* id, so the observable behaviour is unchanged -- but the
    value is now ignored and the caller's token decides the owner.
    """
    response = client.post(
        "/watchlist/add",
        json={"user_id": VICTIM_ID, "movie_id": MOVIE_ID},
        headers=bearer(VICTIM_EMAIL),
    )

    assert response.status_code == 200
    assert db.watchlist == {(VICTIM_ID, MOVIE_ID): "Fight Club"}


def test_duplicate_watchlist_entry_is_reported(db, client):
    db.watchlist[(VICTIM_ID, MOVIE_ID)] = "Fight Club"

    response = client.post(
        "/watchlist/add",
        json={"movie_id": MOVIE_ID},
        headers=bearer(VICTIM_EMAIL),
    )

    assert response.status_code == 200
    assert response.json() == {"error": "Movie is already in watchlist"}
    assert db.commits == 0


def test_movie_already_watched_is_reported(db, client):
    db.watched[(VICTIM_ID, MOVIE_ID)] = "Fight Club"

    response = client.post(
        "/watchlist/add",
        json={"movie_id": MOVIE_ID},
        headers=bearer(VICTIM_EMAIL),
    )

    assert response.status_code == 200
    assert response.json() == {"error": "Movie is already in watched list"}
    assert db.watchlist == {}


def test_one_users_duplicate_does_not_block_another_user(db, client):
    """Duplicate detection is scoped per user, not global."""
    db.watchlist[(ATTACKER_ID, MOVIE_ID)] = "Fight Club"

    response = client.post(
        "/watchlist/add",
        json={"movie_id": MOVIE_ID},
        headers=bearer(VICTIM_EMAIL),
    )

    assert response.status_code == 200
    assert db.watchlist[(VICTIM_ID, MOVIE_ID)] == "Fight Club"


def test_unknown_token_subject_is_a_404_not_a_write(db, client):
    """A token for an email with no user row must not insert anything."""
    response = client.post(
        "/watchlist/add",
        json={"movie_id": MOVIE_ID},
        headers=bearer("ghost@example.com"),
    )

    assert response.status_code == 404
    assert db.watchlist == {}
    assert db.closed is True


# --------------------------------------------------------------------------
# Audit guard: no watchlist endpoint may take a user identifier from the client
# --------------------------------------------------------------------------


def test_no_watchlist_endpoint_accepts_a_client_supplied_user_identifier():
    """Blanket check across every route on the watchlist router.

    Catches a regression anywhere in the router, not just on ``/watchlist/add``:
    no path, query or body parameter may be named after a user identifier.
    """
    banned = {"user_id", "userid", "user", "uid", "email", "user_email", "owner_id"}
    offenders = []

    for route in watchlist.router.routes:
        dependant = route.dependant
        client_params = (
            dependant.path_params + dependant.query_params + dependant.header_params
        )
        for param in client_params:
            if param.name.lower() in banned:
                offenders.append(f"{route.path}: client-supplied parameter {param.name!r}")

        for body_param in dependant.body_params:
            if body_param.name.lower() in banned:
                offenders.append(f"{route.path}: request-body field {body_param.name!r}")
            model = getattr(body_param.field_info, "annotation", None)
            for field_name in getattr(model, "model_fields", {}):
                if field_name.lower() in banned:
                    offenders.append(f"{route.path}: request-body field {field_name!r}")

    assert offenders == [], "user identity must come from the JWT, not the client: " + "; ".join(
        offenders
    )


def test_every_watchlist_route_requires_a_verified_token():
    """Every route on the router must depend on ``verify_token``."""
    unprotected = []

    for route in watchlist.router.routes:
        deps = [d.call for d in route.dependant.dependencies]
        if auth_helpers.verify_token not in deps:
            unprotected.append(f"{list(route.methods)} {route.path}")

    assert unprotected == [], "routes missing authentication: " + "; ".join(unprotected)
