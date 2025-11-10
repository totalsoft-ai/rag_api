# app/services/database.py
import os
import asyncpg
from app.config import DSN, logger


# Database schema configuration
# Note: PostgreSQL folds unquoted identifiers to lowercase, so we normalize here
DB_SCHEMA = os.getenv("DB_SCHEMA", "public").lower()


class PSQLDatabase:
    pool = None

    @classmethod
    async def get_pool(cls):
        if cls.pool is None:
            async def _init_connection(conn: asyncpg.Connection):
                # Ensure pgvector extension exists and register adapter so Python lists map to VECTOR
                try:
                    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except Exception:
                    pass

                try:
                    from pgvector.asyncpg import register_vector  # type: ignore
                    await register_vector(conn)
                except Exception:
                    # If adapter is absent, inserts will require casting to ::vector in SQL
                    pass

            cls.pool = await asyncpg.create_pool(dsn=DSN, init=_init_connection)
        return cls.pool

    @classmethod
    async def close_pool(cls):
        if cls.pool is not None:
            await cls.pool.close()
            cls.pool = None


async def _deduplicate_chunk_id(conn, table_full_name: str):
    """Remove duplicate rows by chunk_id keeping the most recent by created_at."""
    await conn.execute(f"""
        WITH ranked AS (
            SELECT ctid, row_number() OVER (
                PARTITION BY chunk_id
                ORDER BY created_at DESC, ctid DESC
            ) AS rn
            FROM {table_full_name}
        )
        DELETE FROM {table_full_name} t
        USING ranked r
        WHERE t.ctid = r.ctid AND r.rn > 1
    """)


async def _ensure_unique_index_on_chunk_id(conn, table_full_name: str, index_name: str):
    """Ensure a unique index on chunk_id exists; deduplicate if needed and retry."""
    try:
        await conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_full_name}(chunk_id)")
    except Exception as e:
        # Likely duplicates exist; attempt dedup then retry once
        logger.warning(f"Failed to create unique index {index_name} on {table_full_name}: {str(e)}. Attempting to deduplicate and retry.")
        try:
            await _deduplicate_chunk_id(conn, table_full_name)
            await conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_full_name}(chunk_id)")
            logger.info(f"Created unique index {index_name} on {table_full_name} after deduplication")
        except Exception as e2:
            logger.error(f"Could not create unique index {index_name} on {table_full_name} even after deduplication: {str(e2)}")
            raise


