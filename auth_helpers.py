from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWTError
from datetime import datetime, timedelta
import os

SECRET_KEY = os.getenv("SECRET_KEY")  # Change this to a strong secret
ALGORITHM = "HS256"
security = HTTPBearer()

def create_access_token(email: str):
    payload = {
        "sub": email,
        "exp": datetime.utcnow() + timedelta(hours=2)  # Token expires in 2 hours
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
