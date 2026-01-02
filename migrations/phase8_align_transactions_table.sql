-- Migration to align transactions table with the new architecture plan (v2)

-- Step 1: Drop the deprecated razorpay_payment_id column from the transactions table.
-- This column is being removed as the project has standardized on Cashfree as the payment gateway,
-- and the 'external_txn_id' column will be used for storing gateway reference IDs.
ALTER TABLE public.transactions
DROP COLUMN IF EXISTS razorpay_payment_id;

-- Step 2: Add a comment to the external_txn_id column to clarify its purpose.
-- This makes the schema easier to understand for future development.
COMMENT ON COLUMN public.transactions.external_txn_id IS 'Stores the unique reference ID from the Cashfree payment gateway for deposits and withdrawals.';
