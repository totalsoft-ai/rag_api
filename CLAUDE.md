# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ID-based RAG FastAPI** is a Retrieval-Augmented Generation (RAG) API that integrates LangChain with FastAPI to provide document indexing, embedding, and retrieval capabilities. The primary use case is integration with LibreChat for enhanced document-based conversations.

**Key Capabilities:**
- Document ingestion and text extraction (PDF, DOCX, CSV, XLSX, PPTX, Markdown, etc.)
- Multiple vector store backends (PostgreSQL/pgvector, MongoDB Atlas)
- Multiple embeddings providers (OpenAI, Azure, HuggingFace, Ollama, AWS Bedrock, Google GenAI/VertexAI)
- **Namespace-based document organization** for multi-tenant and project-level isolation
- File-level operations with unique chunk identification
- Async document processing with metadata preservation
- Configurable database schema via `DB_SCHEMA` environment variable

## Technology Stack

- **Framework**: FastAPI (async Python web framework)
- **Language**: Python 3.10+
- **Vector Stores**: PostgreSQL with pgvector extension, MongoDB Atlas
- **Document Processing**: LangChain with custom loaders
- **Database Drivers**: asyncpg (PostgreSQL), pymongo (MongoDB)
- **Testing**: pytest with async support
- **Code Quality**: Black formatter via pre-commit hooks

## Commands

### Setup and Installation
```bash
# Install dependencies
pip install -r requirements.txt

# Setup pre-commit hooks for code formatting
pip install pre-commit
pre-commit install

# Migrate existing data to namespace schema (if you have old data)
python migrate_to_namespace_schema.py --namespace general --dry-run  # Preview
python migrate_to_namespace_schema.py --namespace general            # Migrate
python migrate_to_namespace_schema.py --namespace general --verify   # Verify
```

### Development Server
```bash
# Run server locally (no hot reload)
uvicorn main:app

# Run with hot reload for development
uvicorn main:app --reload
```

### Docker Development
```bash
# Full stack - Database + API (recommended for local development)
docker compose up

# Database only - PostgreSQL with pgvector
# Use when API runs locally or you need a shared development database
docker compose -f ./db-compose.yaml up

# API only - Requires external database connection
# Configure via DATABASE_URL environment variable
docker compose -f ./api-compose.yaml up

# Build and run in detached mode
docker compose build
docker compose up -d
```

### Testing
```bash
# Run all tests
pytest

# Run tests with early exit on first failure
pytest --maxfail=1 --disable-warnings

# Run tests as CI does (with JUnit XML output)
pytest --maxfail=1 --disable-warnings --junitxml=test-results.xml
```

### CI/CD Pipeline
- **GitHub Actions workflow** (.github/workflows/ci.yml)
- Triggers on push to main and pull requests
- Python 3.12 with dependency caching
- Runs pytest with JUnit XML output
- Test results uploaded as artifacts

### Code Formatting
```bash
# Format code manually (pre-commit hooks also handle this)
black .
```

### Debug Mode
Enable debug features via environment variables:

```bash
# Enable verbose logging and PostgreSQL debug routes
DEBUG_RAG_API=True

# Enable detailed PostgreSQL query logging (performance tuning)
DEBUG_PGVECTOR_QUERIES=True
```

When `DEBUG_RAG_API=True`, debug routes become available:
- `GET /db/tables` - List all database tables by schema
- `GET /db/tables/columns` - Get column details for a table
- `GET /test/check_index` - Verify index existence
- `GET /records/all` - Fetch all records from legacy tables
- `GET /records` - Filter legacy records by custom_id

## Architecture

### High-Level Structure

The codebase follows a layered architecture pattern:

```
Client Request
    ↓
FastAPI App (main.py)
    ↓
Middleware Stack (Security → Logging → CORS)
    ↓
Routes Layer (app/routes/)
    ↓
Services Layer (app/services/)
    ├─→ Vector Store Factory (PostgreSQL or MongoDB)
    ├─→ Database Service (Connection management)
    └─→ Utils (Document loaders, health checks)
    ↓
External Systems (PostgreSQL/MongoDB, Embeddings APIs)
```

### Key Architectural Patterns

