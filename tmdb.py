from fastapi import APIRouter, HTTPException
import requests
import os
from dotenv import load_dotenv
import json

# Load API key from .env
load_dotenv()
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

if os.path.exists("countries.json"):
    with open("countries.json", "r", encoding="utf-8") as file:
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

# Search movies by query
@router.get("/tmdb/search")
def search_movies(query: str):
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
    response = requests.get(url)

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Search failed")

    data = response.json()
    return {
        "results": [
            {
                "id": movie["id"],
                "title": movie["title"],
                "release_date": movie.get("release_date", "N/A"),
                "poster_url": f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get("poster_path") else None,
                "overview": movie["overview"]
            }
            for movie in data["results"]
        ]
    }

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