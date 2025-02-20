from fastapi import APIRouter, Depends, HTTPException
from database import get_db_connection
from auth_helpers import verify_token
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import requests
import random

router = APIRouter()

class Movie(BaseModel):
    movie_id: int
    title: str

class WatchlistRequest(BaseModel):
    user_id: int
    movie_id: int    

load_dotenv()
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"

def get_movie_details(movie_id: int):
    """Fetch movie details (title, poster) from TMDB API"""
    url = f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={TMDB_API_KEY}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return {
            "title": data.get("title", "Unknown Title"),
            "poster": f"https://image.tmdb.org/t/p/w500{data.get('poster_path', '')}" if data.get("poster_path") else None,
            "genres": [genre["name"] for genre in data.get("genres", [])]
        }
    return {"title": "Unknown Title", "poster": None, "genres": []}

def get_user_id(db, user_email: str):
    """Fetch user_id from email."""
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE email = %s", (user_email,))
    result = cursor.fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    return result[0]

# Add movie to watchlist
@router.post("/watchlist/add")
def add_to_watchlist(request: WatchlistRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if movie is already in watched list
    cursor.execute("SELECT * FROM watched WHERE user_id = %s AND movie_id = %s", (request.user_id, request.movie_id))
    if cursor.fetchone():
        conn.close()
        return {"error": "Movie is already in watched list"}

    # Check if already in watchlist
    cursor.execute("SELECT * FROM watchlist WHERE user_id = %s AND movie_id = %s", (request.user_id, request.movie_id))
    if cursor.fetchone():
        conn.close()
        return {"error": "Movie is already in watchlist"}

    # Fetch movie details from TMDB API
    movie_details = get_movie_details(request.movie_id)

    # Add to watchlist
    cursor.execute("INSERT INTO watchlist (user_id, movie_id, title) VALUES (%s, %s, %s)", 
                   (request.user_id, request.movie_id, movie_details["title"]))
    conn.commit()

    conn.close()
    
    return {"message": "Movie added to watchlist", "title": movie_details["title"]}

# Remove movie from watchlist
@router.delete("/watchlist/remove/{movie_id}")
def remove_from_watchlist(movie_id: int, user_email: str = Depends(verify_token)):
    db = get_db_connection()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        cursor = db.cursor()
        user_id = get_user_id(db, user_email)

        cursor.execute("DELETE FROM watchlist WHERE user_id = %s AND movie_id = %s", (user_id, movie_id))
        db.commit()

        return {"message": "Movie removed from watchlist"}

    finally:
        db.close()

# Add movie to watched list
@router.post("/watched/add")
def add_to_watched(movie: Movie, user_email: str = Depends(verify_token)):
    db = get_db_connection()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection error")

    cursor = db.cursor()
    user_id = get_user_id(db, user_email)

    # Check if movie is already in watched list
    cursor.execute("SELECT * FROM watched WHERE user_id = %s AND movie_id = %s", (user_id, movie.movie_id))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="Movie already in watched list")

    # Check if movie is in the watchlist, remove it
    cursor.execute("SELECT * FROM watchlist WHERE user_id = %s AND movie_id = %s", (user_id, movie.movie_id))
    if cursor.fetchone():
        cursor.execute("DELETE FROM watchlist WHERE user_id = %s AND movie_id = %s", (user_id, movie.movie_id))
        db.commit()

    # Insert movie into watched list
    cursor.execute("INSERT INTO watched (user_id, movie_id, title) VALUES (%s, %s, %s)", 
                   (user_id, movie.movie_id, movie.title))
    
    db.commit()

    db.close()

    return {"message": "Movie added to watched list"}

# Remove movie from watched list
@router.delete("/watched/remove/{movie_id}")
def remove_from_watched(movie_id: int, user_email: str = Depends(verify_token)):
    db = get_db_connection()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        cursor = db.cursor()
        user_id = get_user_id(db, user_email)

        cursor.execute("DELETE FROM watched WHERE user_id = %s AND movie_id = %s", (user_id, movie_id))
        db.commit()

        return {"message": "Movie removed from watched list"}

    finally:
        db.close()

