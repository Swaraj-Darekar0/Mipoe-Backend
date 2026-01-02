-- Phase 12: Add 'distribution' to brand_transaction_type ENUM
-- This migration adds 'distribution' as a valid type to the brand_transaction_type ENUM,
-- allowing the brand_transaction_view to correctly classify creator earnings from the
-- brand's perspective as distributions.

ALTER TYPE public.brand_transaction_type ADD VALUE 'distribution' AFTER 'refund';