**1. Async-First Design**
- All I/O operations use async/await patterns
- Blocking operations (document loading, embeddings) run in thread pool executors
- Database operations use async drivers (asyncpg for PostgreSQL)
- Thread pool executor initialized in lifespan context manager
  - Max workers: Configurable via `RAG_THREAD_POOL_SIZE` (default: min(CPU cores, 8))
  - Access via `request.app.state.thread_pool` in route handlers
  - Graceful shutdown on app termination

**2. Factory Pattern for Vector Stores**
- `VectorStoreFactory` (app/services/vector_store/factory.py) creates appropriate vector store instances
- Supports PostgreSQL (pgvector) and MongoDB Atlas backends
- Configured via `VECTOR_STORE` environment variable
- **Three PostgreSQL implementations available**:
  - `namespace_pg_vector`: Primary implementation with namespace-based schema (recommended)
  - `async_pg_vector`: Async wrapper around LangChain's PGVector (legacy)
  - `extended_pg_vector`: Synchronous LangChain PGVector (legacy, backward compatibility)

**3. Namespace-Based Document Organization**
- Documents organized by `namespace` for multi-tenant support
- Each namespace gets its own table (e.g., `{schema}.project_a`)
- All namespaces also stored in main `{schema}.embeddings` table
- **~~Auto-copy to 'general' namespace~~ [DISABLED]**:
  - ~~Documents automatically copied to 'general' for cross-namespace search~~
  - ~~Exception: Namespaces containing 'totalsoft' (case-insensitive) are NOT copied to 'general'~~
  - ~~Allows project-specific isolation while maintaining optional global access~~
  - **Note**: This feature is currently disabled. Documents remain isolated in their respective namespaces.
- Unique `chunk_id` per chunk (UUID), `source` stores file identifier
- Schema configurable via `DB_SCHEMA` environment variable (default: "public")
- Namespace names sanitized for SQL identifiers (special chars like `-`, `.` replaced with `_`)
- **Namespace can be provided via multiple methods** (priority order):
  - Request body/form parameter (highest priority)
  - `X-Namespace` HTTP header
  - Default value: "general"
  - Supported on endpoints: `/embed`, `/local/embed`, `/query`

**4. Multi-Provider Embeddings**
- Centralized embeddings configuration in `app/config.py`
- `get_embeddings()` returns appropriate provider based on `EMBEDDINGS_PROVIDER` env var
- Supports: OpenAI, Azure OpenAI, HuggingFace, Ollama, AWS Bedrock, Google GenAI, Google VertexAI

**5. Middleware Chain**
- Security middleware (`app/middleware/security.py`): Optional JWT authentication
- Logging middleware (`app/middleware/logging.py`): Request/response logging with JSON or standard format
- CORS middleware: Configured for cross-origin requests

### Component Details

**Main Entry Point** (`main.py`)
- FastAPI app with lifespan context manager
- Initializes database connections on startup, closes on shutdown
- Mounts routes and middleware
- Health check endpoint at `/`

**Routes Layer** (`app/routes/document_routes.py`)
- `/embed` (POST) - Upload and process documents with embeddings, accepts `namespace` parameter
- `/local/embed` (POST) - Embed local file with namespace support
- `/text` (POST) - Extract text from documents without creating embeddings (parsing only)
- `/query` (POST) - Semantic search with namespace and metadata filtering
  - **File-specific search**: Provide `file_id` to search within a single document
  - **Global search**: Omit `file_id` to search across all documents in namespace
- `/ids` (GET) - Get all document IDs
- `/documents` (GET) - Get documents by IDs
- `/documents` (DELETE) - Delete documents by IDs
- `/health` (GET) - Health check endpoint
- Debug routes for PostgreSQL inspection (when `DEBUG_RAG_API=True`):
  - `/db/tables` - List database tables by schema
  - `/db/tables/columns` - Get columns for a specific table
  - `/test/check_index` - Check if index exists
  - `/records/all` - Get all records from legacy tables
  - `/records` - Filter records by custom_id

