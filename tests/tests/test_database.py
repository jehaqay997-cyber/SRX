
from app.database import Base, SessionLocal, engine


def test_database_setup():
    assert engine is not None
    assert SessionLocal is not None
    assert Base is not None
    assert engine.dialect.name == "sqlite"
