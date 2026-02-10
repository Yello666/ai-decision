from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import get_settings


def get_db_url() -> str:
    settings = get_settings()
    if settings.USE_SQLITE:
        return "sqlite:///./test.db"
    
    password = settings.MYSQL_PASSWORD
    if password:
        password = password.replace("@", "%40")
    return (
        f"mysql+pymysql://{settings.MYSQL_USER}:{password}@"
        f"{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DB}"
    )


settings = get_settings()
db_url = get_db_url()

connect_args = {}
if settings.USE_SQLITE:
    connect_args = {"check_same_thread": False}

engine = create_engine(
    db_url,
    pool_pre_ping=True if not settings.USE_SQLITE else False,
    pool_size=settings.MYSQL_POOL_SIZE if not settings.USE_SQLITE else 5,
    max_overflow=settings.MYSQL_MAX_OVERFLOW if not settings.USE_SQLITE else 10,
    connect_args=connect_args
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
