"""Test bootstrap for Cineverse.

These tests are deliberately dependency-light: pytest plus whatever the app
already imports. They must never touch MySQL, the network, or the real .env
file, so this module does two things before any application code is imported:

1. Puts the repository root on ``sys.path`` so ``import auth_helpers`` works
   when pytest is run from the repo root.
2. Installs a stub ``database`` module in ``sys.modules``. ``auth.py`` does
   ``from database import get_db_connection`` at import time, and the real
   ``database.py`` calls ``load_dotenv()`` (reading the developer's real .env)
   and pulls in ``mysql.connector``. The stub keeps the import graph intact
   without any of that.
3. Sets a known ``SECRET_KEY``. Both ``auth.py`` and ``auth_helpers.py`` read
   ``SECRET_KEY`` from the environment *at import time*, so it has to be in
   place before the first import of either module.
"""

import os
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Deterministic signing key for the whole test session.
TEST_SECRET_KEY = "test-secret-key-for-smoke-tests-only"
os.environ["SECRET_KEY"] = TEST_SECRET_KEY
os.environ.setdefault("TMDB_API_KEY", "test-tmdb-api-key")
os.environ.setdefault("TMDB_ACCESS_TOKEN", "test-tmdb-access-token")

# Stub out database access so importing `auth` never opens a MySQL connection
# and never reads the real .env.
if "database" not in sys.modules:
    _database_stub = types.ModuleType("database")

    def _get_db_connection():  # pragma: no cover - must never be called
        raise AssertionError(
            "Tests must not open a database connection. "
            "Something imported database.get_db_connection() for real."
        )

    _database_stub.get_db_connection = _get_db_connection
    sys.modules["database"] = _database_stub
