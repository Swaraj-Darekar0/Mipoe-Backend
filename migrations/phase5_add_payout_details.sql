-- Phase 5: Add Payout Details to Creator Table
-- Purpose: Store creator's bank account and UPI information for withdrawals
-- Migration Date: 2024

ALTER TABLE creator ADD COLUMN IF NOT EXISTS payout_method VARCHAR(10) DEFAULT NULL;
-- 'upi' or 'bank' or NULL if not set

ALTER TABLE creator ADD COLUMN IF NOT EXISTS upi_id VARCHAR(255) DEFAULT NULL;
-- UPI ID format: username@bankname (e.g., user@okhdfcbank)

ALTER TABLE creator ADD COLUMN IF NOT EXISTS bank_account VARCHAR(20) DEFAULT NULL;
-- Bank account number (encrypted in production, validated for length ≥ 9)

ALTER TABLE creator ADD COLUMN IF NOT EXISTS ifsc VARCHAR(11) DEFAULT NULL;
-- IFSC code for bank transfers (standard format: 4 letters + 0 + 6 digits)

ALTER TABLE creator ADD COLUMN IF NOT EXISTS account_holder_name VARCHAR(255) DEFAULT NULL;
-- Account holder name for bank transfers

-- Create index on payout_method for quick filtering
CREATE INDEX IF NOT EXISTS idx_creator_payout_method ON creator(payout_method);

-- Verify columns were added
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'creator' 
AND column_name IN ('payout_method', 'upi_id', 'bank_account', 'ifsc', 'account_holder_name')
ORDER BY ordinal_position;
