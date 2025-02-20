from fastapi import APIRouter, Depends
from auth_helpers import verify_token

router = APIRouter()

@router.get("/protected-route")
def protected_example(user_email: str = Depends(verify_token)):
    return {"message": f"Hello, {user_email}! You have accessed a protected route."}
