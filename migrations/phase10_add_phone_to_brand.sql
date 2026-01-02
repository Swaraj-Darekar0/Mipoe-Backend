-- Phase 10: Add phone column to brand table
-- This migration adds a 'phone' column to the 'public.brand' table.
-- This column is required for Cashfree payment gateway integrations,
-- specifically for creating deposit orders and virtual accounts.

ALTER TABLE public.brand
ADD COLUMN phone VARCHAR(20) NULL;
