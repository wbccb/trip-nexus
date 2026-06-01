# TripNexus

English | [简体中文](README.md)

TripNexus is an AI travel planning application built with large language models, Function Calling, RAG, LangGraph-based agent orchestration, and map visualization. It covers the full workflow from collecting travel inspiration and understanding user intent to generating structured itineraries, replanning part of a trip, and rendering routes on a map.

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Common APIs](#common-apis)
- [Project Structure](#project-structure)
- [Development Scripts](#development-scripts)
- [Documentation](#documentation)
- [License](#license)

## Features

- **AI itinerary generation and editing**: Generate structured trips from destination, duration, budget, travel pace, preferences, and constraints. Edit, replan a day, or replan a partial time window with natural language.
- **Travel inspiration inbox**: Upload PDFs, Markdown, and TXT files. Import social or web URLs with URL preprocessing, risk grading, fallback handling, and in-place retry for failed sources.
- **RAG-enhanced planning**: Search private knowledge bases and public web sources. Evidence Budgeting controls summary and body evidence to reduce context overflow.
- **Function Calling tools**: Use a unified protocol for weather, geocoding, POI lookup, and other tools, with caching, timeouts, circuit breakers, and normalized errors.
- **Agent orchestration and streaming UX**: Use LangGraph to manage planning and execution state. Stream workflow events through SSE with status inspection, pause, resume, and retry controls.
- **Map visualization**: Display itinerary POIs, routes, and map layers with Amap street tiles, Amap satellite tiles, and CartoDB.
- **Multi-tenant model configuration**: Store user-level LLM settings, validate connectivity before saving, and refresh model instances after configuration changes.

## Tech Stack

| Layer | Technologies |
| --- | --- |
| Frontend | React 19, Vite, Ant Design, MapLibre, Deck.gl, React Map GL |
| Backend | Python 3.12, FastAPI, Pydantic, Uvicorn |
| LLM / Agent | LangChain, LangGraph, OpenAI-compatible APIs, Ollama-compatible APIs |
| RAG / Knowledge Base | Chroma, Sentence Transformers, SearXNG, BeautifulSoup, Unstructured |
| Storage | SQLite for local/test storage, Redis for short-term cache, MySQL for production storage |
| Maps | Folium, Amap tiles, CartoDB |
| Observability | Structured logs, workflow metrics, error codes, timeouts, circuit breakers |

## Architecture

```text
React + Vite Web
  |-- Chat / Trip / Knowledge / Map UI
  |-- SSE consumer and local state rendering
  v
FastAPI Backend
  |-- routes: auth, flow, trip, knowledge, map
  |-- logic: main flow, trip normalization, knowledge orchestration
  |-- llm: prompt building, model routing, function calling, streaming adapter
  |-- rag: intent recognition, search, crawl, validation, vector retrieval
  |-- agent: LangGraph plan-and-execute loop
  |-- observability: metrics, limits, cache, errors
  v
Storage and External Services
  |-- SQLite / Redis / MySQL
  |-- Chroma vector store
  |-- OpenAI-compatible or Ollama-compatible models
  |-- SearXNG and web sources
```

The main business entry point is `/api/flow/stream`. The frontend submits a user message, trip constraints, and optional knowledge-base parameters. The backend then handles intent recognition, context preparation, RAG retrieval, tool calls, itinerary generation, conflict detection, and final result streaming.

## Quick Start

### 1. Clone the repository

```bash
git clone <your-repository-url>
cd TripNexus
```

### 2. Prepare the Python environment

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Install frontend dependencies

```bash
cd web
pnpm install
cd ..
```

### 4. Configure environment variables

The backend loads `.env.development` or `.env.production` based on `ENVIRONMENT`, while system environment variables take precedence. For local development, edit `.env.development` or export variables with the same names before starting the backend.

At minimum, review these backend values:

```dotenv
ENVIRONMENT=development
JWT_SECRET_KEY=replace-with-a-local-secret

ANALYSIS_PROVIDER=openai_compatible
ANALYSIS_BASE_URL=http://localhost:11434/v1
ANALYSIS_MODEL_NAME=your-analysis-model
ANALYSIS_API_KEY=your-api-key

GENERATION_PROVIDER=openai_compatible
GENERATION_BASE_URL=http://localhost:11434/v1
GENERATION_MODEL_NAME=your-generation-model
GENERATION_API_KEY=your-api-key

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=http://localhost:11434/v1
EMBEDDING_MODEL_NAME=your-embedding-model
EMBEDDING_API_KEY=your-api-key

SEARXNG_URL=http://localhost:8080
TRIP_CONTEXT_STORAGE_TYPE=test
AUTH_DB_BACKEND=sqlite
```

The frontend points to `http://127.0.0.1:8000` by default. To override the frontend API base URL, create `web/.env.local`:

```bash
cd web
printf 'VITE_API_BASE=http://127.0.0.1:8000\n' > .env.local
```

### 5. Start the backend

```bash
source venv/bin/activate
PYTHONPATH=. uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### 6. Start the frontend

```bash
cd web
pnpm run dev
```

Open the local Vite URL, usually `http://localhost:5173`.

## Environment Variables

| Variable | Description |
| --- | --- |
| `ENVIRONMENT` | Runtime environment, usually `development` or `production` |
| `JWT_SECRET_KEY` | Secret key for JWT signing |
| `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` | Initial super-admin account |
| `ANALYSIS_*` | Model settings for intent recognition and parameter extraction |
| `GENERATION_*` | Model settings for itinerary generation and editing |
| `EMBEDDING_*` | Embedding model settings |
| `SEARXNG_URL` | SearXNG metasearch service URL |
| `CHROMA_DB_PATH` | Chroma vector-store path |
| `TRIP_CONTEXT_STORAGE_TYPE` | Trip context storage type: `test` or `prod` |
| `AUTH_DB_BACKEND` | Authentication database backend: `sqlite` or `mysql` |
| `REDIS_*` | Redis configuration for short-term cache |
| `MYSQL_*` | MySQL configuration for production storage |
| `CORS_ORIGINS` | Allowed frontend origins for the backend |

## Common APIs

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/flow/stream` | Main SSE workflow entry point for generating or editing trips |
| `GET` | `/api/flow/status` | Query workflow status |
| `POST` | `/api/flow/control` | Pause, resume, or retry a workflow |
| `GET` | `/api/flow/metrics` | Query detailed workflow metrics |
| `GET` | `/api/flow/metrics/summary` | Query aggregated workflow metrics |
| `GET` | `/api/knowledge/bases` | List knowledge bases |
| `POST` | `/api/knowledge/bases` | Create a knowledge base |
| `POST` | `/api/knowledge/bases/{knowledge_base_id}/upload` | Upload files to a knowledge base |
| `POST` | `/api/knowledge/bases/{knowledge_base_id}/ingest/url` | Import a URL source |
| `POST` | `/api/knowledge/preprocess/url` | Preprocess a URL and preview extraction quality |
| `GET` | `/api/knowledge/bases/{knowledge_base_id}/sources` | List knowledge sources |
| `PATCH` | `/api/knowledge/bases/{knowledge_base_id}/sources/{source_id}` | Update source text and rebuild chunks |
| `DELETE` | `/api/knowledge/bases/{knowledge_base_id}/sources/{source_id}` | Delete a knowledge source |

## Project Structure

```text
TripNexus/
├── src/
│   ├── api/              # FastAPI entry point, routes, schemas, and orchestration
│   ├── auth/             # Users, JWT, admin initialization, and database access
│   ├── llm/              # LLM management, prompts, tools, streaming adapter
│   ├── rag/              # Search, crawl, validation, and vector retrieval
│   ├── agent/            # LangGraph agent orchestration
│   ├── map/              # Map HTML rendering
│   ├── frontend/context/ # Conversation context and storage abstractions
│   ├── models/           # Domain models
│   └── observability/    # Metrics, cache, concurrency limits, error normalization
├── web/
│   ├── src/api/          # Frontend API clients and SSE handling
│   ├── src/components/   # React UI components
│   ├── src/hooks/        # Session, trip, knowledge, and auth state hooks
│   └── src/utils/        # Trip normalization and debug helpers
├── scripts/              # Replay, report, and helper scripts
├── sql/                  # Database initialization scripts
├── searxng/              # SearXNG notes and configuration
├── requirements.txt      # Python dependencies
└── pyproject.toml        # Python project metadata
```

## Development Scripts

Backend:

```bash
source venv/bin/activate
PYTHONPATH=. uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

Frontend:

```bash
cd web
pnpm run dev
pnpm run build
pnpm run preview
```

## Documentation

- [Frontend notes](web/README.md)
- [SearXNG notes](searxng/README.md)
- [SQL notes](sql/README.md)
- [Backend entry point](src/api/app.py)
- [Main workflow routes](src/api/routes/flow.py)
- [Knowledge routes](src/api/routes/knowledge.py)
- [Trip logic](src/api/logic/trip.py)
- [LLM manager](src/llm/llm_manager.py)
- [RAG pipeline](src/rag/rag_main.py)

## License

This project is licensed under the [MIT License](LICENSE).
