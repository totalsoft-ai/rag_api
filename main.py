# main.py
import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from starlette.responses import JSONResponse

from app.config import (
    VectorDBType,
    debug_mode,
    RAG_HOST,
    RAG_PORT,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    PDF_EXTRACT_IMAGES,
    VECTOR_DB_TYPE,
    LogMiddleware,
    logger,
)
from app.middleware import security_middleware
from app.routes import document_routes, pgvector_routes
from app.services.database import PSQLDatabase, ensure_vector_indexes, create_embeddings_table


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic goes here
    logger.info("=" * 60)
    logger.info("=== RAG API Starting ===")
    logger.info("=" * 60)

    # Log important configuration
    logger.info(f"Vector Store Type: {VECTOR_DB_TYPE.value}")
    logger.info(f"DB Schema: {os.getenv('DB_SCHEMA', 'public')}")
    logger.info(f"Embeddings Provider: {os.getenv('EMBEDDINGS_PROVIDER', 'openai')}")
    logger.info(f"Chunk Size: {CHUNK_SIZE} | Chunk Overlap: {CHUNK_OVERLAP}")

    # Log webhook configuration
    webhook_url = os.getenv("LIBRECHAT_WEBHOOK_URL")
    if webhook_url:
        logger.info(f"LibreChat Webhook: ENABLED ({webhook_url})")
    else:
        logger.info("LibreChat Webhook: DISABLED (LIBRECHAT_WEBHOOK_URL not set)")

    # Create bounded thread pool executor based on CPU cores
    max_workers = min(
        int(os.getenv("RAG_THREAD_POOL_SIZE", str(os.cpu_count()))), 8
    )  # Cap at 8
    app.state.thread_pool = ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="rag-worker"
    )
    logger.info(
        f"Thread Pool: {max_workers} workers (CPU cores: {os.cpu_count()})"
    )

    if VECTOR_DB_TYPE == VectorDBType.PGVECTOR:
        await PSQLDatabase.get_pool()  # Initialize the pool

        # Create namespace-based embeddings table if it doesn't exist
        await create_embeddings_table()

        # Keep old indexes for backward compatibility
        await ensure_vector_indexes()

    logger.info("=" * 60)
    logger.info("=== RAG API Ready ===")
    logger.info("=" * 60)

    yield

    # Cleanup logic
    logger.info("Shutting down thread pool")
    app.state.thread_pool.shutdown(wait=True)
    logger.info("Thread pool shutdown complete")


app = FastAPI(lifespan=lifespan, debug=debug_mode)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(LogMiddleware)

app.middleware("http")(security_middleware)

# Set state variables for use in routes
app.state.CHUNK_SIZE = CHUNK_SIZE
app.state.CHUNK_OVERLAP = CHUNK_OVERLAP
app.state.PDF_EXTRACT_IMAGES = PDF_EXTRACT_IMAGES

# Include routers
app.include_router(document_routes.router)
if debug_mode:
    app.include_router(router=pgvector_routes.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.debug(f"Validation error occurred")
    logger.debug(f"Validation errors: {exc.errors()}")

    # Build response content
    response_content = {
        "detail": exc.errors(),
        "message": "Request validation failed",
    }

    # Only attempt to read body for JSON requests (not multipart/form-data)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.body()
            response_content["body"] = body.decode()
            logger.debug(f"Raw request body: {body.decode()}")
        except RuntimeError as e:
            # Stream already consumed
            logger.debug(f"Could not read request body: {e}")
            response_content["body"] = None
    else:
        # For multipart/form-data or other types, don't attempt to read
        # (stream is already consumed by FastAPI during parameter parsing)
        response_content["body"] = None
        logger.debug(f"Request body not included for content-type: {content_type}")

    return JSONResponse(
        status_code=422,
        content=response_content,
    )


if __name__ == "__main__":
    uvicorn.run(app, host=RAG_HOST, port=RAG_PORT, log_config=None)
