# INFRASTRUCTURE.md — Infrastructure Stack, Docker, & Services

This document details the system infrastructure, service dependencies, environment configurations, and deployment strategies for the Mipoe Backend.

---

## 🛠️ Infrastructure Stack & Service Dependencies

The application relies on several core infrastructure components and external SaaS integrations:

```
                  ┌──────────────────┐
                  │  Mipoe Backend   │
                  │   FastAPI Node   │
                  └────────┬─────────┘
        ┌──────────────────┼─────────────────┐
        ▼                  ▼                 ▼
┌──────────────┐    ┌──────────────┐   ┌──────────────┐
│  PostgreSQL  │    │ Redis Server │   │ Supabase Auth│
│  (Database)  │    │ (Queue/State)│   │ (OAuth Provider)
└──────────────┘    └──────────────┘   └──────────────┘
        ┌──────────────────┼─────────────────┐
        ▼                  ▼                 ▼
┌──────────────┐    ┌──────────────┐   ┌──────────────┐
│   Cashfree   │    │    Resend    │   │  Instagram   │
│  (Payments)  │    │(Email Portal)│   │(Media Scrape)│
└──────────────┘    └──────────────┘   └──────────────┘
```

1. **PostgreSQL**: Durable storage of campaigns, user accounts, transactions, notifications, and clip status. Sensitive fields (like PAN cards) are symmetrically encrypted using Fernet cryptography prior to saving.
2. **Redis**: In-memory high-throughput data store serving as Celery broker, result backend, and app state store.
3. **Supabase**: Handles external user sign-in via Google OAuth, synced into our local DB via `/api/auth/google-sync`. Also hosts a public storage bucket (`thumnail_folder`) to store scraped clip thumbnails.
4. **Cashfree**: Core payout and payment gateway used to process brand deposits, campaign budget allocations, and creator withdrawals. Additionally used for sync business PAN card validation.
5. **Resend**: Transactional email API provider for password resets and system updates.
6. **Instagram**: Source platform for clips; verified reel URLs are scraped to query view counts and clip metadata.
7. **Pillow (PIL)**: Python Image Library used by background tasks to normalize thumbnails into compressed WebP assets before uploading to Supabase Storage.
8. **yt-dlp**: Metadata extraction utility used by the thumbnail caching pipeline to resolve source thumbnail URLs without downloading the original media file.

---

## ⚡ Redis & Celery Partitioning

Redis is configured to use distinct numerical logical database partitions to avoid key collisions and isolate workloads:

| Partition ID | Purpose | URL Config Var | Description |
| :--- | :--- | :--- | :--- |
| **DB 0** | Celery Message Broker | `CELERY_BROKER_URL` | Receives tasks from FastAPI endpoints and distributes them to workers. |
| **DB 1** | Celery Result Backend | `CELERY_RESULT_BACKEND` | Stores task exit statuses and return JSON data. |
| **DB 2** | Application Cache / State | `REDIS_URL` | Stores transient records: active password reset tokens (`reset:{token}`) and JWT JTI blocklist values (`blocklist:{jti}`). |

### Celery Worker Pool Configuration
- **Concurrency**: On Windows, Celery struggles with multiprocessing forks. Therefore, workers must run under the **solo** pool:
  ```powershell
  celery -A backend.tasks.celery_app.celery_app worker --loglevel=info --pool=solo
  ```
- **Beat Scheduler**: Used to run recurring tasks (e.g. hourly payouts, daily metrics scraper):
  ```powershell
  celery -A backend.tasks.celery_app.celery_app beat --loglevel=info
  ```

---

## 🐋 Dockerization Strategy

The project utilizes Docker to standardize local development and production environments.

### 1. Local Redis Service Container
To run the Redis engine locally without configuring native services:
```powershell
docker run --name mipoe-redis -p 6379:6379 -d redis:7
```

### 2. Multi-Container Orchestration Blueprint (Future Production Setup)
For deployment, the application is divided into three primary container classes:
1. **`web`**: Runs the FastAPI application with Uvicorn (`uvicorn backend.main:app`).
2. **`worker`**: Runs the Celery task consumer (`celery -A backend.tasks.celery_app worker`).
3. **`beat`**: Runs the periodic timer scheduler (`celery -A backend.tasks.celery_app beat`).

*Example Dockerfile (`Dockerfile`):*
```dockerfile
FROM python:3.11-slim
WORKDIR /workspace
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "5000"]
```

---

## 🌐 Cloudflare Workers & Edge Integration Blueprint

To enhance performance, security, and scraper reliability, Cloudflare services should be integrated at the edge:

```
Client Request ──► Cloudflare Worker (Edge) ──► FastAPI Backend (Origin)
                        │
                        ├─► 1. Edge-side JWT Signature Verification
                        ├─► 2. Rate Limiting / DDoS Shielding
                        └─► 3. Scraping Proxy Network Routing
```

1. **Edge-side JWT Signature Verification**:
   - Cloudflare Workers can inspect authorization headers at the edge, verify the HS256 signature using Web Crypto APIs, and reject unauthorized requests before they hit the origin server.
2. **API Gateways & Rate Limiting**:
   - Edge Workers can enforce rate-limiting rules based on client IP or authenticated IDs, shielding the origin server from DDoS attacks.
3. **Scraping Proxy Networks**:
   - Since the Instagram scraper API (`instagrapi`) is prone to rate blocks (`HTTP 429`), Cloudflare Worker routing or external proxy rotation should be configured to cycle requests through dynamic IPs when checking reel views.

---

## 🔐 Environment Variables Specification

Ensure your local `.env` contains the following keys:

```bash
# Database Configuration
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:5432/<dbname>

# Security Config
JWT_SECRET_KEY=your-long-random-string-used-for-signing-tokens
JWT_ALGORITHM=HS256
TOKEN_CRYPT_KEY=your-32-byte-hex-or-base64-key-for-pii-encryption

# Redis Partition URLs
REDIS_URL=redis://localhost:6379/2
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Supabase Auth Integration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_KEY=your-service-role-key

# Cashfree Payments SDK Configuration
CASHFREE_APP_ID=your-cashfree-client-app-id
CASHFREE_SECRET_KEY=your-cashfree-secret-key
CASHFREE_API_URL=https://sandbox.cashfree.com/pg

# Resend Transactional Email Provider
RESEND_API_KEY=re_your_api_key
RESEND_FROM_EMAIL=no-reply@yourdomain.com
RESEND_REPLY_TO=support@yourdomain.com

# Scraper Credentials
INSTAGRAM_USERNAME=optional-instagram-username-for-metrics
INSTAGRAM_PASSWORD=optional-instagram-password
```
