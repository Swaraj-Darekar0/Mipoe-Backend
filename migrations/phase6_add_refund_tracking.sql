-- Phase 6: Comprehensive Refund Flow
-- Purpose: Track mid-campaign refunds, partial returns, audit trail, and refund status
-- Migration Date: 2024

-- Create refund_audits table for comprehensive refund tracking
CREATE TABLE IF NOT EXISTS refund_audits (
    id SERIAL PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES brand(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    refund_type VARCHAR(50) NOT NULL, -- 'mid_campaign', 'partial_return', 'campaign_deletion', 'dispute_resolution'
    requested_amount DECIMAL(10, 2) NOT NULL, -- Amount brand is requesting to refund
    allocated_amount DECIMAL(10, 2) NOT NULL, -- Amount originally allocated to campaign
    distributed_amount DECIMAL(10, 2) NOT NULL, -- Amount already paid to creators
    refundable_amount DECIMAL(10, 2) NOT NULL, -- allocated - distributed (what can actually be refunded)
    approved_amount DECIMAL(10, 2), -- Amount actually approved (may differ from requested)
    status VARCHAR(20) DEFAULT 'pending', -- pending, approved, rejected, completed, failed
    reason VARCHAR(500) NOT NULL, -- Why the refund is requested
    rejection_reason VARCHAR(500), -- Why the refund was rejected (if applicable)
    processed_by_admin_id INTEGER REFERENCES admin(id) ON DELETE SET NULL, -- Admin who approved/rejected
    external_txn_id VARCHAR(255), -- Reference ID if manual payout/transfer
    metadata JSONB DEFAULT '{}', -- Flexible field for additional info
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP -- When refund was actually processed
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_refund_audits_brand_id ON refund_audits(brand_id);
CREATE INDEX IF NOT EXISTS idx_refund_audits_campaign_id ON refund_audits(campaign_id);
CREATE INDEX IF NOT EXISTS idx_refund_audits_status ON refund_audits(status);
CREATE INDEX IF NOT EXISTS idx_refund_audits_created_at ON refund_audits(created_at DESC);

-- Add refund tracking columns to transactions table (if not already present)
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS refund_audit_id INTEGER REFERENCES refund_audits(id) ON DELETE SET NULL;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS refund_reason VARCHAR(500);

-- Add index for refund tracking in transactions
CREATE INDEX IF NOT EXISTS idx_transactions_refund_audit_id ON transactions(refund_audit_id);

-- Create view for refund summary per campaign
CREATE OR REPLACE VIEW refund_summary_by_campaign AS
SELECT 
    c.id as campaign_id,
    c.name as campaign_name,
    b.id as brand_id,
    COUNT(ra.id) FILTER (WHERE ra.status = 'completed') as total_refunds_completed,
    COALESCE(SUM(ra.approved_amount) FILTER (WHERE ra.status = 'completed'), 0) as total_refunded_amount,
    COUNT(ra.id) FILTER (WHERE ra.status = 'pending') as pending_refund_requests,
    COALESCE(SUM(ra.requested_amount) FILTER (WHERE ra.status = 'pending'), 0) as pending_refund_total
FROM campaign c
JOIN brand b ON c.brand_id = b.id
LEFT JOIN refund_audits ra ON c.id = ra.campaign_id
GROUP BY c.id, c.name, b.id;

-- Verify tables and columns were created
SELECT table_name FROM information_schema.tables 
WHERE table_name IN ('refund_audits', 'transactions') 
AND table_schema = 'public';

SELECT column_name FROM information_schema.columns 
WHERE table_name = 'refund_audits' 
AND table_schema = 'public'
ORDER BY ordinal_position;
