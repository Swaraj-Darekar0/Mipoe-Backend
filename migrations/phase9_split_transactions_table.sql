-- Phase 9: Split Transactions Table
-- This migration splits the single 'transactions' table into three distinct tables:
-- 'brand_transactions', 'creator_transactions', and 'platform_transactions'.
-- It also creates new, specific ENUM types for each table.

-- Step 1: Create new ENUM types for the separate tables for better type safety.
CREATE TYPE brand_transaction_type AS ENUM ('deposit', 'allocation', 'reclaim', 'refund');
CREATE TYPE creator_transaction_type AS ENUM ('earning', 'withdrawal', 'bonus', 'penalty');
CREATE TYPE platform_transaction_type AS ENUM ('commission', 'fee');
-- NOTE: We will continue using the existing 'transaction_status' ENUM for all tables.

-- Step 2: Create the new 'brand_transactions' table.
CREATE TABLE public.brand_transactions (
    id SERIAL PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES public.brand(id) ON DELETE CASCADE,
    campaign_id INTEGER NULL REFERENCES public.campaign(id) ON DELETE SET NULL,
    type brand_transaction_type NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    status public.transaction_status NOT NULL DEFAULT 'pending',
    description TEXT NULL,
    external_txn_id TEXT NULL,
    refund_audit_id INTEGER NULL REFERENCES public.refund_audits(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (now() at time zone 'utc')
);
CREATE INDEX ON public.brand_transactions (brand_id);
CREATE INDEX ON public.brand_transactions (campaign_id);
CREATE INDEX ON public.brand_transactions (type);

-- Step 3: Create the new 'creator_transactions' table.
CREATE TABLE public.creator_transactions (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES public.creator(id) ON DELETE CASCADE,
    campaign_id INTEGER NULL REFERENCES public.campaign(id) ON DELETE SET NULL,
    type creator_transaction_type NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    status public.transaction_status NOT NULL DEFAULT 'pending',
    description TEXT NULL,
    external_txn_id TEXT NULL,
    payout_method TEXT NULL, -- Added column for withdrawal method (e.g., 'upi', 'bank')
    utr TEXT NULL,            -- Added column for Unique Transaction Reference
    created_at TIMESTAMPTZ NOT NULL DEFAULT (now() at time zone 'utc')
);
CREATE INDEX ON public.creator_transactions (creator_id);
CREATE INDEX ON public.creator_transactions (campaign_id);
CREATE INDEX ON public.creator_transactions (type);

-- Step 4: Create the new 'platform_transactions' table.
CREATE TABLE public.platform_transactions (
    id SERIAL PRIMARY KEY,
    source_brand_id INTEGER NULL REFERENCES public.brand(id) ON DELETE SET NULL,
    source_creator_id INTEGER NULL REFERENCES public.creator(id) ON DELETE SET NULL,
    campaign_id INTEGER NULL REFERENCES public.campaign(id) ON DELETE SET NULL,
    type platform_transaction_type NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    status public.transaction_status NOT NULL DEFAULT 'pending',
    description TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (now() at time zone 'utc')
);
CREATE INDEX ON public.platform_transactions (campaign_id);
CREATE INDEX ON public.platform_transactions (type);

-- Step 5: Migrate data from the old 'transactions' table to the new tables.
-- IMPORTANT: Run these INSERT statements BEFORE dropping the old table.

-- Migrate Brand transactions
INSERT INTO public.brand_transactions (brand_id, campaign_id, type, amount, status, description, external_txn_id, refund_audit_id, created_at)
SELECT
    user_id,
    campaign_id,
    type::text::brand_transaction_type,
    amount,
    status,
    description,
    external_txn_id,
    refund_audit_id,
    created_at
FROM public.transactions
WHERE user_type = 'brand' AND type::text IN ('deposit', 'allocation', 'reclaim', 'refund');

-- Migrate Creator transactions
INSERT INTO public.creator_transactions (creator_id, campaign_id, type, amount, status, description, external_txn_id, created_at)
SELECT
    user_id,
    campaign_id,
    type::text::creator_transaction_type,
    amount,
    status,
    description,
    external_txn_id,
    created_at
FROM public.transactions
WHERE user_type = 'creator' AND type::text IN ('earning', 'withdrawal'); -- Note: bonus/penalty are not yet implemented, so not migrated.

-- Migrate Platform transactions (commissions are logged against the brand)
INSERT INTO public.platform_transactions (source_brand_id, campaign_id, type, amount, status, description, created_at)
SELECT
    user_id,
    campaign_id,
    type::text::platform_transaction_type,
    amount,
    status,
    description,
    created_at
FROM public.transactions
WHERE user_type = 'brand' AND type::text = 'commission';

-- Step 6: Drop the old 'transactions' table after data has been migrated.
DROP TABLE public.transactions;

-- Step 7: Drop the old 'transaction_type' ENUM as it has been replaced by more specific ENUMs.
DROP TYPE public.transaction_type;
