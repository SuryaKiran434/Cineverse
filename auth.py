from fastapi import APIRouter, HTTPException, Depends, Security
from fastapi.security import OAuth2PasswordBearer  # ✅ Import this!
from pydantic import BaseModel
from passlib.context import CryptContext
import jwt
import os
from database import get_db_connection
from auth_helpers import create_access_token, verify_token
from fastapi import Query
import requests

# ✅ Define OAuth2PasswordBearer before using it
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

SECRET_KEY = os.getenv("SECRET_KEY")  # Make sure this is set in your .env file
ALGORITHM = "HS256"
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
url = "https://api.themoviedb.org/3/search/movie"
TMDB_ACCESS_TOKEN = os.getenv("TMDB_ACCESS_TOKEN")


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter()

class UserRegister(BaseModel):
    firstname: str
    lastname: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

@router.post("/register")
def register_user(user: UserRegister):
    db = get_db_connection()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    cursor = db.cursor()
    
    # Check if email already exists
    cursor.execute("SELECT id FROM users WHERE email = %s", (user.email,))
    existing_user = cursor.fetchone()
    
    if existing_user:
        db.close()
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = hash_password(user.password)
    
    # Insert new user
    cursor.execute("INSERT INTO users (firstname, lastname, email, password) VALUES (%s, %s, %s, %s)",
                   (user.firstname, user.lastname, user.email, hashed_password))
    db.commit()
    db.close()
    
    return {"message": "User registered successfully"}

@router.post("/login")
def login_user(user: UserLogin):
    db = get_db_connection()
    if not db:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, password FROM users WHERE email = %s", (user.email,))
    db_user = cursor.fetchone()
    db.close()

    if not db_user or not verify_password(user.password, db_user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_access_token(user.email)
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": db_user["id"]  # ✅ Include user_id in response
    }

def get_current_user(token: str = Security(oauth2_scheme)):  # ✅ Now it’s correctly defined!
    """
    Decodes JWT token and returns the user's email.
    Raises HTTPException if the token is invalid.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"email": email}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

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
    
    response = requests.get(url, headers=headers, params=params)
    return response.json()