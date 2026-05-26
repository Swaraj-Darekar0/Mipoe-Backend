# SYSTEM.md — Mipoe Backend Contract & Coding Standards

This document establishes the architecture contract for the Mipoe FastAPI + Celery + Redis backend. Any developer or agent modifying this repository must adhere strictly to these patterns and rules.

---

## 🏛️ Application Structure & Patterns

The Mipoe Backend uses **FastAPI** for synchronous/asynchronous HTTP endpoints, **SQLAlchemy (Async)** for data persistence, **Redis** for state caching and queuing, and **Celery** for background worker execution.

### 1. Router Layout & Dependency Injection
- **App Instance**: Configured in [backend/main.py](file:///d:/Mipoe/Mipoe-Backend/backend/main.py) with CORS configuration and global exception handlers.
- **Combined Router**: All module routes are registered in [backend/api/router.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/router.py) and nested under the FastAPI instance.
- **Dependencies**: Located in [backend/api/deps.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/deps.py). Endpoints should avoid instantiating databases, clients, or services directly. Instead, they must inject sessions using FastAPI dependencies:
  - Database access: Use `db: AsyncSession = Depends(get_db)` (or the shorthand `db: AsyncSession = DbSession`).
  - Authentication & Authz: Use `current_user: CurrentUser = Depends(get_current_user)` or restrict access with `Depends(require_role("brand", "creator"))`.

### 2. Asynchronous Programming Patterns
- **FastAPI Endpoints**: Must be declared using `async def`. All DB queries or external network requests within routers must be awaited.
- **Database Engine**: SQLAlchemy runs in async mode via the `asyncpg` driver (`postgresql+asyncpg://...`). All DB statements must be run via async execution, e.g.:
  ```python
  result = await db.execute(select(Campaign).where(Campaign.is_active.is_(True)))
  campaigns = result.scalars().all()
  ```
- **Celery Tasks**: Celery workers run in synchronous execution contexts (configured with the `solo` pool on Windows). However, because the database session pool is fully async, Celery tasks must wrap their internal database code inside an async inner function and execute it synchronously via `asyncio.run()`.
  * *Correct Pattern*:
    ```python
    @celery_app.task(name="maintenance.delete_rejected_clips")
    def delete_rejected_clips():
        import asyncio
        async def _run():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(SubmittedClip).where(SubmittedClip.is_deleted_by_admin.is_(True)))
                await db.commit()
        asyncio.run(_run())
    ```

### 3. State Management & Cache
- **Stateless Web Nodes**: FastAPI server nodes are entirely stateless. No global lists, dictionaries, or in-memory caches may be used to store business state.
- **Durable State**: All persistent state must reside in PostgreSQL.
- **Transient State**: Redis is used for all transient cache variables:
  - **Token Revocation (Blocklist)**: revoking an access token on `/logout` puts its JTI claim into Redis with a TTL matching its remaining validity: `blocklist:{jti}`.
  - **Password Reset Verification**: token-to-user links are stored in Redis under the key `reset:{token}` with a TTL corresponding to `PASSWORD_RESET_TOKEN_TTL_SECONDS` (default: 900 seconds).

---

## 🚨 Error Handling Conventions

All endpoints must return consistent, standardized JSON error responses.

1. **HTTP Exceptions**: Always raise a FastAPI `HTTPException` with a specific status code and a descriptive payload.
   ```python
   raise HTTPException(
       status_code=status.HTTP_400_BAD_REQUEST, 
       detail="Insufficient wallet balance"
   )
   ```
2. **Global Exception Mapping**: Global interceptors are declared in [backend/main.py](file:///d:/Mipoe/Mipoe-Backend/backend/main.py) to translate exceptions to standard responses:
   - `HTTPException` is mapped to return `{"msg": exc.detail}`.
   - Any unhandled `Exception` is caught by a global fallback returning a 500 status code: `{"msg": "Internal server error", "error": str(exc)}`.
3. **Pydantic Validation Handlers**: Input validation failures on Pydantic schemas automatically raise `RequestValidationError`, resulting in a standard FastAPI HTTP 422 Unprocessable Entity response containing structural error locations.

---

## 🎨 Global Coding Standards

To maintain clean and uniform code:
- **Language Level**: Python 3.11+ is the project target.
- **Formatting**:
  - Code must be formatted with **Black** (standard line length 120).
  - Imports must be sorted and grouped using **isort** (Standard Library first, Third Party second, Local Project third).
- **Typing**:
  - Full type hinting is required for all function arguments, returns, and variables.
  - Pydantic models must be used for all API request body payloads and serialization structures.
- **Docstrings**:
  - All public modules, classes, functions, and services must include docstrings outlining purpose, parameters, and exceptions raised.

---

## 🔄 Self-Maintenance Rule

> [!IMPORTANT]
> **The LLM Wiki is a living document and the ultimate Ground Truth for this project.**
>
> 1. You (the AI system or the developer) are strictly responsible for keeping the wiki directory updated.
> 2. Whenever the codebase evolves, the wiki files MUST be modified in the **same commit** as the code changes.
>    - If a **Celery task** is added or modified, update [CELERY_FLOW.md](CELERY_FLOW.md).
>    - If a **Redis key structure** or caching strategy is updated, update [INFRASTRUCTURE.md](INFRASTRUCTURE.md).
>    - If an **API endpoint**, path, or Pydantic model is created/modified, update [API_CONTRACTS.md](API_CONTRACTS.md).
>    - If a **configuration setting** or env variable is added/removed, update [INFRASTRUCTURE.md](INFRASTRUCTURE.md).
