# ID-based RAG FastAPI

## Overview
This project integrates Langchain with FastAPI in an Asynchronous, Scalable manner, providing a framework for document indexing and retrieval, using PostgreSQL/pgvector.

Files are organized into embeddings by `file_id`. The primary use case is for integration with [LibreChat](https://librechat.ai), but this simple API can be used for any ID-based use case.

The main reason to use the ID approach is to work with embeddings on a file-level. This makes for targeted queries when combined with file metadata stored in a database, such as is done by LibreChat.

The API will evolve over time to employ different querying/re-ranking methods, embedding models, and vector stores.

## Features
- **Document Management**: Methods for adding, retrieving, and deleting documents.
- **Vector Store**: Utilizes Langchain's vector store for efficient document retrieval.
- **Asynchronous Support**: Offers async operations for enhanced performance.
- **Namespace-Based Organization**: Organize embeddings by namespace/project for multi-tenant support.
  - Automatic table creation per namespace
  - Cross-namespace data isolation
  - Configurable schema via `DB_SCHEMA` environment variable

## Namespace Support

The API supports organizing documents into **namespaces** for better multi-tenant and project-based organization. **Namespaces are optional** - if not specified, documents use the default `"general"` namespace.

### Key Features:
- **Per-namespace tables**: Each namespace gets its own table (e.g., `public.project_a`)
- **Main embeddings table**: All namespaces also stored in central `public.embeddings` table
- **Auto-copy to 'general'**: Documents automatically copied to 'general' namespace (unless namespace contains 'totalsoft')
- **Configurable schema**: Use `DB_SCHEMA` environment variable to set PostgreSQL schema (default: `public`)
- **Query filtering**: Queries filter results by namespace and optionally by file_id (source)
- **DELETE isolation**: Delete operations are isolated per namespace - users can only delete documents from their own namespace
- **Webhook callbacks**: Automatic POST callbacks to LibreChat after embedding completion with status updates

### Providing Namespace

Namespace can be provided in **three ways** with the following priority:

1. **Request Body/Form Parameter** (highest priority)
2. **HTTP Header `X-Namespace`** (medium priority)
3. **Default Value**: `"general"` (lowest priority)

This applies to endpoints: `/embed`, `/local/embed`, and `/query`

### Usage Examples:

```bash
# 1. Upload document with namespace via form parameter
curl -X POST "http://localhost:8000/embed" \
  -F "file=@document.pdf" \
  -F "file_id=doc-001" \
  -F "namespace=project-alpha"

# 2. Upload document with namespace via HTTP header
curl -X POST "http://localhost:8000/embed" \
  -H "X-Namespace: project-alpha" \
  -F "file=@document.pdf" \
  -F "file_id=doc-001"

# 3. Upload without namespace (uses default "general")
curl -X POST "http://localhost:8000/embed" \
  -F "file=@document.pdf" \
  -F "file_id=doc-001"

# 4. Query within namespace via request body
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is this about?",
    "file_id": "doc-001",
    "namespace": "project-alpha",
    "k": 3
  }'

# 5. Query within namespace via HTTP header
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -H "X-Namespace: project-alpha" \
  -d '{
    "query": "What is this about?",
    "file_id": "doc-001",
    "k": 3
  }'

# 6. Query in default namespace (searches "general")
curl -X POST "http://localhost:8000/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is this about?",
    "file_id": "doc-001",
    "k": 3
  }'

# 7. Priority example: Body parameter overrides header
curl -X POST "http://localhost:8000/embed" \
  -H "X-Namespace: project-beta" \
  -F "namespace=project-alpha" \
  -F "file=@document.pdf" \
  -F "file_id=doc-001"
# Result: Uses "project-alpha" from form parameter

# 8. Delete documents with namespace isolation
curl -X DELETE "http://localhost:8000/documents" \
  -H "Content-Type: application/json" \
  -H "X-Namespace: john_doe_example_com" \
  -d '["doc-001", "doc-002"]'
# Result: Only deletes documents from namespace "john_doe_example_com"
```

For detailed information, see [NAMESPACE_SCHEMA_GUIDE.md](NAMESPACE_SCHEMA_GUIDE.md).

## Setup

### Getting Started

- **Configure `.env` file based on [section below](#environment-variables)**
- **Setup pgvector database:**
  - Run an existing PSQL/PGVector setup, or,
  - Docker: `docker compose up` (also starts RAG API)
    - or, use docker just for DB: `docker compose -f ./db-compose.yaml up`
- **Run API**:
  - Docker: `docker compose up` (also starts PSQL/pgvector)
    - or, use docker just for RAG API: `docker compose -f ./api-compose.yaml up`
  - Local:
    - Make sure to setup `DATABASE_URL` with the correct PostgreSQL connection string
    - Run the following commands (preferably in a [virtual environment](https://realpython.com/python-virtual-environments-a-primer/))
```bash
pip install -r requirements.txt
uvicorn main:app
```

### Environment Variables

The following environment variables are required to run the application:

- `RAG_OPENAI_API_KEY`: The API key for OpenAI API Embeddings (if using default settings).
    - Note: `OPENAI_API_KEY` will work but `RAG_OPENAI_API_KEY` will override it in order to not conflict with LibreChat setting.
- `RAG_OPENAI_BASEURL`: (Optional) The base URL for your OpenAI API Embeddings
- `RAG_OPENAI_PROXY`: (Optional) Proxy for OpenAI API Embeddings
    - Note: When using with LibreChat, you can also set `HTTP_PROXY` and `HTTPS_PROXY` environment variables in the `docker-compose.override.yml` file (see [Proxy Configuration](#proxy-configuration) section below)
- `VECTOR_DB_TYPE`: (Optional) select vector database type, default to `pgvector`.
- `DATABASE_URL`: PostgreSQL connection string for database connectivity. Format: `postgresql://user:password@host:port/database`
    - Example: `postgresql://myuser:mypassword@localhost:5432/mydatabase`
    - Required when `VECTOR_DB_TYPE=pgvector`
    - Must start with `postgresql://` or `postgres://`
- `DB_SCHEMA`: (Optional) The PostgreSQL schema name where tables will be created. Default value is "public".
    - **Note**: Schema names are automatically converted to lowercase (PostgreSQL standard behavior for unquoted identifiers)
    - Example: `DB_SCHEMA=MySchema` will create schema `myschema`
- `RAG_HOST`: (Optional) The hostname or IP address where the API server will run. Defaults to "0.0.0.0"
- `RAG_PORT`: (Optional) The port number where the API server will run. Defaults to port 8000.
- `JWT_SECRET`: (Optional) The secret key used for verifying JWT tokens for requests.
  - The secret is only used for verification. This basic approach assumes a signed JWT from elsewhere.
  - Omit to run API without requiring authentication
- `LIBRECHAT_WEBHOOK_URL`: (Optional) The base URL for sending webhook callbacks to LibreChat after embedding completion.
  - Format: `http://librechat:3080` (will automatically append `/api/files/webhooks/embedding`)
  - When set, RAG API will send POST requests with `{file_id, embedded: true/false, namespace, error?}` payload
  - Useful for updating document status in LibreChat after embedding processing
  - Omit to skip webhook callbacks

- `COLLECTION_NAME`: (Optional) The name of the collection in the vector store. Default value is "testcollection".
- `CHUNK_SIZE`: (Optional) The size of the chunks for text processing. Default value is "1500".
- `CHUNK_OVERLAP`: (Optional) The overlap between chunks during text processing. Default value is "100".
- `RAG_UPLOAD_DIR`: (Optional) The directory where uploaded files are stored. Default value is "./uploads/".
- `PDF_EXTRACT_IMAGES`: (Optional) A boolean value indicating whether to extract images from PDF files. Default value is "False".
- `DEBUG_RAG_API`: (Optional) Set to "True" to show more verbose logging output in the server console, and to enable postgresql database routes
- `DEBUG_PGVECTOR_QUERIES`: (Optional) Set to "True" to enable detailed PostgreSQL query logging for pgvector operations. Useful for debugging performance issues with vector database queries.
- `CONSOLE_JSON`: (Optional) Set to "True" to log as json for Cloud Logging aggregations
- `EMBEDDINGS_PROVIDER`: (Optional) either "openai", "bedrock", "azure", "huggingface", "huggingfacetei", "google_genai", "vertexai", or "ollama", where "huggingface" uses sentence_transformers; defaults to "openai"
- `EMBEDDINGS_MODEL`: (Optional) Set a valid embeddings model to use from the configured provider.
    - **Defaults**
    - openai: "text-embedding-3-small"
    - azure: "text-embedding-3-small" (will be used as your Azure Deployment)
    - huggingface: "sentence-transformers/all-MiniLM-L6-v2"
    - huggingfacetei: "http://huggingfacetei:3000". Hugging Face TEI uses model defined on TEI service launch.
    - vertexai: "text-embedding-004"
    - ollama: "nomic-embed-text"
    - bedrock: "amazon.titan-embed-text-v1"
    - google_genai: "gemini-embedding-001"
- `RAG_AZURE_OPENAI_API_VERSION`: (Optional) Default is `2023-05-15`. The version of the Azure OpenAI API.
- `RAG_AZURE_OPENAI_API_KEY`: (Optional) The API key for Azure OpenAI service.
    - Note: `AZURE_OPENAI_API_KEY` will work but `RAG_AZURE_OPENAI_API_KEY` will override it in order to not conflict with LibreChat setting.
- `RAG_AZURE_OPENAI_ENDPOINT`: (Optional) The endpoint URL for Azure OpenAI service, including the resource.
    - Example: `https://YOUR_RESOURCE_NAME.openai.azure.com`.
    - Note: `AZURE_OPENAI_ENDPOINT` will work but `RAG_AZURE_OPENAI_ENDPOINT` will override it in order to not conflict with LibreChat setting.
- `HF_TOKEN`: (Optional) if needed for `huggingface` option.
- `OLLAMA_BASE_URL`: (Optional) defaults to `http://ollama:11434`.
- `ATLAS_SEARCH_INDEX`: (Optional) the name of the vector search index if using Atlas MongoDB, defaults to `vector_index`
- `MONGO_VECTOR_COLLECTION`: Deprecated for MongoDB, please use `ATLAS_SEARCH_INDEX` and `COLLECTION_NAME`
- `AWS_DEFAULT_REGION`: (Optional) defaults to `us-east-1`
- `AWS_ACCESS_KEY_ID`: (Optional) needed for bedrock embeddings
- `AWS_SECRET_ACCESS_KEY`: (Optional) needed for bedrock embeddings
- `GOOGLE_API_KEY`, `GOOGLE_KEY`, `RAG_GOOGLE_API_KEY`: (Optional) Google API key for Google GenAI embeddings. Priority order: RAG_GOOGLE_API_KEY > GOOGLE_KEY > GOOGLE_API_KEY
- `AWS_SESSION_TOKEN`: (Optional) may be needed for bedrock embeddings
- `GOOGLE_APPLICATION_CREDENTIALS`: (Optional) needed for Google VertexAI embeddings. This should be a path to a service account credential file in JSON format, as accepted by [langchain](https://python.langchain.com/api_reference/google_vertexai/index.html)
- `RAG_CHECK_EMBEDDING_CTX_LENGTH` (Optional) Default is true, disabling this will send raw input to the embedder, use this for custom embedding models.

Make sure to set these environment variables before running the application. You can set them in a `.env` file or as system environment variables.

### Use Atlas MongoDB as Vector Database

Instead of using the default pgvector, we could use [Atlas MongoDB](https://www.mongodb.com/products/platform/atlas-vector-search) as the vector database. To do so, set the following environment variables

```env
VECTOR_DB_TYPE=atlas-mongo
ATLAS_MONGO_DB_URI=<mongodb+srv://...>
COLLECTION_NAME=<vector collection>
ATLAS_SEARCH_INDEX=<vector search index>
```

The `ATLAS_MONGO_DB_URI` could be the same or different from what is used by LibreChat. Even if it is the same, the `$COLLECTION_NAME` collection needs to be a completely new one, separate from all collections used by LibreChat. In addition,  create a vector search index for collection above (remember to assign `$ATLAS_SEARCH_INDEX`) with the following json:

```json
{
  "fields": [
    {
      "numDimensions": 768,
      "path": "embedding",
      "similarity": "cosine",
      "type": "vector"
    },
    {
      "path": "file_id",
      "type": "filter"
    }
  ]
}
```

Follow one of the [four documented methods](https://www.mongodb.com/docs/atlas/atlas-vector-search/create-index/#procedure) to create the vector index.


### Proxy Configuration

When using the RAG API with LibreChat and you need to configure proxy settings, you can set the `HTTP_PROXY` and `HTTPS_PROXY` environment variables in the [`docker-compose.override.yml`](https://www.librechat.ai/docs/configuration/docker_override) file (from the LibreChat repository):

```yaml
rag_api:
    environment:
        - HTTP_PROXY=<your-proxy>
        - HTTPS_PROXY=<your-proxy>
```

This configuration will ensure that all HTTP/HTTPS requests from the RAG API container are routed through your specified proxy server.


### Cloud Installation Settings:

#### AWS:
Make sure your RDS Postgres instance adheres to this requirement:

`The pgvector extension version 0.5.0 is available on database instances in Amazon RDS running PostgreSQL 15.4-R2 and higher, 14.9-R2 and higher, 13.12-R2 and higher, and 12.16-R2 and higher in all applicable AWS Regions, including the AWS GovCloud (US) Regions.`

In order to setup RDS Postgres with RAG API, you can follow these steps:

* Create a RDS Instance/Cluster using the provided [AWS Documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_CreateDBInstance.html).
* Login to the RDS Cluster using the Endpoint connection string from the RDS Console or from your IaC Solution output.
* The login is via the *Master User*.
* Create a dedicated database for rag_api:
``` create database rag_api;```.
* Create a dedicated user\role for that database:
``` create role rag;```

* Switch to the database you just created: ```\c rag_api```
* Enable the Vector extension: ```create extension vector;```
* Use the documentation provided above to set up the connection string to the RDS Postgres Instance\Cluster.

Notes:
  * Even though you're logging with a Master user, it doesn't have all the super user privileges, that's why we cannot use the command: ```create role x with superuser;```
  * If you do not enable the extension, rag_api service will throw an error that it cannot create the extension due to the note above.

### Dev notes:

#### Installing pre-commit formatter

Run the following commands to install pre-commit formatter, which uses [black](https://github.com/psf/black) code formatter:

```bash
pip install pre-commit
pre-commit install
```

