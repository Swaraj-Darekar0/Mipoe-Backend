-- Phase 11: Create RPC function to get creator notifications
-- This function is created to more robustly fetch and handle the JSONB[] notifications array from the creator table,
-- avoiding potential deserialization issues in the Python client.

CREATE OR REPLACE FUNCTION public.get_creator_notifications(p_creator_id INT)
RETURNS JSONB[] AS $$
DECLARE
    notifications_array JSONB[];
BEGIN
    -- Select the notification array directly into a variable
    SELECT notifications INTO notifications_array
    FROM public.creator
    WHERE id = p_creator_id;

    -- If the notifications column was NULL, return an empty JSONB array instead of NULL.
    RETURN COALESCE(notifications_array, '{}'::JSONB[]);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Grant permissions for this function to be called by authenticated users (i.e., your backend).
GRANT EXECUTE ON FUNCTION public.get_creator_notifications(INT) TO authenticated;