# Get watchlist with posters
@router.get("/watchlist")
def get_watchlist(user_email: str = Depends(verify_token)):
    db = get_db_connection()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        cursor = db.cursor(dictionary=True)
        user_id = get_user_id(db, user_email)

        cursor.execute("SELECT movie_id, title FROM watchlist WHERE user_id = %s", (user_id,))
        watchlist = cursor.fetchall()

        # Fetch posters dynamically from TMDB
        for movie in watchlist:
            movie_details = get_movie_details(movie["movie_id"])
            movie["poster"] = movie_details["poster"]

        return {"watchlist": watchlist}

    finally:
        db.close()

# Get watched list with posters
@router.get("/watched")
def get_watched(user_email: str = Depends(verify_token)):
    db = get_db_connection()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        cursor = db.cursor(dictionary=True)
        user_id = get_user_id(db, user_email)

        cursor.execute("SELECT movie_id, title FROM watched WHERE user_id = %s", (user_id,))
        watched = cursor.fetchall()

        # Fetch posters dynamically from TMDB
        for movie in watched:
            movie_details = get_movie_details(movie["movie_id"])
            movie["poster"] = movie_details["poster"]

        return {"watched": watched}

    finally:
        db.close()

@router.get("/recommendations")
def get_recommendations(user_email: str = Depends(verify_token)):
    db = get_db_connection()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        cursor = db.cursor(dictionary=True)
        user_id = get_user_id(db, user_email)

        # Step 1: Get all movies from watched and watchlist
        cursor.execute("""
            SELECT movie_id FROM watched WHERE user_id = %s
            UNION 
            SELECT movie_id FROM watchlist WHERE user_id = %s
        """, (user_id, user_id))

        user_movies = {row["movie_id"] for row in cursor.fetchall()}
        print("User Movies:", user_movies)  # Debugging output

        if not user_movies:
            return {"recommendations": []}  # No recommendations if user has no movies

        # Step 2: Get genres of user's movies
        user_genres = set()
        for movie_id in user_movies:
            movie_details = get_movie_details(movie_id)
            print(f"Details for movie {movie_id}:", movie_details)  # Debugging output

            if movie_details["genres"]:  # Ensure genres are not empty
                user_genres.update(movie_details["genres"])

        print("User Genres:", user_genres)  # Debugging output

        if not user_genres:
            return {"recommendations": []}  # No recommendations if no genres found

        # Step 3: Get TMDB Genre IDs
        genre_mapping_url = f"{TMDB_BASE_URL}/genre/movie/list?api_key={TMDB_API_KEY}"
        response = requests.get(genre_mapping_url)
        if response.status_code != 200:
            return {"recommendations": []}

        genre_mapping = {g["name"]: g["id"] for g in response.json().get("genres", [])}
        user_genre_ids = [str(genre_mapping[g]) for g in user_genres if g in genre_mapping]

        if not user_genre_ids:
            return {"recommendations": []}

        # Randomize the order of genres to ensure different results each time
        random.shuffle(user_genre_ids)

        # Step 4: Fetch Movies from TMDB for User's Genres
        recommended_movies = {}
        for genre_id in user_genre_ids:
            tmdb_url = f"{TMDB_BASE_URL}/discover/movie?api_key={TMDB_API_KEY}&with_genres={genre_id}"
            response = requests.get(tmdb_url)
            if response.status_code == 200:
                movies = response.json().get("results", [])
                random.shuffle(movies)  # Shuffle movies to randomize the order
                for movie in movies:
                    movie_id = movie["id"]
                    if movie_id not in user_movies and movie_id not in recommended_movies:
                        recommended_movies[movie_id] = {
                            "movie_id": movie_id,
                            "title": movie["title"],
                            "poster": f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get("poster_path") else None
                        }

                    if len(recommended_movies) >= 10:  # Limit recommendations
                        break

        print("Recommended Movies:", recommended_movies.values())  # Debugging output

        return {"recommendations": list(recommended_movies.values())}

    finally:
        db.close()
