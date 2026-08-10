import os
import jwt
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from db.models import TalkarAdmin
from config import settings

TALKAR_ADMIN_JWT_SECRET = settings.TALKAR_ADMIN_JWT_SECRET
ALGORITHM = "HS256"

security = HTTPBearer()

def create_admin_access_token(data: dict, expires_delta: timedelta = timedelta(hours=24)):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, TALKAR_ADMIN_JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate admin credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, TALKAR_ADMIN_JWT_SECRET, algorithms=[ALGORITHM])
        admin_id: str = payload.get("sub")
        is_admin: bool = payload.get("is_admin")
        if admin_id is None or not is_admin:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
        
    result = await db.execute(select(TalkarAdmin).where(TalkarAdmin.id == int(admin_id)))
    admin = result.scalar_one_or_none()
    if admin is None:
        raise credentials_exception
    return admin
