-- Phase 1: Add Budget Tracking & Fund Distribution Columns
-- Purpose: Track cumulative budget allocation and amount distributed to creators

-- 1. Add funds_distributed to campaign table
ALTER TABLE public.campaign 
ADD COLUMN IF NOT EXISTS funds_distributed double precision DEFAULT 0.0;

-- 2. Ensure transactions table has campaign_id (for tracking which campaign the transaction relates to)
ALTER TABLE public.transactions
ADD COLUMN IF NOT EXISTS campaign_id integer,
ADD COLUMN IF NOT EXISTS external_txn_id text,
ADD CONSTRAINT fk_transactions_campaign FOREIGN KEY (campaign_id) 
  REFERENCES campaign(id) ON DELETE SET NULL;

-- 3. Create or verify transaction type enum
DO $$ BEGIN
    CREATE TYPE transaction_type AS ENUM ('deposit', 'allocation', 'reclaim', 'earning', 'payout', 'commission');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- 4. Create or verify transaction status enum
DO $$ BEGIN
    CREATE TYPE transaction_status AS ENUM ('pending', 'success', 'failed');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- 5. Add indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_campaign_funds_allocated ON public.campaign(id, funds_allocated);
CREATE INDEX IF NOT EXISTS idx_campaign_funds_distributed ON public.campaign(id, funds_distributed);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON public.transactions(user_type, user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_campaign ON public.transactions(campaign_id);
CREATE INDEX IF NOT EXISTS idx_creator_wallet ON public.creator(id, wallet_balance);
CREATE INDEX IF NOT EXISTS idx_brand_wallet ON public.brand(id, wallet_balance);
CREATE INDEX IF NOT EXISTS idx_accepted_clips_amount_paid ON public.accepted_clips(campaign_id, amount_paid);

-- 6. Add check constraint to ensure funds_distributed <= funds_allocated
ALTER TABLE public.campaign
ADD CONSTRAINT check_funds_distributed 
  CHECK (funds_distributed <= funds_allocated);

-- 7. Verify creator and brand have wallet_balance (they do, but let's ensure defaults are correct)
-- This is just documentation - columns already exist
-- creator.wallet_balance: double precision, default 0.0
-- brand.wallet_balance: double precision, default 0.0
