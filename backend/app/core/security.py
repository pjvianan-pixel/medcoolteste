from datetime import UTC, datetime, timedelta

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(sub: str) -> str:
    expire = datetime.now(tz=UTC) + timedelta(minutes=settings.JWT_EXPIRES_MINUTES)
    payload = {"sub": sub, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    """Return the ``sub`` claim or raise jose.JWTError on failure."""
    data = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    return str(data["sub"])
