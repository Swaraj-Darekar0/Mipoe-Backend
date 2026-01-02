-- Phase 10: Add Wallet Increment Functions
-- This migration creates PL/pgSQL functions for safely incrementing
-- creator and platform wallet balances, preventing race conditions.

-- Function to increment a creator's wallet balance
CREATE OR REPLACE FUNCTION public.increment_creator_wallet(p_user_id integer, p_amount double precision)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE public.creator
  SET wallet_balance = wallet_balance + p_amount
  WHERE id = p_user_id;
END;
$$;

-- Function to increment the platform's wallet balance
CREATE OR REPLACE FUNCTION public.increment_platform_wallet(p_amount double precision)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  -- Assuming the platform wallet always has an ID of 1
  UPDATE public.platform_wallet
  SET balance = balance + p_amount
  WHERE id = 1;
END;
$$;
