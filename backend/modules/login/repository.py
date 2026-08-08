from sqlalchemy.orm import Session
from sqlalchemy import select, Column, Integer, String, Boolean
from backend.core.database import Base
from backend.core.security import verify_password

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    role = Column(String, default="admin")

def get_by_email(db: Session, email: str):
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()

def validate_user(db: Session, email: str, password: str):
    user = get_by_email(db, email)
    if user and verify_password(password, user.password_hash) and user.is_active:
        return user
    return None