**Services Layer** (`app/services/`)
- `vector_store/`: Factory and implementations for PostgreSQL/MongoDB vector stores
  - `namespace_pg_vector.py`: New namespace-based PostgreSQL implementation
  - `extended_pg_vector.py`: LangChain-based PostgreSQL implementation (legacy)
  - `async_pg_vector.py`: Async wrapper for legacy implementation
  - `atlas_mongo_vector.py`: MongoDB Atlas implementation
- `database.py`: Async database connection management, schema creation functions
- Thread pool executor usage for blocking operations

**Utils Layer** (`app/utils/`)
- `document_loader.py`: Multi-format document loading with custom loaders
  - **PDFs**: `SafePyPDFLoader` - Custom wrapper with graceful fallback for image extraction failures
  - **CSVs**: Automatic encoding detection using `chardet` library
    - BOM (Byte Order Mark) detection for UTF-16/32
    - Automatic conversion of non-UTF-8 files to temporary UTF-8 file
    - Cleanup of temporary files after loading
  - **DOCX, XLSX, PPTX, Markdown**: LangChain loaders with text cleaning (null byte removal)
  - **Supported formats**: PDF, CSV, DOCX, XLSX, PPTX, MD, EPUB, XML, RST, JSON, TXT (60+ code extensions)
- `health_check.py`: Database connectivity checks

**Models** (`app/models.py`)
- Pydantic models for request/response validation
- `FileUpload`, `QueryRequest`, `QueryResponse`, etc.

**Configuration** (`app/config.py`)
- Centralized environment variable management
- Embeddings provider factory
- Database connection settings
- Security and logging configuration

### Important Implementation Details

**Document Processing Flow:**
1. File uploaded via `/upload` endpoint
2. Document loader selected based on file extension
3. Content loaded asynchronously (blocking ops in thread pool)
4. Text split into chunks using LangChain's `RecursiveCharacterTextSplitter`
   - **Chunk Size**: Configurable via `CHUNK_SIZE` (default: 1500 characters)
   - **Chunk Overlap**: Configurable via `CHUNK_OVERLAP` (default: 100 characters)
   - **Smart Separators**: Respects punctuation and sentence boundaries
   - **Default Separator Priority**: `\n\n` (paragraphs) → `\n` (lines) → `. ` (sentences) → `! ` → `? ` → `; ` → `: ` → `, ` → ` ` (spaces) → `""` (characters)
   - **Customizable**: Override via `TEXT_SEPARATORS` environment variable
5. Embeddings generated via configured provider
6. Chunks stored in vector store with metadata (file_id, page, source, chunk_id, chunk_index)

**Vector Store Operations:**
- PostgreSQL: Uses LangChain's `PGVector` with asyncpg
- MongoDB: Uses LangChain's `MongoDBAtlasVectorSearch`
- Both support similarity search with metadata filtering
- Bulk operations by `file_id` for efficient document management

**Namespace Usage:**
Namespace can be provided via request body/form or HTTP header with priority:
```bash
# Using form parameter (highest priority)
curl -X POST "http://localhost:8000/embed" \
  -F "namespace=project-alpha" \
  -F "file_id=doc-001" \
  -F "file=@document.pdf"

# Using HTTP header
curl -X POST "http://localhost:8000/embed" \
  -H "X-Namespace: project-alpha" \
  -F "file_id=doc-001" \
  -F "file=@document.pdf"

# Priority demonstration (form parameter wins)
curl -X POST "http://localhost:8000/embed" \
  -H "X-Namespace: project-beta" \
  -F "namespace=project-alpha" \
  -F "file_id=doc-001" \
  -F "file=@document.pdf"
# Result: Uses 'project-alpha' from form parameter
```

**Query Endpoint Usage:**
The `/query` endpoint supports both file-specific and global search:

```bash
# File-specific search (search within a single document)
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning concepts",
    "file_id": "doc-001",
    "k": 4
  }'

# Global search (search across all documents in namespace)
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning concepts",
    "k": 10
  }'

# Global search with specific namespace
curl -X POST "http://localhost:8000/query" \
  -H "X-Namespace: project-alpha" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "neural networks",
    "k": 5
  }'

# Global search with namespace in body (highest priority)
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deep learning",
    "namespace": "ai-research",
    "k": 10
  }'

# Search with text fallback (if no embeddings match)
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "specific technical term",
    "file_id": "documentation-v2",
    "allow_text_search": true
  }'
```

