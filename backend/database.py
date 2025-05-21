from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "postgresql://bf68ec5_crmuser1:GdTomas2025.,@localhost:5432/bf68ec5_crmdb"

engine = create_engine(
    DATABASE_URL,
    connect_args={"client_encoding": "utf8"}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