async def create_embeddings_table():
    """Create the main embeddings table if it doesn't exist"""
    pool = await PSQLDatabase.get_pool()
    async with pool.acquire() as conn:
        # Ensure pgvector extension exists
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            pass
        # Create schema if it doesn't exist
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}")
        logger.info(f"Schema '{DB_SCHEMA}' created or already exists")

        # Check if table exists
        table_exists = await conn.fetchval(f"""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = '{DB_SCHEMA}'
                AND table_name = 'embeddings'
            );
        """)

        if not table_exists:
            # Create the embeddings table with namespace column and file_id
            await conn.execute(f"""
                CREATE TABLE {DB_SCHEMA}.embeddings (
                    id SERIAL PRIMARY KEY,
                    chunk_id TEXT,
                    file_id TEXT,
                    source TEXT,
                    chunk_index INTEGER,
                    text TEXT,
                    embedding vector(768),
                    namespace TEXT DEFAULT 'general',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create index on namespace for faster filtering
            await conn.execute(f"""
                CREATE INDEX idx_embeddings_namespace
                ON {DB_SCHEMA}.embeddings(namespace)
            """)

            # Create index on file_id for faster filtering by file
            await conn.execute(f"""
                CREATE INDEX idx_embeddings_file_id
                ON {DB_SCHEMA}.embeddings(file_id)
            """)

            # Ensure unique index on chunk_id
            await _ensure_unique_index_on_chunk_id(conn, f"{DB_SCHEMA}.embeddings", f"uniq_{DB_SCHEMA}_embeddings_chunk_id")
            logger.info("Successfully created embeddings table with namespace and file_id columns")
        else:
            # Check if namespace column exists
            namespace_column_exists = await conn.fetchval(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = '{DB_SCHEMA}'
                    AND table_name = 'embeddings'
                    AND column_name = 'namespace'
                );
            """)

            if not namespace_column_exists:
                # Add namespace column with default value
                await conn.execute(f"""
                    ALTER TABLE {DB_SCHEMA}.embeddings
                    ADD COLUMN namespace TEXT DEFAULT 'general'
                """)

                # Create index on namespace
                await conn.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_embeddings_namespace
                    ON {DB_SCHEMA}.embeddings(namespace)
                """)

                logger.info("Added namespace column to existing embeddings table")
            else:
                logger.info("Table and namespace column already exist")

            # Check if file_id column exists
            file_id_column_exists = await conn.fetchval(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = '{DB_SCHEMA}'
                    AND table_name = 'embeddings'
                    AND column_name = 'file_id'
                );
            """)

            if not file_id_column_exists:
                # Add file_id column
                await conn.execute(f"""
                    ALTER TABLE {DB_SCHEMA}.embeddings
                    ADD COLUMN file_id TEXT
                """)

                # Create index on file_id
                await conn.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_embeddings_file_id
                    ON {DB_SCHEMA}.embeddings(file_id)
                """)

                logger.info("Added file_id column to existing embeddings table")

            # Ensure unique index on chunk_id exists even for pre-existing tables
            await _ensure_unique_index_on_chunk_id(conn, f"{DB_SCHEMA}.embeddings", f"uniq_{DB_SCHEMA}_embeddings_chunk_id")

            # Ensure embedding column type is vector(768)
            try:
                await conn.execute(
                    f"""
                    ALTER TABLE {DB_SCHEMA}.embeddings
                      ALTER COLUMN embedding TYPE vector(768)
                      USING embedding::vector
                    """
                )
            except Exception:
                # Ignore if already correct
                pass


async def create_namespace_table(namespace: str):
    """Create a table for a specific namespace if it doesn't exist"""
    # Sanitize namespace name for SQL identifiers
    safe_namespace = (
        namespace.lower()
        .replace('-', '_')
        .replace(' ', '_')
        .replace('.', '_')
        .replace('@', '_')
    )

    pool = await PSQLDatabase.get_pool()
    async with pool.acquire() as conn:
        # Create schema if it doesn't exist
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}")

        # Create the namespace-specific table
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_SCHEMA}.{safe_namespace} (
                id SERIAL PRIMARY KEY,
                chunk_id TEXT,
                file_id TEXT,
                source TEXT,
                chunk_index INTEGER,
                text TEXT,
                embedding vector(768),
                namespace TEXT DEFAULT '{namespace}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create index on chunk_id for faster lookups
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{safe_namespace}_chunk_id
            ON {DB_SCHEMA}.{safe_namespace}(chunk_id)
        """)

        # Create index on file_id for faster filtering by file
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{safe_namespace}_file_id
            ON {DB_SCHEMA}.{safe_namespace}(file_id)
        """)

        # Ensure unique index on chunk_id for namespace table
        await _ensure_unique_index_on_chunk_id(conn, f"{DB_SCHEMA}.{safe_namespace}", f"uniq_{DB_SCHEMA}_{safe_namespace}_chunk_id")
        logger.info(f"Successfully created or verified table for namespace '{namespace}'")


async def ensure_vector_indexes():
    table_name = "langchain_pg_embedding"
    column_name = "custom_id"
    # You might want to standardize the index naming convention
    index_name = f"idx_{table_name}_{column_name}"

    pool = await PSQLDatabase.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name});
        """
        )

        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_file_id
            ON {table_name} ((cmetadata->>'file_id'));
        """
        )

        logger.info("Vector database indexes ensured")


async def pg_health_check() -> bool:
    try:
        pool = await PSQLDatabase.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return False
