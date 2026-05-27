# API_CONTRACTS.md — Router Groups, Schemas, & API Map

This document serves as the comprehensive endpoint schema mapping and API contract reference for the Mipoe Backend.

---

## 🔐 Global Request Headers

Authenticated endpoints require the HttpOnly session cookie set in the request:
```http
Cookie: access_token=<access_token>
```
- Alternatively, endpoints can accept `Authorization: Bearer <access_token>` headers for API testing.
- The signature is validated against `JWT_SECRET_KEY` using the `HS256` algorithm.
- Access token payload must contain the `type: "access"` claim.
- Token revocation is enforced by checking if the token's `jti` is blocklisted in Redis DB 2 (`blocklist:<jti>`).

---

## 🗃️ Feature Routers & Endpoint Maps

### 1. Authentication Router (`/`)
- **File**: [backend/api/routers/auth.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/routers/auth.py)
- **Dependencies**: No auth cookies or headers required for registration/login.

| Method | Endpoint | Request Schema | Response / Actions |
| :--- | :--- | :--- | :--- |
| `POST` | `/register` | `RegisterRequest` | Hash password, create DB record, set `access_token` HttpOnly cookie, return metadata. |
| `POST` | `/login` | `LoginRequest` | Verify password hash, set `access_token` HttpOnly cookie, return metadata. |
| `POST` | `/request-password-reset` | `PasswordResetRequest` | Store temporary reset token in Redis, dispatch Celery reset email. |
| `POST` | `/reset-password` | `ResetPasswordRequest` | Verify Redis token, update user password, delete token from Redis. |
| `DELETE`| `/logout` | *Cookie: access_token* | Deletes the `access_token` cookie and blocklists the active JWT `jti` in Redis. |
| `POST` | `/api/auth/google-sync` | *Header: Bearer Supabase* | Validates token with Supabase, sets local `access_token` HttpOnly cookie, and returns user metadata. |

---

### 2. Campaigns Router (`/`)
- **File**: [backend/api/routers/campaigns.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/routers/campaigns.py)
- **Authentication**: Optional / Public.

