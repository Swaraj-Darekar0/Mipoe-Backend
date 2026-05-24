# API_CONTRACTS.md - Mipoe Backend

## Core Endpoints
- **Auth:** `/login`, `/register`, `/refresh` - JWT-based authentication for brand, creator, and admin roles
- **Auth Recovery:** `/request-password-reset`, `/reset-password` - password reset token generation and password rotation
- **Brands:** `/api/brand/...` - brand profile, wallet, and campaign management
- **Campaigns:** `/api/campaigns/...` - campaign discovery and lifecycle operations
- **Creators:** `/api/creator/...` - creator profile, onboarding, and submission flows
- **Payments:** `/api/payments/...` - payment initiation, wallet operations, payout actions, and transaction history
- **System:** `/api/health`, `/api/system/...` - health checks and system information

## Data Models
- **SQLAlchemy models** live in `db/models.py`
- **Pydantic schemas** live in `schemas/`
- **Relationships:** brands, campaigns, creators, and transactions are linked via foreign keys

## Task Flow Logic
- **Celery Tasks:**
  - Defined in `tasks/` (e.g. `maintenance.py`, `metrics.py`, `payouts.py`, `emails.py`)
  - Triggered by API routes or scheduled jobs
  - Use Redis as broker/result backend and the database for durable business state
  - Must be idempotent and log failures/results

## Email Flow
- **Provider:** Resend
- **Composition:** reusable email builders and sender utilities live in `services/`
- **Delivery:** routers enqueue Celery email tasks so HTTP requests stay fast
- **Reset Flow:** request reset -> store short-lived token in Redis -> enqueue reset email -> user opens frontend reset URL -> frontend calls `/reset-password`

## Redis/Celery
- **Redis:** stores transient auth state such as password reset tokens and JWT blocklist entries
- **Celery:** executes background tasks such as maintenance, metrics sync, payouts, and email delivery
- **Pattern:** API triggers background work via Celery; workers process tasks independently and persist results in Redis/DB as needed

---

**All new endpoints, models, or tasks must be documented here before implementation.**
