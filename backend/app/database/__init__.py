"""Database package: connection management and initialisation."""

from app.database.connection import database_ready, get_connection, init_db
from app.models.generator import ensure_table as ensure_generator_table

__all__ = ["database_ready", "get_connection", "init_db"]
