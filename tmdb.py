from fastapi import APIRouter, HTTPException, Query
import requests
import os
from dotenv import load_dotenv
import json

# Load API credentials from .env
load_dotenv()
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_ACCESS_TOKEN = os.getenv("TMDB_ACCESS_TOKEN")

# Resolved against this module's own directory rather than the process working
# directory, so importing tmdb.py does not depend on where the app was started.
_COUNTRIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "countries.json")

if os.path.exists(_COUNTRIES_PATH):
    with open(_COUNTRIES_PATH, "r", encoding="utf-8") as file:
        COUNTRY_MAPPING = json.load(file)
else:
    raise FileNotFoundError("The countries.json file is missing")

# Create a router instead of a separate FastAPI instance
router = APIRouter()

@router.get("/tmdb/movie/{movie_id}")
def get_movie_details(movie_id: int):
    url = f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={TMDB_API_KEY}"
    response = requests.get(url)

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Movie not found")

    data = response.json()
    
    # Return only relevant details
    return {
        "id": data["id"],
        "title": data["title"],
        "overview": data["overview"],
        "release_date": data["release_date"],
        "runtime": data["runtime"],
        "genres": [genre["name"] for genre in data["genres"]],
        "poster_url": f"https://image.tmdb.org/t/p/w500{data['poster_path']}" if data["poster_path"] else None,
        "backdrop_url": f"https://image.tmdb.org/t/p/w500{data['backdrop_path']}" if data["backdrop_path"] else None,
        "vote_average": data["vote_average"],
        "vote_count": data["vote_count"],
        "tagline": data["tagline"],
        "status": data["status"],
        "production_companies": [company["name"] for company in data["production_companies"]],
        "spoken_languages": [lang["english_name"] for lang in data["spoken_languages"]],
    }

# Get list of genres
@router.get("/tmdb/genres")
def get_movie_genres():
    url = f"https://api.themoviedb.org/3/genre/movie/list?api_key={TMDB_API_KEY}"
    response = requests.get(url)
    
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Genres not found")

    data = response.json()
    return {"genres": data["genres"]}

# Search movies by query.
#
# This used to be declared twice -- here and again in auth.py -- with two
# different response shapes. auth.py's router is mounted first in main.py, so
# auth.py's version was the one FastAPI actually served and the version below
# was dead code. The two are now consolidated into this single declaration,
# and the behaviour kept is auth.py's, because that is what the client needs:
# cineverse-frontend's src/pages/Search.jsx reads `response.data.results` and
# then builds its own poster URL from `movie.poster_path`. TMDB's raw response
# carries `poster_path`; the trimmed shape that used to live here exposed a
# pre-expanded `poster_url` and no `poster_path` at all, so every poster would
# have fallen back to /noimage.jpg had it ever been reachable.
@router.get("/tmdb/search")
def search_movies(query: str = Query(..., min_length=1), page: int = Query(1, ge=1)):
    if not TMDB_API_KEY:
        raise HTTPException(status_code=500, detail="TMDB API key is missing")

    headers = {
        "Authorization": f"Bearer {TMDB_ACCESS_TOKEN}"
    }

    params = {
        "query": query,
    }

    response = requests.get("https://api.themoviedb.org/3/search/movie", headers=headers, params=params)
    return response.json()

# Get streaming providers
@router.get("/tmdb/movie/{movie_id}/providers")
def get_movie_providers(movie_id: int):
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/watch/providers?api_key={TMDB_API_KEY}"
    response = requests.get(url)

    if response.status_code != 200:
        return {"error": "Failed to fetch data from TMDB"}

    data = response.json().get("results", {})

    providers_list = []
    
    for country_code, details in data.items():
        country_name = COUNTRY_MAPPING.get(country_code, country_code)  # Default to code if not found
        providers = set()  # Use a set to avoid duplicates

        for key in ["buy", "rent", "flatrate"]:
            if key in details:
                for provider in details[key]:
                    providers.add(provider["provider_name"])

        if providers:
            providers_list.append({
                "country": country_name,
                "providers": list(providers)
            })

    return providers_list

@router.get("/tmdb/movie/{movie_id}/recommendations")
def get_movie_recommendations(movie_id: int):
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/recommendations?api_key={TMDB_API_KEY}"
    response = requests.get(url)

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Recommendations not found")

    data = response.json()
    return {
        "recommendations": [
            {
                "id": movie["id"],
                "title": movie["title"],
                "release_date": movie.get("release_date", "N/A"),
                "poster_url": f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get("poster_path") else None
            }
            for movie in data["results"]
        ]
    }