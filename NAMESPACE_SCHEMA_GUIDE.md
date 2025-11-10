# Namespace-Based Schema Guide

This guide explains the new namespace-based storage schema that replaces the LangChain collection-based approach.

## Overview

The new schema is inspired by `tobe.py` and `tobe2.py`, providing a flatter, more efficient structure for storing document embeddings with namespace-based organization.

## Key Changes from LangChain Schema

### Old Schema (LangChain-based)
```sql
-- Two tables with JSONB metadata
langchain_pg_collection (
    uuid UUID PRIMARY KEY,
    name STRING,
    cmetadata JSON
)

langchain_pg_embedding (
    uuid UUID PRIMARY KEY,
    collection_id UUID REFERENCES langchain_pg_collection,
    embedding VECTOR,
    document STRING,
    cmetadata JSONB,  -- Contains file_id, user_id, digest, source, page
    custom_id STRING  -- Set to file_id
)
```

### New Schema (Namespace-based)
```sql
-- Single main table with flat columns + per-namespace tables
{schema}.embeddings (
    id SERIAL PRIMARY KEY,
    chunk_id TEXT UNIQUE,           -- UUID per chunk (not file_id)
    source TEXT,                     -- File path or file_id
    chunk_index INTEGER,             -- Position within document
    text TEXT,                       -- Document content
    embedding VECTOR(768),          -- Embedding vector
    namespace TEXT DEFAULT 'general', -- Namespace/project identifier
    created_at TIMESTAMP DEFAULT NOW
)

-- Namespace-specific tables (created dynamically)
{schema}.{sanitized_namespace} (
    -- Same structure as embeddings table
    -- Example: schema.project_a, schema.user_123
)
```

## Schema Features

### 1. Namespace Organization
- **namespace**: Top-level organization unit (e.g., 'project-a', 'user-123', 'general')
- Documents are stored in both:
  - Main `embeddings` table (all namespaces)
  - Namespace-specific table (e.g., `project_a`)
- Automatic copy to 'general' namespace (unless namespace is 'general' or contains 'totalsoft')

### 2. Chunk Identification
- **chunk_id**: Unique UUID per chunk (not shared across chunks of the same file)
- **source**: File identifier (replaces file_id)
- **chunk_index**: Order of chunk within the document (0-indexed)

### 3. Benefits
- **Faster queries**: Direct namespace filtering without JSONB operations
- **Isolation**: Each namespace can have its own table
- **Simpler schema**: Flat columns instead of nested JSONB
- **Better indexing**: Unique constraint on chunk_id, index on namespace
- **Flexibility**: Easy to add new namespaces without schema changes

## API Changes

### 1. Upload Endpoints

#### `/embed` - Upload file with namespace
```bash
curl -X POST "http://localhost:8000/embed" \
  -F "file=@document.pdf" \
  -F "file_id=my-doc-123" \
  -F "namespace=project-alpha"  # NEW PARAMETER
```

#### `/local/embed` - Embed local file
```json
{
  "filepath": "/path/to/file.pdf",
  "filename": "document.pdf",
  "file_content_type": "application/pdf",
  "file_id": "my-doc-123",
  "namespace": "project-alpha"  // NEW FIELD (optional, defaults to 'general')
}
```

### 2. Query Endpoints

#### `/query` - Query with namespace filtering
```json
{
  "query": "What is the main topic?",
  "file_id": "my-doc-123",
  "k": 4,
  "namespace": "project-alpha"  // NEW FIELD (optional, defaults to 'general')
}
```

## Database Configuration

Add to your `.env` file:

```bash
# Database schema (defaults to 'public')
DB_SCHEMA=public

# Note: Schema names are automatically converted to lowercase
# Example: DB_SCHEMA=MySchema will create schema 'myschema'
# This follows PostgreSQL standard behavior for unquoted identifiers
```

## Migration from Old Schema

### Option 1: Using Migration Script

```bash
# Dry run (preview without changes)
python migrate_to_namespace_schema.py --namespace general --dry-run

# Actual migration
python migrate_to_namespace_schema.py --namespace general --batch-size 100

# Verify migration
python migrate_to_namespace_schema.py --namespace general --verify
```

### Option 2: Manual Migration

```sql
-- 1. Create new tables (automatic on startup)
-- Tables are created automatically when the application starts

-- 2. Migrate data manually
INSERT INTO public.embeddings (chunk_id, source, chunk_index, text, embedding, namespace)
SELECT
    gen_random_uuid()::text,                    -- New unique chunk_id
    COALESCE(cmetadata->>'file_id', custom_id), -- source from file_id
    COALESCE((cmetadata->>'page')::int, 0),     -- chunk_index from page
    document,                                    -- text content
    embedding,                                   -- vector embedding
    'general'                                    -- namespace (customize as needed)
FROM langchain_pg_embedding;
```

