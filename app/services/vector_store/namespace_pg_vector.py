import os
import asyncio
from typing import List, Dict, Any, Optional
from uuid import uuid4
import numpy as np
from langchain_core.documents import Document
from app.config import logger
from app.services.database import PSQLDatabase, create_namespace_table, DB_SCHEMA


def _sanitize_namespace(namespace: str) -> str:
    """Sanitize namespace name for SQL identifiers"""
    return (
        namespace.lower()
        .replace('-', '_')
        .replace(' ', '_')
        .replace('.', '_')
        .replace('@', '_')
    )


def _embedding_to_pgvector_string(embedding: List[float]) -> str:
    """Convert embedding list to pgvector-compatible string format"""
    return '[' + ','.join(str(x) for x in embedding) + ']'


class NamespacePgVector:
    """Custom PostgreSQL vector store using namespace-based schema"""

    def __init__(self, embeddings, namespace: str = "general"):
        self.embeddings = embeddings
        self.namespace = namespace
        self.safe_namespace = _sanitize_namespace(namespace)

    async def upsert_documents(
        self,
        documents: List[Document],
        chunk_indices: Optional[List[int]] = None,
        copy_to_general: bool = False
    ):
        """Upsert documents to PostgreSQL with embeddings"""
        if not documents:
            return

        # Generate embeddings in parallel using thread pool
        texts = [doc.page_content for doc in documents]
        embeddings = await asyncio.to_thread(
            self.embeddings.embed_documents,
            texts
        )

        # Prepare data for insertion
        data_with_embeddings = []
        for i, (doc, embedding) in enumerate(zip(documents, embeddings)):
            chunk_id = doc.metadata.get('chunk_id') or str(uuid4())
            chunk_index = chunk_indices[i] if chunk_indices else doc.metadata.get('chunk_index', i)

            data_item = {
                'chunk_id': chunk_id,
                'file_id': doc.metadata.get('file_id', ''),
                'source': doc.metadata.get('source', ''),
                'chunk_index': chunk_index,
                'text': doc.page_content,
                'embedding': embedding,
                'namespace': self.namespace
            }
            data_with_embeddings.append(data_item)

        # Upsert to database
        await self._upsert_to_postgres(data_with_embeddings, copy_to_general)
        logger.info(f"Upserted {len(data_with_embeddings)} documents to namespace '{self.namespace}'")

    async def _upsert_to_postgres(self, data_with_embeddings: List[Dict[str, Any]], copy_to_general: bool = False):
        """Upsert embeddings data to PostgreSQL"""
        pool = await PSQLDatabase.get_pool()

        # Create namespace-specific table if it doesn't exist
        await create_namespace_table(self.namespace)

        async with pool.acquire() as conn:
            # Prepare values for batch insert
            # Convert embeddings to pgvector string format for asyncpg compatibility
            values = [
                (
                    item['chunk_id'],
                    item['file_id'],
                    item['source'],
                    item['chunk_index'],
                    item['text'],
                    _embedding_to_pgvector_string(item['embedding']),
                    self.namespace
                )
                for item in data_with_embeddings
            ]

            # Upsert to main embeddings table
            logger.info(f"Upserting {len(values)} items to main embeddings table")
            await conn.executemany(
                f"""
                INSERT INTO {DB_SCHEMA}.embeddings (chunk_id, file_id, source, chunk_index, text, embedding, namespace)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (chunk_id) DO UPDATE
                SET file_id = EXCLUDED.file_id,
                    source = EXCLUDED.source,
                    chunk_index = EXCLUDED.chunk_index,
                    text = EXCLUDED.text,
                    embedding = EXCLUDED.embedding,
                    namespace = EXCLUDED.namespace
                """,
                values
            )
            logger.info("Successfully upserted to main embeddings table")

            # Upsert to namespace-specific table
            logger.info(f"Upserting {len(values)} items to namespace table '{self.safe_namespace}'")
            await conn.executemany(
                f"""
                INSERT INTO {DB_SCHEMA}.{self.safe_namespace} (chunk_id, file_id, source, chunk_index, text, embedding, namespace)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (chunk_id) DO UPDATE
                SET file_id = EXCLUDED.file_id,
                    source = EXCLUDED.source,
                    chunk_index = EXCLUDED.chunk_index,
                    text = EXCLUDED.text,
                    embedding = EXCLUDED.embedding,
                    namespace = EXCLUDED.namespace
                """,
                values
            )
            logger.info(f"Successfully upserted to namespace table '{self.safe_namespace}'")

            # Copy to 'general' namespace if requested and not already 'general' or 'totalsoft'
            if copy_to_general and self.namespace != 'general' and 'totalsoft' not in self.namespace.lower():
                general_values = [
                    (v[0], v[1], v[2], v[3], v[4], v[5], 'general')  # Replace namespace with 'general'
                    for v in values
                ]

                # Create general namespace table if needed
                await create_namespace_table('general')

                logger.info(f"Upserting {len(general_values)} items to 'general' namespace")
                await conn.executemany(
                    f"""
                    INSERT INTO {DB_SCHEMA}.embeddings (chunk_id, file_id, source, chunk_index, text, embedding, namespace)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (chunk_id) DO UPDATE
                    SET file_id = EXCLUDED.file_id,
                        source = EXCLUDED.source,
                        chunk_index = EXCLUDED.chunk_index,
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        namespace = EXCLUDED.namespace
                    """,
                    general_values
                )
                await conn.executemany(
                    f"""
                    INSERT INTO {DB_SCHEMA}.general (chunk_id, file_id, source, chunk_index, text, embedding, namespace)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (chunk_id) DO UPDATE
                    SET file_id = EXCLUDED.file_id,
                        source = EXCLUDED.source,
                        chunk_index = EXCLUDED.chunk_index,
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        namespace = EXCLUDED.namespace
                    """,
                    general_values
                )
                logger.info(f"Also added {len(general_values)} documents to 'general' namespace")

    async def similarity_search(
        self,
        query: str,
        k: int = 4,
        filter_file_id: Optional[str] = None,
        filter_source: Optional[str] = None
    ) -> List[Document]:
        """Search for similar documents using vector similarity

        Args:
            query: Search query text
            k: Number of results to return
            filter_file_id: Optional file_id to filter by (preferred)
            filter_source: Optional source path to filter by (deprecated, use filter_file_id)
        """
        # Generate query embedding
        query_embedding = await asyncio.to_thread(
            self.embeddings.embed_query,
            query
        )

        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            # Build query with optional file_id or source filter
            where_clause = f"WHERE namespace = '{self.namespace}'"
            if filter_file_id:
                where_clause += f" AND file_id = '{filter_file_id}'"
            elif filter_source:
                # Backward compatibility: support filtering by source if file_id not provided
                where_clause += f" AND source = '{filter_source}'"

            results = await conn.fetch(f"""
                SELECT chunk_id, file_id, source, chunk_index, text,
                       1 - (embedding <=> $1) as similarity
                FROM {DB_SCHEMA}.embeddings
                {where_clause}
                ORDER BY embedding <=> $1
                LIMIT $2
            """, query_embedding, k)

            documents = [
                Document(
                    page_content=row['text'],
                    metadata={
                        'chunk_id': row['chunk_id'],
                        'file_id': row['file_id'],
                        'source': row['source'],
                        'chunk_index': row['chunk_index'],
                        'similarity': row['similarity'],
                        'namespace': self.namespace
                    }
                )
                for row in results
            ]

            return documents

    async def delete_by_file_id(self, file_id: str):
        """Delete all documents with the given file_id"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            # Delete from main table
            deleted_main = await conn.execute(
                f"""
                DELETE FROM {DB_SCHEMA}.embeddings
                WHERE namespace = $1 AND file_id = $2
                """,
                self.namespace, file_id
            )
            logger.info(f"Deleted from main table by file_id: {deleted_main}")

            # Delete from namespace-specific table
            deleted_namespace = await conn.execute(
                f"""
                DELETE FROM {DB_SCHEMA}.{self.safe_namespace}
                WHERE file_id = $1
                """,
                file_id
            )
            logger.info(f"Deleted from namespace table by file_id: {deleted_namespace}")

    async def delete_by_source(self, source: str):
        """Delete all documents with the given source (deprecated, use delete_by_file_id)"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            # Delete from main table
            deleted_main = await conn.execute(
                f"""
                DELETE FROM {DB_SCHEMA}.embeddings
                WHERE namespace = $1 AND source = $2
                """,
                self.namespace, source
            )
            logger.info(f"Deleted from main table: {deleted_main}")

            # Delete from namespace-specific table
            deleted_namespace = await conn.execute(
                f"""
                DELETE FROM {DB_SCHEMA}.{self.safe_namespace}
                WHERE source = $1
                """,
                source
            )
            logger.info(f"Deleted from namespace table: {deleted_namespace}")

    async def delete_by_chunk_ids(self, chunk_ids: List[str]):
        """Delete documents with the given chunk IDs"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            # Delete from main table
            deleted_main = await conn.execute(
                f"""
                DELETE FROM {DB_SCHEMA}.embeddings
                WHERE chunk_id = ANY($1::text[])
                """,
                chunk_ids
            )
            logger.info(f"Deleted {deleted_main} from main table")

            # Delete from namespace-specific table
            deleted_namespace = await conn.execute(
                f"""
                DELETE FROM {DB_SCHEMA}.{self.safe_namespace}
                WHERE chunk_id = ANY($1::text[])
                """,
                chunk_ids
            )
            logger.info(f"Deleted {deleted_namespace} from namespace table")

    async def get_by_file_id(self, file_id: str) -> List[Document]:
        """Get all documents with the given file_id"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            results = await conn.fetch(f"""
                SELECT chunk_id, file_id, source, chunk_index, text, namespace
                FROM {DB_SCHEMA}.embeddings
                WHERE namespace = $1 AND file_id = $2
                ORDER BY chunk_index
            """, self.namespace, file_id)

            documents = [
                Document(
                    page_content=row['text'],
                    metadata={
                        'chunk_id': row['chunk_id'],
                        'file_id': row['file_id'],
                        'source': row['source'],
                        'chunk_index': row['chunk_index'],
                        'namespace': row['namespace']
                    }
                )
                for row in results
            ]

            return documents

    async def get_by_source(self, source: str) -> List[Document]:
        """Get all documents with the given source (deprecated, use get_by_file_id)"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            results = await conn.fetch(f"""
                SELECT chunk_id, file_id, source, chunk_index, text, namespace
                FROM {DB_SCHEMA}.embeddings
                WHERE namespace = $1 AND source = $2
                ORDER BY chunk_index
            """, self.namespace, source)

            documents = [
                Document(
                    page_content=row['text'],
                    metadata={
                        'chunk_id': row['chunk_id'],
                        'file_id': row.get('file_id', ''),
                        'source': row['source'],
                        'chunk_index': row['chunk_index'],
                        'namespace': row['namespace']
                    }
                )
                for row in results
            ]

            return documents

    async def count_by_file_id(self, file_id: str) -> int:
        """Count documents with the given file_id (with fallback to source for backward compatibility)"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            # First try by file_id
            count = await conn.fetchval(f"""
                SELECT COUNT(*)
                FROM {DB_SCHEMA}.embeddings
                WHERE namespace = $1 AND file_id = $2
            """, self.namespace, file_id)

            # If not found and file_id looks like a path, try by source as fallback
            if count == 0 and ('/' in file_id or '\\' in file_id):
                logger.info(f"No results by file_id, trying source fallback for: {file_id}")
                count = await conn.fetchval(f"""
                    SELECT COUNT(*)
                    FROM {DB_SCHEMA}.embeddings
                    WHERE namespace = $1 AND source = $2
                """, self.namespace, file_id)

            return count or 0

    async def count_by_source(self, source: str) -> int:
        """Count documents with the given source (deprecated, use count_by_file_id)"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval(f"""
                SELECT COUNT(*)
                FROM {DB_SCHEMA}.embeddings
                WHERE namespace = $1 AND source = $2
            """, self.namespace, source)

            return count or 0

    async def get_all_file_ids(self) -> List[str]:
        """Get all unique file_ids in this namespace"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            results = await conn.fetch(f"""
                SELECT DISTINCT file_id
                FROM {DB_SCHEMA}.embeddings
                WHERE namespace = $1 AND file_id IS NOT NULL
                ORDER BY file_id
            """, self.namespace)

            return [row['file_id'] for row in results]

    async def get_all_sources(self) -> List[str]:
        """Get all unique sources in this namespace (deprecated, use get_all_file_ids)"""
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            results = await conn.fetch(f"""
                SELECT DISTINCT source
                FROM {DB_SCHEMA}.embeddings
                WHERE namespace = $1
                ORDER BY source
            """, self.namespace)

            return [row['source'] for row in results]