**Query Parameters:**
- `query` (required): The search text
- `file_id` (optional): When provided, searches only within this document. When omitted, searches across all documents in the namespace.
- `k` (optional, default: 4): Number of results to return
- `namespace` (optional, default: "general"): Namespace to search in (can also be provided via `X-Namespace` header)
- `entity_id` (optional): User/entity identifier for authorization
- `allow_text_search` (optional, default: false): Fallback to PostgreSQL ILIKE text search if vector search returns no results

**Error Handling:**
- Custom logging setup in `app/middleware/logging.py`
- JSON logging support via `CONSOLE_JSON` environment variable
- Structured error responses with HTTP status codes
- **Standardized error messages** via `ERROR_MESSAGES` class in `app/constants.py`
  - Context-aware error descriptions (FILE_NOT_FOUND, PANDOC_NOT_INSTALLED, etc.)
  - User-friendly error formatting
- Database operation failures logged with full stack traces
- Health check requests logged at DEBUG level to reduce noise

**Security:**
- Optional JWT middleware enabled via `USE_SECURITY_MIDDLEWARE`
- Token validation against `SECRET_KEY` and `ALGORITHM`
- CORS configured for frontend integration

## Development Guidelines

**Async Patterns:**
- Use `async def` for all route handlers and database operations
- Wrap blocking operations (file I/O, embeddings) in `asyncio.to_thread()` or use executor
- Database operations use async context managers for connection handling

**Code Formatting:**
- Black formatter enforced via pre-commit hooks
- Runs automatically on commit
- Manual format: `black .`

**Testing:**
- Tests in `tests/` directory
- Use `pytest` with async support
- Test database operations with fixtures
- Run tests before committing: `pytest --maxfail=1 --disable-warnings`

**Configuration:**
- All settings via environment variables (see `app/config.py` and `.env.example`)
- **Environment Variable Hierarchy**: RAG-prefixed variables override standard ones
  - Example: `RAG_OPENAI_API_KEY` takes precedence over `OPENAI_API_KEY`
  - Also applies to: `RAG_AZURE_OPENAI_API_KEY`, `RAG_GOOGLE_API_KEY`, etc.
- **Required**: `EMBEDDINGS_PROVIDER`, `VECTOR_DB_TYPE`, `DATABASE_URL` (for PostgreSQL)
- **Optional**: JWT settings, logging format, debug mode, PDF image extraction
- **Database Connection**: `DATABASE_URL` (format: `postgresql://user:password@host:port/database`)
  - Required when using `VECTOR_DB_TYPE=pgvector`
  - Must start with `postgresql://` or `postgres://`
- **Database Schema**: `DB_SCHEMA` (default: "public") - controls which PostgreSQL schema to use for tables
  - Note: Schema names are automatically converted to lowercase (PostgreSQL standard)
- **Text Chunking**:
  - `CHUNK_SIZE` (default: 1500) - Maximum characters per chunk
  - `CHUNK_OVERLAP` (default: 100) - Overlapping characters between chunks
  - `TEXT_SEPARATORS` (optional) - Custom separators for text splitting (comma-separated, use `\n` for newlines)
    - Example: `TEXT_SEPARATORS="\n\n,\n,. ,! ,? , ,"`
    - Default: Respects paragraphs, lines, sentences, and punctuation boundaries
- **Thread Pool**: `RAG_THREAD_POOL_SIZE` (default: min(CPU cores, 8)) - max workers for blocking operations

**Adding New Document Types:**
- Extend `load_document()` in `app/utils/document_loader.py`
- Add file extension to supported types check in routes
- Create custom loader if needed (see CSV loader example)
- Ensure async compatibility with `asyncio.to_thread()`

**Adding New Embeddings Providers:**
- Add provider case to `get_embeddings()` in `app/config.py`
- Add required environment variables to config class
- Update documentation and example .env files

**Adding New Vector Store Backends:**
- Implement new store in `app/services/vector_store/`
- Add factory case in `VectorStoreFactory`
- Ensure async compatibility
- Update configuration and documentation
