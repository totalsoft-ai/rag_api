from typing import Optional
from langchain_core.embeddings import Embeddings

from .async_pg_vector import AsyncPgVector
from .extended_pg_vector import ExtendedPgVector
# NOTE: AtlasMongoVector imported lazily only when needed (requires langchain-mongodb)


def get_vector_store(
    connection_string: str,
    embeddings: Embeddings,
    collection_name: str,
    mode: str = "sync",
    search_index: Optional[str] = None
):
    if mode == "sync":
        return ExtendedPgVector(
            connection_string=connection_string,
            embedding_function=embeddings,
            collection_name=collection_name,
        )
    elif mode == "async":
        return AsyncPgVector(
            connection_string=connection_string,
            embedding_function=embeddings,
            collection_name=collection_name,
        )
    elif mode == "atlas-mongo":
        # Lazy import to avoid requiring langchain-mongodb unless Atlas Mongo is used
        try:
            from .atlas_mongo_vector import AtlasMongoVector
            from pymongo import MongoClient  # type: ignore
        except ImportError as e:
            raise ImportError(
                "MongoDB support requires langchain-mongodb and pymongo. "
                "Install with: pip install langchain-mongodb pymongo"
            ) from e

        mongo_db = MongoClient(connection_string).get_database()
        mong_collection = mongo_db[collection_name]
        return AtlasMongoVector(
            collection=mong_collection, embedding=embeddings, index_name=search_index
        )
    else:
        raise ValueError("Invalid mode specified. Choose 'sync', 'async', or 'atlas-mongo'.")