| Method | Endpoint | Query / Path | Response |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/campaigns` | None | Returns a list of all active campaign payloads. |
| `GET` | `/api/campaigns/{campaign_id}` | `campaign_id` (Path) | Returns detailed campaign details, containing ranked clips and creator rankings. |

---

### 3. Creators Router (`/`)
- **File**: [backend/api/routers/creators.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/routers/creators.py)
- **Role Requirement**: `creator`.

| Method | Endpoint | Request Schema / Query | Response / Actions |
| :--- | :--- | :--- | :--- |
| `POST` | `/verify-instagram` | `VerifyInstagramRequest` | Triggers a background thread scraper worker to link/verify username. |
| `GET` | `/api/creator/your-campaigns` | None | Returns active campaigns where the creator has submitted or accepted clips. |
| `POST` | `/api/creator/submit-clip` | `SubmitClipRequest` | Creates a `SubmittedClip` row (max 5 submissions per campaign). |
| `GET` | `/api/creator/campaign-clips` | `campaign_id` (Query) | Returns all submitted and accepted clips for a campaign. |
| `DELETE`| `/api/creator/clip/{clip_id}` | `clip_id` (Path) | Deletes clip. Reverts view count totals from Campaign if accepted. |
| `GET` | `/api/creator/profile` | None | Retrieves creator's profile settings and completion status. |
| `PUT` | `/api/creator/profile` | `UpdateCreatorProfileRequest` | Updates bio, nickname, and phone. Sets `profile_completed=True`. |

---

### 4. Brands Router (`/`)
- **File**: [backend/api/routers/brands.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/routers/brands.py)
- **Role Requirement**: `brand`.

| Method | Endpoint | Request Schema / Path | Response / Actions |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/brand/campaigns` | `CreateCampaignRequest` | Creates a new draft campaign (initially `is_active=False`). |
| `GET` | `/api/brand/campaigns` | None | Lists all campaigns owned by the authenticated brand. |
| `DELETE`| `/api/brand/campaigns/{campaign_id}`| `campaign_id` (Path) | Deletes campaign; refunds remaining budget back to brand wallet. |
| `PUT` | `/api/brand/campaigns/{id}/image` | `{"image_url": str}` | Updates the promotional banner image for the campaign. |
| `PUT` | `/api/brand/campaigns/{id}/budget` | `{"budget": float}` | Updates total budget allocation settings. |
| `PUT` | `/api/brand/campaigns/{id}/requirements`| `{"requirements": str}` | Updates campaign creator briefs. |
| `PUT` | `/api/brand/campaigns/{id}/status` | `{"is_active": bool}` | Sets campaign state (active or paused). |
| `PUT` | `/api/brand/campaigns/{id}/view_threshold`| `{"view_threshold": int}` | Modifies view threshold settings per payout milestone. |
| `PUT` | `/api/brand/campaigns/{id}/deadline` | `{"deadline": "YYYY-MM-DD"}`| Updates end date constraints. |
| `GET` | `/api/brand/campaigns/{id}/pending-payouts`| `campaign_id` (Path) | Lists unpaid milestone values for creators associated with the campaign. |
| `GET` | `/api/brand/campaigns/{campaign_id}/clips`| `campaign_id` (Path) | Retrieves all accepted and submitted/pending clips with creator names plus zero-initialized metric fields (`view_count`, `like_count`, `comment_count`) for the campaign. |
| `PUT` | `/api/brand/campaigns/{campaign_id}/clips/{clip_id}/status` | `UpdateClipStatusRequest` | Brand-owned moderation action. Approves a submitted clip into `accepted_clips` or rejects it with feedback for creator notification. |
| `GET` | `/api/brand/profile` | None | Retrieves brand company metadata. |
| `PUT` | `/api/brand/profile` | `UpdateBrandProfileRequest`| Updates username and contact phone. |
| `POST`| `/api/brand/onboarding/verify-pan` | `VerifyPanRequest` | Queues Cashfree PAN sync verification background task. |
| `POST`| `/api/brand/onboarding/profile` | `SubmitBrandProfileRequest` | Submits logo/socials, transitions status to `pending_verification` for admin review. |

---

### 5. Payments Router (`/api/payments` prefix or direct routes)
- **File**: [backend/api/routers/payments.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/routers/payments.py)
- **Authentication**: Required (varies by role).

