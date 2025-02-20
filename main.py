from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import get_db_connection
import auth
import protected
import watchlist
import tmdb

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

@app.get("/")
def home():
    return {"message": "Welcome to Cineverse!"}

@app.get("/test-db")
def test_db():
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DATABASE();")
            db_name = cursor.fetchone()[0]
            conn.close()
            return {"message": f"Connected to database: {db_name}"}
        else:
            return {"error": "Database connection failed"}
    except Exception as e:
        return {"error": str(e)}
