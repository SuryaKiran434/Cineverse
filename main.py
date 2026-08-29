import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from database import get_db_connection
import auth
import protected
import watchlist
import tmdb

logger = logging.getLogger(__name__)

app = FastAPI()

# ✅ Add CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Frontend URL
    allow_credentials=True,
    allow_methods=["*"],  # Allows all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)

# Include routers
app.include_router(auth.router)
app.include_router(protected.router)
app.include_router(watchlist.router)
app.include_router(tmdb.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn any unhandled exception into an opaque 500.

    Handlers must not hand exception text to the client: a driver error from
    mysql-connector carries the DSN, the database name, the server version and
    sometimes the failing SQL, all of which help an attacker map the backend.
    The detail is written to the server log -- with the traceback, via
    ``logger.exception`` -- and the client gets a fixed generic message.

    Registering this once on the app is what keeps the guarantee true for every
    route, including ones added later. It replaces the per-endpoint
    ``except Exception as e: return {"error": str(e)}`` that used to leak from
    ``/test-db``; endpoints should now let unexpected errors propagate and
    raise ``HTTPException`` for the failures they genuinely want to describe.
    """
    logger.exception(
        "Unhandled error while serving %s %s", request.method, request.url.path
    )
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.get("/")
def home():
    return {"message": "Welcome to Cineverse!"}

@app.get("/test-db")
def test_db():
    conn = get_db_connection()
    if not conn:
        return {"error": "Database connection failed"}

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE();")
        db_name = cursor.fetchone()[0]
        return {"message": f"Connected to database: {db_name}"}
    finally:
        conn.close()
