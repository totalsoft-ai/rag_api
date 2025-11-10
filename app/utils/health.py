# app/utils/health.py
from app.config import VECTOR_DB_TYPE, VectorDBType
from app.services.database import pg_health_check
# NOTE: mongo_client imported lazily only when needed (requires pymongo)


def is_health_ok():
    if VECTOR_DB_TYPE == VectorDBType.PGVECTOR:
        return pg_health_check()
    if VECTOR_DB_TYPE == VectorDBType.ATLAS_MONGO:
        # Lazy import to avoid requiring pymongo unless Atlas Mongo is used
        try:
            from app.services.mongo_client import mongo_health_check
        except ImportError:
            # If pymongo not installed, return False for health check
            return False
        return mongo_health_check()
    else:
        return True