| Method | Endpoint | Role | Payload / Query | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/create-deposit-order` | Brand | `{"amount": float}` | Initiates Cashfree order; returns `payment_session_id`. |
| `POST` | `/verify-deposit` | Brand | `{"order_id": str}` | Verifies Cashfree payment; updates `brand.wallet_balance`. |
| `GET` | `/virtual-account` | Brand | None | Returns or creates Axis bank virtual account details. |
| `GET` | `/wallet-balance` | Both | None | Returns current role balance in INR. |
| `POST` | `/allocate-budget` | Brand | `{"campaign_id": int, "amount": float}` | Deducts from brand wallet; adds to campaign budget. |
| `POST` | `/reclaim-budget` | Brand | `{"campaign_id": int, "amount": float}` | Returns unspent campaign budget back to brand wallet. |
| `POST` | `/distribute-to-creator` | Brand | Payout parameters | Manually triggers a milestone payment to a creator. |
| `POST` | `/creator-withdraw` | Creator| `{"amount": float, "payout_method": str}` | Initiates withdrawal; sets balance status to `pending`. |
| `POST`/`PUT`| `/creator/payout-details`| Creator| Bank details or UPI ID | Configures settlement accounts. |
| `GET` | `/creator/payout-details` | Creator| None | Retrieves settlement configurations. |
| `GET` | `/creator/withdrawals` | Creator| `status`, `limit`, `offset` | Lists withdrawal transaction history logs. |
| `GET` | `/creator/notifications/{id}` | Creator| `creator_id` (Path) | Retrieves user payout notification messages. |
| `DELETE`| `/creator/notifications/{notification_id}` | Creator| `notification_id` (Path) | Deletes/dismisses a specific creator notification by ID. |
| `GET` | `/transactions/{type}/{id}`| Both | `user_type`, `user_id` | Returns financial audit ledger lines. |
| `POST` | `/creator/revert-withdrawal`| Creator| `{"transaction_id": int}` | Reverts failed withdrawal; restores creator wallet balance. |
| `POST` | `/refund-campaign` | Brand | `{"campaign_id": int}` | Returns all unused campaign allocations to the brand wallet. |
| `GET` | `/campaign-summary/{id}` | Both | `campaign_id` (Path) | Returns overall budget utilization metrics. |
| `GET` | `/calculate-earnings/{c_id}/{cr_id}`| Both | `campaign_id`, `creator_id` | Computes payout earnings based on clip views. |
| `POST` | `/bulk-distribute` | Brand | `{"distributions": [...]}` | Distributes milestones across multiple creators at once. |
| `POST` | `/request-refund` | Brand | Refund amounts | Creates a pending `RefundAudit` for admin approval. |
| `GET` | `/refund-requests` | Brand | None | Retrieves refund status requests history. |
| `POST` | `/admin/approve-refund` | Admin | `{"refund_id": int}` | Approves refund; updates brand wallet balance. |
| `POST` | `/admin/reject-refund` | Admin | `{"refund_id": int}` | Rejects refund request; registers administrative rejection reasons. |
| `GET` | `/refund-status/{id}` | Brand/Admin| `refund_id` (Path) | Detailed audit timeline and transaction linkages. |
| `GET` | `/admin/refund-audit-trail` | Admin | `brand_id`, `status` | Global admin ledger monitoring. |

---

### 6. Admin Router (`/`)
- **File**: [backend/api/routers/admin.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/routers/admin.py)
- **Role Requirement**: `admin`.

| Method | Endpoint | Payload / Path | Response / Actions |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/admin/campaigns` | None | Returns all campaigns alongside their submitted/accepted clips. |
| `PUT` | `/api/admin/clip/{clip_id}` | `UpdateClipStatusRequest` | Modifies status. `accepted` creates `AcceptedClip` row, removes `SubmittedClip`. |
| `DELETE`| `/api/admin/clip/{clip_id}` | `clip_id` (Path) | Deletes clip from records. |
| `PUT` | `/api/admin/clip/{clip_id}/view-count`| `UpdateViewCountRequest` | Manually updates clip view count; updates campaign totals. |
| `PUT` | `/api/admin/campaign/{id}/update-views`| `{"total_view_count": int}` | Manually overrides total campaign view count. |
| `GET` | `/api/admin/analytics/campaign-performance/{id}`| `campaign_id` (Path) | Returns detailed performance and utilization statistics. |
| `GET` | `/api/admin/brands/onboarding` | None | Lists all onboarding applications (pending, verified, rejected) with decrypted/masked PAN. |
| `POST`| `/api/admin/brands/{brand_id}/verify`| `brand_id` (Path), `VerifyBrandActionRequest` | Accepts or rejects the brand's onboarding application. |

---

### 7. System Router (`/`)
- **File**: [backend/api/routers/system.py](file:///d:/Mipoe/Mipoe-Backend/backend/api/routers/system.py)

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Public API health verification endpoint; returns `{"status": "ok"}`. |

---

## 📦 Pydantic Schema Declarations

Defined in `schemas/auth.py` and `schemas/common.py`. Key validators include:

- **`RegisterRequest`**: Validates credentials. Enforces `password` length (6-128 characters) and restricts `role` to `["brand", "creator", "admin"]`.
- **`CreateCampaignRequest`**: Validates platforms, budget limits, CPV rates, deadlines, and categories (`["fashion_clothing", "beauty_products", "youtube"]`).
- **`SubmitClipRequest`**: Enforces a valid `HttpUrl` for submitted reel links.
- **`UpdateViewCountRequest`**: Guarantees view counts are non-negative (`ge=0`).
- **`VerifyPanRequest`**: Enforces `pan_number` (10 chars), non-empty `pan_holder_name` and `business_address`, and `consent_given` check.
- **`SubmitBrandProfileRequest`**: Optional inputs for URLs (logo, banner, socials) and category string enum validation.
- **`VerifyBrandActionRequest`**: Restricts action field to `"approve"` or `"reject"` string literals, with an optional string `reason`.
