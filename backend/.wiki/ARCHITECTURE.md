# ARCHITECTURE.md — Mipoe Backend

## High-Level Overview

The Mipoe Backend is a modular Python application using **FastAPI** for synchronous HTTP APIs and **Celery** for distributed background task processing. The architecture is designed for scalability, maintainability, and clear separation of concerns.

### Key Components
- **FastAPI App** (`main.py`): Entry point for HTTP requests, routing, and dependency injection.
- **API Routers** (`api/routers/`): Modular route definitions for admin, auth, brands, campaigns, creators, payments, and system endpoints.
- **Core** (`core/`): Configuration, security, and shared logic.
- **Database Layer** (`db/`): SQLAlchemy models, session management, and base classes.
- **Schemas** (`schemas/`): Pydantic models for request/response validation.
- **Services** (`services/`): Business logic, external integrations (e.g., Supabase, Resend), notification handling, and reusable email workflows.
- **Tasks** (`tasks/`): Celery app and distributed task modules (maintenance, metrics, payouts, email delivery).

### Interaction Patterns
- **HTTP requests** are handled by FastAPI routers, which use dependency injection to access DB/session/context.
- **Background jobs** are dispatched to Celery workers via the `tasks/` package, using Redis or DB as the broker.
- **No global mutable state**; all state is passed explicitly or stored in the DB/Redis.
- **Transactional emails** are composed in `services/` and delivered asynchronously through Celery tasks so user-facing endpoints stay responsive.

### State & Data Flow
- **Request lifecycle:** HTTP request → Router → Service → DB/Task → Response
- **Task lifecycle:** API/Service triggers Celery task → Task executes (possibly async) → Result logged or returned

### Extensibility
- New endpoints: Add router/service/schema, update API_CONTRACTS.md
- New tasks: Add Celery task, update API_CONTRACTS.md
- New email workflows: Add a reusable email builder/sender in `services/`, dispatch through `tasks/`, and document the trigger flow
- All changes: Update this wiki first