## Namespace Naming Conventions

### Valid Namespace Names
- Alphanumeric: `project123`, `userABC`
- With separators: `project-alpha`, `user.123`, `team@company`

### Sanitization Rules
Characters are automatically converted for SQL table names:
- `-` → `_` (project-alpha → project_alpha)
- `.` → `_` (user.123 → user_123)
- `@` → `_` (team@company → team_company)
- Spaces → `_` (my project → my_project)
- Lowercase conversion

### Reserved Namespaces
- `general`: Default namespace, receives copies from other namespaces
- Namespaces containing `totalsoft`: Do not auto-copy to 'general'

## Data Flow

### Upload Flow
```
1. User uploads file with namespace='project-a'
   ↓
2. File split into chunks, each gets unique chunk_id (UUID)
   ↓
3. Chunks stored in:
   - public.embeddings (namespace='project-a')
   - public.project_a (namespace='project-a')
   - public.embeddings (namespace='general')  ← Auto-copy
   - public.general (namespace='general')     ← Auto-copy
```

### Query Flow
```
1. User queries with namespace='project-a'
   ↓
2. Search in public.embeddings WHERE namespace='project-a'
   ↓
3. Filter by source (file_id) if specified
   ↓
4. Return top k results with similarity scores
```

## Implementation Files

### New Files
- `app/services/database.py` - Added `create_embeddings_table()`, `create_namespace_table()`
- `app/services/vector_store/namespace_pg_vector.py` - New namespace vector store implementation
- `migrate_to_namespace_schema.py` - Migration utility script

### Modified Files
- `app/models.py` - Added namespace field to `StoreDocument`, `QueryRequestBody`, `QueryMultipleBody`
- `app/routes/document_routes.py` - Updated to use namespace vector store
- `main.py` - Added table initialization on startup

## Performance Considerations

### Indexing
- Unique index on `chunk_id` (with deduplication on conflict)
- B-tree index on `namespace` for fast filtering
- Consider adding index on `source` if frequently filtering by file

### Query Optimization
```sql
-- Good: Uses namespace index
SELECT * FROM embeddings WHERE namespace = 'project-a';

-- Better: Uses both namespace and source
SELECT * FROM embeddings WHERE namespace = 'project-a' AND source = 'file-123';

-- Best: Query namespace-specific table directly
SELECT * FROM project_a WHERE source = 'file-123';
```

### Bulk Operations
- Upsert operations use `executemany` for batch efficiency
- Default batch size: 100 documents
- Configurable via migration script `--batch-size` parameter

## Troubleshooting

### Issue: Table already exists
**Solution**: The system handles existing tables gracefully and adds missing columns if needed.

### Issue: Namespace contains invalid characters
**Solution**: Characters are automatically sanitized. Check logs for the sanitized table name.

### Issue: Migration fails with duplicate chunk_id
**Solution**: The system automatically deduplicates, keeping the most recent entry by `created_at`.

### Issue: Old and new schemas conflict
**Solution**: Both schemas can coexist. The new implementation doesn't modify old tables.

## Best Practices

1. **Use descriptive namespace names**: `project-alpha` instead of `pa`
2. **Consistent naming**: Stick to one convention (e.g., `project-name` or `project_name`)
3. **Namespace per project/user**: Isolate data by logical boundaries
4. **Monitor table growth**: Each namespace creates a new table
5. **Regular cleanup**: Remove unused namespace tables periodically

## Example Usage

### Python Client
```python
import requests

# Upload document to namespace
files = {'file': open('document.pdf', 'rb')}
data = {
    'file_id': 'doc-001',
    'namespace': 'research-project'
}
response = requests.post('http://localhost:8000/embed', files=files, data=data)

# Query within namespace
query = {
    'query': 'What are the main findings?',
    'file_id': 'doc-001',
    'k': 5,
    'namespace': 'research-project'
}
response = requests.post('http://localhost:8000/query', json=query)
results = response.json()
```

### Curl Examples
```bash
# Upload
curl -X POST http://localhost:8000/embed \
  -F "file=@paper.pdf" \
  -F "file_id=paper-001" \
  -F "namespace=research"

# Query
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "summarize the results",
    "file_id": "paper-001",
    "namespace": "research",
    "k": 3
  }'
```

## Future Enhancements

- [ ] MongoDB implementation with namespace support
- [ ] Namespace-level access control
- [ ] Cross-namespace search
- [ ] Namespace analytics and usage metrics
- [ ] Automatic namespace cleanup based on age/usage
