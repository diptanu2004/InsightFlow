"""Declarative base shared by every Phase 6 model. Kept in its own module (no engine/session
imports here) so Alembic's env.py can import just the metadata without pulling in a live DB
connection at import time.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
