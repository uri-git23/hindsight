from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import Conflict
from app.models import User
from app.schemas import SignupIn, TokenOut, UserOut
from app.security import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=UserOut, status_code=201)
def signup(body: SignupIn, db: Session = Depends(get_db)):
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # 사전 SELECT 대신 유니크 제약에 맡긴다 → 동시 가입 경합에도 안전
        db.rollback()
        raise Conflict("email already registered", code="email_taken")
    return user


@router.post("/token", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password flow. username 칸에 이메일을 넣는다 (/docs의 Authorize 버튼이 그대로 동작)."""
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    # 이메일이 없는 경우와 비밀번호가 틀린 경우를 같은 메시지로 → 가입 여부 노출 방지
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "incorrect email or password",
                            headers={"WWW-Authenticate": "Bearer"})
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
