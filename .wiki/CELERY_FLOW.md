# CELERY_FLOW.md — Background Task Architecture & State Flow

This document details the background queue structure, Celery tasks, scheduling policies, state synchronization, and idempotency guarantees.

---

## ⚙️ Celery App Configuration

The Celery application is initialized in [backend/tasks/celery_app.py](file:///d:/Mipoe/Mipoe-Backend/backend/tasks/celery_app.py) using the following parameters:
- **Broker**: `celery_broker_url` (Redis DB 0)
- **Backend**: `celery_result_backend` (Redis DB 1)
- **Task Serialization**: JSON format for task payload exchanges
- **Registered Modules**:
  - `backend.tasks.maintenance`
  - `backend.tasks.payouts`
  - `backend.tasks.emails`
  - `backend.tasks.metrics` (Scraper routines)

---

## ⚡ Task Registry & Lifecycle Flows

```
[ FastAPI Endpoint ] ──► Dispatch (delay / apply_async)
                                │
                                ▼
                       [ Redis Broker (DB 0) ]
                                │
                                ▼
                       [ Celery Worker Pool ]
                                │
                                ├─► Task Execution (asyncio.run)
                                ├─► Database Operations (AsyncSessionLocal)
                                └─► Error Handling & Auto-Retries
```

### 1. Transactional Email Queue (`emails.send_password_reset_email`)
- **File**: [backend/tasks/emails.py](file:///d:/Mipoe/Mipoe-Backend/backend/tasks/emails.py)
- **Trigger**: Called asynchronously by the `/request-password-reset` endpoint in the auth router.
- **Workflow**:
  1. FastAPI endpoint generates a cryptographically secure token and stores it in Redis (`reset:{token}`) with a 15-minute TTL.
  2. The endpoint triggers the task: `send_password_reset_email.delay(...)`.
  3. The worker loads the email template, instantiates the `EmailService` client, and calls the Resend API.
- **Robustness & Retries**:
  - `autoretry_for=(Exception,)` guarantees automatic retries upon network or API outages.
  - `retry_backoff=True` applies exponential backoff delays.
  - `retry_jitter=True` adds randomness to prevent simultaneous retries.
  - `max_retries=3` caps execution attempts.
  - **Idempotency**: An idempotency key (`password-reset:{reset_token}`) is sent in the header to Resend to prevent duplicate delivery.

### 2. Campaign Maintenance Tasks
- **File**: [backend/tasks/maintenance.py](file:///d:/Mipoe/Mipoe-Backend/backend/tasks/maintenance.py)
- **Trigger**: Run via Celery Beat schedules or local CLI command runners.
- **Tasks**:
  - `maintenance.deactivate_expired_campaigns`:
    1. Opens a database connection.
    2. Runs an update query setting `is_active = False` for all campaigns where `deadline < today` and `is_active = True`.
    3. Commits the transaction.
  - `maintenance.delete_rejected_clips`:
    1. Queries clips where `is_deleted_by_admin = True`.
    2. Deletes these records from the `submitted_clips` database table to keep clip storage clean.
    3. Commits the transaction.

### 3. Automatic Financial Payouts (`payouts.run_hourly_payouts`)
- **File**: [backend/tasks/payouts.py](file:///d:/Mipoe/Mipoe-Backend/backend/tasks/payouts.py)
- **Trigger**: Periodic schedule (hourly) managed via Celery Beat or CLI scheduler.
- **Payout Computation & Idempotency**:
  1. Queries all active campaigns with remaining allocated funds (`is_active = True` and `funds_allocated > 0`).
  2. For each campaign, fetches all accepted clips (`AcceptedClip`).
  3. For each clip, computes target earnings:
     $$\text{Target Earnings} = \lfloor \frac{\text{clip.view\_count}}{\text{campaign.view\_threshold}} \rfloor \times \text{campaign.cpv}$$
  4. Resolves due payout amount: `amount_due = Target Earnings - clip.amount_paid`.
  5. If `amount_due > 0` and `campaign.funds_allocated >= amount_due`:
     - Deducts `amount_due` from `campaign.funds_allocated`.
     - Splits earnings: **90%** goes to `creator.wallet_balance`, **10%** goes to `platform_wallet.balance`.
     - Appends transaction logs: `CreatorTransaction` (type: earning) and `BrandTransaction` (type: distribution) are marked `completed`.
     - Updates `clip.amount_paid = Target Earnings`.
     - Dispatches a database-backed notification to the creator.
  6. Saves updates by running `db.commit()` at the end of the transaction batch.
- **Idempotency Guarantee**: By comparing current milestone achievements against `clip.amount_paid` (which records the historical payout sum), the worker ensures that a clip is never paid twice for the same views.

### 4. Metrics Synchronizer Scraper (`metrics.fetch_and_update_metrics`)
- **File**: [backend/tasks/metrics.py](file:///d:/Mipoe/Mipoe-Backend/backend/tasks/metrics.py)
- **Trigger**: Scheduled scraper run.
- **Workflow**:
  1. Instantiates `instagrapi.Client` and logs in using settings credentials. Session cookies are serialized to `instagrapi.json` to prevent re-authentication blocks.
  2. Loads all clips with a valid url from the `accepted_clips` table.
  3. For each clip:
     - Extracts the Instagram Media Code from the URL.
     - Calls the Instagram web API to retrieve the current view count, caption, and publication timestamp.
     - Automatically updates the clip row database state.
     - Introduces a `time.sleep(2)` delay between requests to avoid IP rate limits.
  4. Recalculates `campaign.total_view_count` as the sum of all its children's view counts.

---

## 🗃️ Worker-to-Database State Management

Since workers process queued tasks out-of-band:
1. **Isolated DB Connections**: Every worker task invokes `AsyncSessionLocal()` inside its runner block to initialize a fresh, isolated SQLAlchemy transaction.
2. **Context Isolation**: No session state is shared across tasks. If one task throws an error, the database automatically rolls back its session, preventing dirty reads or partial writes.
3. **Async Loop Wrapping**: Since Celery tasks run in synchronous worker execution pools, the async database transaction block is wrapped in `asyncio.run()` to safely await queries without blocking worker daemon loops.
4. **Failure Handling**: Database connection failures cause the worker to retry (e.g. on Resend calls) or exit cleanly without commiting bad states.
