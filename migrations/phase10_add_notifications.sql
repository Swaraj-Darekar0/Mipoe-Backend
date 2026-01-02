-- Phase 10: Add Notifications column and RPC functions to Creator table

-- Step 1: Add 'notifications' JSONB[] column to the 'creator' table.
-- This column will store an array of JSON objects, each representing a notification.
-- Defaulting to an empty JSONB array ensures we always have an array to append to.
ALTER TABLE public.creator
ADD COLUMN IF NOT EXISTS notifications JSONB[] DEFAULT '{}';

-- Step 2: Create the 'append_notification' RPC function.
-- This function atomically appends a new JSONB notification object to a creator's notifications array.
-- It handles cases where the array might be NULL or empty by coalescing to an empty array first.
CREATE OR REPLACE FUNCTION public.append_notification(p_creator_id INT, p_new_notification JSONB)
RETURNS VOID AS $$
BEGIN
    UPDATE public.creator
    SET notifications = COALESCE(notifications, '{}'::JSONB[]) || p_new_notification
    WHERE id = p_creator_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Step 3: Create the 'clear_creator_notifications' RPC function.
-- This function sets a specific creator's notifications array to an empty array.
-- It can be used for targeted cleanup.
CREATE OR REPLACE FUNCTION public.clear_creator_notifications(p_creator_id INT)
RETURNS VOID AS $$
BEGIN
    UPDATE public.creator
    SET notifications = '{}'::JSONB[]
    WHERE id = p_creator_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Step 4: Create 'clear_all_creator_notifications' RPC function.
-- This function clears notifications for all creators by setting their arrays to empty.
-- This is suitable for a daily scheduled cleanup job.
CREATE OR REPLACE FUNCTION public.clear_all_creator_notifications()
RETURNS VOID AS $$
BEGIN
    UPDATE public.creator
    SET notifications = '{}'::JSONB[];
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Step 5: Grant permissions for these functions to the 'authenticated' role.
-- This allows the Supabase client (e.g., your backend Flask app) to call these functions.
GRANT EXECUTE ON FUNCTION public.append_notification(INT, JSONB) TO authenticated;
GRANT EXECUTE ON FUNCTION public.clear_creator_notifications(INT) TO authenticated;
GRANT EXECUTE ON FUNCTION public.clear_all_creator_notifications() TO authenticated;
