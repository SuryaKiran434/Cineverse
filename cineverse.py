from fastapi import FastAPI, Depends, HTTPException
import requests
import os
from dotenv import load_dotenv
from auth import get_current_user  # Ensure this is implemented for JWT authentication

load_dotenv()

app = FastAPI()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"

@app.get("/tmdb/search/{query}")
def search_movies(query: str, current_user: dict = Security(get_current_user)):
    """
    Search for movies by title using TMDB API. 
    This endpoint is protected and requires authentication.
    """
    url = f"{TMDB_BASE_URL}/search/movie?query={query}&api_key={TMDB_API_KEY}&language=en-US"
    response = requests.get(url)

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch data from TMDB")

    data = response.json()
    
    # Extract necessary details
    movies = [
        {
            "id": movie["id"],
            "title": movie["title"],
            "overview": movie["overview"],
            "release_date": movie.get("release_date", "N/A"),
            "poster_path": f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie["poster_path"] else None,
            "vote_average": movie.get("vote_average", 0),
        }
        for movie in data.get("results", [])
    ]

    return {"results": movies}