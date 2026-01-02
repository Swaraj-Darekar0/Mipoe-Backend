-- Phase 13: Create Detailed Brand Transaction View
-- This view builds upon the 'brand_transaction_view' to pre-join campaign and creator names,
-- which simplifies the backend query logic and resolves issues with Supabase's library
-- not being able to perform joins on other views.

CREATE OR REPLACE VIEW public.brand_transaction_details_view AS
SELECT
    b_view.brand_id,
    b_view.creator_id,
    b_view.campaign_id,
    b_view.created_at,
    b_view.description,
    b_view.type,
    b_view.amount,
    b_view.status,
    b_view.id,
    b_view.external_txn_id,
    -- Pre-join the campaign name using a LEFT JOIN
    COALESCE(c.name, 'N/A') AS campaign_name,
    -- Pre-join the creator username using a LEFT JOIN
    COALESCE(cr.username, 'N/A') AS creator_username
FROM 
    public.brand_transaction_view b_view
LEFT JOIN 
    public.campaign c ON b_view.campaign_id = c.id
LEFT JOIN
    public.creator cr ON b_view.creator_id = cr.id;
