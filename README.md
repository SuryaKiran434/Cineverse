# Cineverse

A FastAPI backend for movie discovery and personal watch tracking, backed by
MySQL and [The Movie Database (TMDB)](https://www.themoviedb.org/).

Users register and log in, get a JWT, and use it to build two lists — a
**watchlist** (want to see) and a **watched** list (already seen). Movie
metadata, posters, genres, streaming providers and recommendations are pulled
live from TMDB; only the user's own lists are persisted locally.

The React client that consumes this API lives in
[`cineverse-frontend`](https://github.com/SuryaKiran434/cineverse-frontend).

---

## Contents

- [Architecture](#architecture)
- [Modules](#modules)
- [API](#api)
- [Authentication](#authentication)
- [Data model](#data-model)
- [TMDB integration](#tmdb-integration)
- [Running locally](#running-locally)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Known rough edges](#known-rough-edges)

---

## Architecture

```
                 Browser / React client (http://localhost:5173)
                                  │
                                  │  JSON over HTTP
                                  │  Authorization: Bearer <JWT>
                                  ▼
        ┌──────────────────────────────────────────────────────┐
        │                  FastAPI app  (main.py)              │
        │                                                      │
        │   CORSMiddleware  ── allow_origins=[localhost:5173]   │
        │                                                      │
        │   include_router(auth)        /register /login       │
        │   include_router(protected)   /protected-route       │
        │   include_router(watchlist)   /watchlist /watched    │
        │   include_router(tmdb)        /tmdb/*                │
        └───────┬───────────────────────────────┬──────────────┘
                │                               │
     auth_helpers.py                            │
   create_access_token()                        │
   verify_token()  ── HTTPBearer                │
        │  HS256, 2h expiry                     │
        ▼                                       ▼
 ┌──────────────────┐              ┌────────────────────────────┐
 │  database.py     │              │  TMDB HTTP client          │
 │  get_db_conn()   │              │  requests → api.themoviedb │
 │  mysql-connector │              │  .org/3                    │
 └────────┬─────────┘              │                            │
          │ raw parameterised SQL  │  api_key= query param, or  │
          ▼                        │  Bearer TMDB_ACCESS_TOKEN  │
   ┌─────────────────┐             └────────────┬───────────────┘
   │   MySQL 8       │                          │
   │                 │            countries.json ┘ (provider country
   │  users          │                             code → name)
   │  watchlist      │
   │  watched        │
   └─────────────────┘
```

Request flow, end to end:

1. `POST /login` verifies a bcrypt hash and returns a signed JWT.
2. The client sends that JWT on every subsequent call.
3. A route dependency (`verify_token` or `get_current_user`) decodes it and
   yields the caller's email.
4. `get_user_id()` maps that email to `users.id`.
5. The handler runs parameterised SQL against MySQL and, where the response
   needs titles or posters, calls TMDB server-side before returning JSON.

---

## Modules

| File | Role |
|---|---|
| `main.py` | Builds the `FastAPI` app, installs CORS, mounts all four routers, exposes `/` and `/test-db` |
| `auth.py` | `/register`, `/login`, bcrypt hashing, and the `get_current_user` dependency |
| `auth_helpers.py` | `create_access_token()` / `verify_token()` — HS256, `HTTPBearer` scheme |
| `database.py` | `get_db_connection()` — a raw `mysql.connector` connection built from `DB_*` env vars |
| `watchlist.py` | Watchlist + watched CRUD and the personalised `/recommendations` endpoint |
| `tmdb.py` | TMDB proxy routes: movie details, genres, search, providers, per-movie recommendations |
| `protected.py` | A single example route demonstrating the JWT dependency |
| `countries.json` | ISO country code → display name, used to label streaming providers |
| `config.py`, `models.py` | **Empty placeholder files** — see [Known rough edges](#known-rough-edges) |

---

## API

Base URL in development: `http://127.0.0.1:8000`.
Interactive docs: **http://127.0.0.1:8000/docs**.

### Health

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Returns `{"message": "Welcome to Cineverse!"}` |
| `GET` | `/test-db` | — | Opens a MySQL connection and reports the connected database name |

### Auth (`auth.py`)

| Method | Path | Auth | Body / params |
|---|---|---|---|
| `POST` | `/register` | — | `{firstname, lastname, email, password}` → 400 if the email already exists |
| `POST` | `/login` | — | `{email, password}` → `{access_token, token_type, user_id}` |

### Protected example (`protected.py`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/protected-route` | Bearer | Echoes the authenticated user's email |

### Watchlist and watched (`watchlist.py`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/watchlist/add` | — ¹ | Body `{user_id, movie_id}`. Refuses if the movie is already watched or already listed. Resolves the title from TMDB. |
| `DELETE` | `/watchlist/remove/{movie_id}` | Bearer | Removes a movie from the caller's watchlist |
| `POST` | `/watched/add` | Bearer | Body `{movie_id, title}`. Moves the movie out of the watchlist if it was there. |
| `DELETE` | `/watched/remove/{movie_id}` | Bearer | Removes a movie from the caller's watched list |
| `GET` | `/watchlist` | Bearer | The caller's watchlist, each entry enriched with a poster URL from TMDB |
| `GET` | `/watched` | Bearer | The caller's watched list, likewise enriched |
| `GET` | `/recommendations` | Bearer | Personalised recommendations — see below |

¹ `/watchlist/add` is the one mutating route that takes `user_id` from the
request body instead of the JWT. See [Known rough edges](#known-rough-edges).

**How `/recommendations` works:** it unions the caller's `watched` and
`watchlist` movie ids, asks TMDB for the genres of each, maps those genre names
back to TMDB genre ids, shuffles them, then walks `/discover/movie` per genre
collecting up to 10 movies the user has not already saved.

### TMDB proxy (`tmdb.py`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/tmdb/movie/{movie_id}` | — | Trimmed movie detail: title, overview, runtime, genres, poster/backdrop URLs, votes, tagline, status, production companies, spoken languages |
| `GET` | `/tmdb/genres` | — | The full TMDB movie genre list |
| `GET` | `/tmdb/search?query=&page=` | — | Search by title → TMDB's raw `/search/movie` response, passed through unchanged ² |
| `GET` | `/tmdb/movie/{movie_id}/providers` | — | Streaming / buy / rent providers, grouped by country name via `countries.json` |
| `GET` | `/tmdb/movie/{movie_id}/recommendations` | — | TMDB's own "more like this" list for a movie |

² This route used to be declared **twice** — once in `auth.py` and once in
`tmdb.py` — with two different response shapes. `auth.py`'s router is mounted
first, so FastAPI served that one and `tmdb.py`'s was unreachable. The two are
now consolidated into a single declaration in `tmdb.py`, keeping the behaviour
that was actually being served and that the client depends on: it
authenticates to TMDB with the bearer `TMDB_ACCESS_TOKEN` and passes TMDB's
raw response straight through, so `results[].poster_path` is a **relative**
path. `cineverse-frontend` expands it into a full image URL itself.
`page` is accepted and validated but is not currently forwarded to TMDB.

---

## Authentication

- **Scheme:** JSON Web Tokens, `HS256`, signed with `SECRET_KEY`.
- **Issued by:** `POST /login`, via `create_access_token(email)`.
- **Claims:** `sub` = the user's email, `exp` = now + **2 hours**.
- **Sent as:** `Authorization: Bearer <token>`.
- **Verified by:** two dependencies that do the same job through different
  FastAPI security schemes —
  - `auth_helpers.verify_token` (`HTTPBearer`) returns the email as a string and
    is what the watchlist routes use;
  - `auth.get_current_user` (`OAuth2PasswordBearer`) returns `{"email": ...}`.
    It has no route depending on it at the moment — its only consumer was
    `cineverse.py`, which has been removed — but it stays as the supported
    `OAuth2PasswordBearer` entry point and is covered by the test suite.
- **Passwords:** hashed with **bcrypt** through `passlib`'s `CryptContext`. Only
  the hash is ever stored, and `/login` compares with `pwd_context.verify`.

An expired or malformed token yields `401`.

---

## Data model

There is no ORM layer and no migration tool — `database.py` hands out a raw
`mysql.connector` connection and every handler writes parameterised SQL
directly. The schema the queries imply:

```sql
CREATE TABLE users (
  id        INT AUTO_INCREMENT PRIMARY KEY,
  firstname VARCHAR(100) NOT NULL,
  lastname  VARCHAR(100) NOT NULL,
  email     VARCHAR(255) NOT NULL UNIQUE,
  password  VARCHAR(255) NOT NULL          -- bcrypt hash, never plaintext
);

CREATE TABLE watchlist (
  id       INT AUTO_INCREMENT PRIMARY KEY,
  user_id  INT NOT NULL,
  movie_id INT NOT NULL,                   -- TMDB movie id
  title    VARCHAR(255),
  FOREIGN KEY (user_id) REFERENCES users(id),
  UNIQUE KEY uniq_user_movie (user_id, movie_id)
);

CREATE TABLE watched (
  id       INT AUTO_INCREMENT PRIMARY KEY,
  user_id  INT NOT NULL,
  movie_id INT NOT NULL,                   -- TMDB movie id
  title    VARCHAR(255),
  FOREIGN KEY (user_id) REFERENCES users(id),
  UNIQUE KEY uniq_user_movie (user_id, movie_id)
);
```

Only `movie_id` and `title` are stored per entry. Posters, genres and
everything else are fetched from TMDB at read time, so the local database never
holds stale movie metadata. `watchlist` and `watched` are treated as mutually
exclusive: adding a movie to `watched` deletes it from `watchlist`, and
`/watchlist/add` refuses a movie that is already watched.

All SQL uses `%s` placeholders — values are never interpolated into query
strings.

---

## TMDB integration

Every TMDB call is made **server-side** with `requests` against
`https://api.themoviedb.org/3`. Two credentials are used, depending on the
route:

| Credential | Used as | Where |
|---|---|---|
| `TMDB_API_KEY` | `?api_key=` query parameter | `tmdb.py`, `watchlist.py` |
| `TMDB_ACCESS_TOKEN` | `Authorization: Bearer …` header | `tmdb.py`'s `/tmdb/search` |

Endpoints consumed: `/search/movie`, `/movie/{id}`, `/movie/{id}/watch/providers`,
`/movie/{id}/recommendations`, `/genre/movie/list`, `/discover/movie`.

Poster and backdrop paths are expanded to full URLs
(`https://image.tmdb.org/t/p/w500…`) before being returned — with the
exception of `/tmdb/search`, which passes TMDB's raw response through and so
returns the relative `poster_path` for the client to expand.

`countries.json` maps the ISO country codes TMDB returns in the providers
response to human-readable country names.

---

## Running locally

### 1. Prerequisites

- **Python 3.12**
- **MySQL 8** running locally
- A **TMDB API key** (free — see step 5)

### 2. Clone and create a virtualenv

```bash
git clone https://github.com/SuryaKiran434/Cineverse.git
cd Cineverse

python3.12 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> `requirements.txt` was added recently. This repository previously committed
> an entire `venv/` directory instead; that has been removed and is now
> ignored. If you have an old clone with a tracked `venv/`, delete it and
> recreate one as above.

### 4. Create the database

```bash
mysql -u root -p -e "CREATE DATABASE cineverse CHARACTER SET utf8mb4;"
```

Then create the three tables using the DDL in
[Data model](#data-model). Whatever database name you pick must match `DB_NAME`
(and the database in `DATABASE_URL`) in your `.env`.

### 5. Get a TMDB API key

1. Create a free account at [themoviedb.org](https://www.themoviedb.org/signup).
2. Go to **Settings → API** and request a developer key.
3. Copy both the **API Key (v3 auth)** → `TMDB_API_KEY`, and the **API Read
   Access Token (v4 auth)** → `TMDB_ACCESS_TOKEN`.

### 6. Configure `.env`

Copy the template — it contains placeholders only — and fill in your own
values:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | HS256 signing key for JWTs. Generate with `openssl rand -base64 32`. |
| `DB_HOST` | MySQL host, e.g. `localhost` |
| `DB_USER` | MySQL user |
| `DB_PASSWORD` | MySQL password |
| `DB_NAME` | Database name, e.g. `cineverse` |
| `DATABASE_URL` | SQLAlchemy-style URL, e.g. `mysql+mysqlconnector://USER:PASSWORD@localhost/cineverse` |
| `TMDB_API_KEY` | TMDB v3 API key |
| `TMDB_ACCESS_TOKEN` | TMDB v4 read access token |

`database.py` reads the four `DB_*` variables; `DATABASE_URL` is kept for
tooling. **`.env` is git-ignored and must never be committed.**

### 7. Run the server

```bash
uvicorn main:app --reload
```

| | |
|---|---|
| API | http://127.0.0.1:8000 |
| Interactive docs (Swagger UI) | **http://127.0.0.1:8000/docs** |
| ReDoc | http://127.0.0.1:8000/redoc |

Confirm the database wiring with:

```bash
curl http://127.0.0.1:8000/test-db
```

CORS is configured for `http://localhost:5173`, the Vite dev server default —
run the frontend on that port or add your origin to `allow_origins` in
`main.py`.

---

## Testing

```bash
pip install pytest pytest-cov httpx
pytest
```

**62 tests across 5 files, all passing.** No database and no network: the MySQL
connection and every TMDB call are stubbed, so the suite runs from a clean
checkout with nothing provisioned.

| File | Covers |
|---|---|
| `tests/test_auth_jwt.py` | Token creation and expiry, `alg: none` rejection, signature failures, and that PyJWT still raises the exception classes the code catches |
| `tests/test_watchlist_authz.py` | That the watchlist written is always the one owned by the bearer of the token, never a `user_id` from the request body |
| `tests/test_tmdb_routes.py` | The proxy routes, so the TMDB credentials stay server-side |
| `tests/test_error_response_hygiene.py` | That error responses carry no upstream text, stack frames or connection details |
| `tests/test_watchlist_logging.py` | That log records carry no personal data |

CI (`.github/workflows/ci.yml`, job **Tests (Python)**) runs the same suite on
Python 3.13 and publishes a coverage report.

---

## Security notes

### TMDB keys are server-side credentials

**`TMDB_API_KEY` and `TMDB_ACCESS_TOKEN` must never reach a browser.** They
belong in this backend's `.env` and nowhere else.

Any TMDB request a client needs must go **through this API**, which is exactly
what the `/tmdb/*` routes are for: the browser calls Cineverse, Cineverse calls
TMDB with the key, and only the response comes back. A key handed to a frontend
is public — bundled into shipped JavaScript, visible in DevTools, readable by
every visitor, and trivially extractable from a deployed site. In particular,
**never** put a TMDB key in a `VITE_`-prefixed variable in the frontend
repository: Vite inlines those into the production bundle at build time.

If a key is ever exposed, rotating it in the TMDB dashboard is the only fix —
removing it from the code does not un-publish it.

### Other

- `.env` is git-ignored. Commit `.env.example` (placeholders only), never `.env`.
- `SECRET_KEY` forges JWTs for **any** account if leaked. Treat it like a
  password and use a distinct value per environment.
- Passwords are stored only as bcrypt hashes.
- All SQL is parameterised.
- Set `allow_origins` to your real frontend origin before deploying — do not
  widen it to `*` while `allow_credentials=True`.

---

## Known rough edges

Documented here so they are not mistaken for design:

- **`config.py` and `models.py` are empty.** Configuration is read ad hoc with
  `os.getenv` in each module, and there are no ORM models — despite
  `SQLAlchemy` being pinned in `requirements.txt`, it is not currently imported
  anywhere.
- **`auth_helpers.create_access_token` uses `datetime.utcnow()`**, which is
  deprecated in Python 3.12 in favour of `datetime.now(timezone.utc)`. The
  behaviour is correct — the tests pin expiry and rejection — but the call will
  eventually stop existing.
- **Coverage is concentrated on auth, authorisation and response hygiene.** The
  database access paths have no tests, because they would need a live MySQL.
