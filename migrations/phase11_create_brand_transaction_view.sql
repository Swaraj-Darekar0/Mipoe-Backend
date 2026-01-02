CREATE OR REPLACE VIEW public.brand_transaction_view AS

-- 1. Get all standard brand transactions
SELECT
    bt.brand_id,
    NULL::integer AS creator_id, -- No specific creator for these types
    bt.campaign_id,
    bt.created_at,
    bt.description,
    bt.type::text AS type, -- Cast to text for UNION consistency
    bt.amount,
    bt.status,
    bt.id, -- Include transaction ID for uniqueness
    NULL::text AS payout_method, -- Not applicable for brand_transactions directly
    NULL::text AS utr, -- Not applicable for brand_transactions directly
    bt.external_txn_id,
    NULL::text AS creator_username, -- Add placeholder for potential future use
    NULL::text AS campaign_name -- Will be joined in Python
FROM
    public.brand_transactions bt

UNION ALL

-- 2. Get all creator earnings, but show them as a 'distribution' from the brand's perspective
SELECT
    c.brand_id,
    ct.creator_id,
    ct.campaign_id,
    ct.created_at,
    ct.description,
    'distribution' AS type,
    -ct.amount AS amount, -- Show as a negative value for the brand (money flowing out)
    ct.status,
    ct.id, -- Include transaction ID for uniqueness
    ct.payout_method, -- Not applicable for earnings, but for consistent columns
    ct.utr, -- Not applicable for earnings, but for consistent columns
    ct.external_txn_id,
    NULL::text AS creator_username, -- Will be joined in Python
    NULL::text AS campaign_name -- Will be joined in Python
FROM
    public.creator_transactions ct
JOIN
    public.campaign c ON ct.campaign_id = c.id
WHERE
    ct.type = 'earning';
