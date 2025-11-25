import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

# 1. Load Environment Variables
load_dotenv()

# 2. Initialize Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: Supabase credentials not found in .env")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def deactivate_expired_campaigns():
    """
    Task 1: Campaign Cleanup
    Finds active campaigns past their deadline and sets them to inactive.
    """
    print(f"[{datetime.now()}] Task 1: Checking for expired campaigns...")
    
    try:
        today = datetime.utcnow().date().isoformat()
        
        # Update is_active=False WHERE deadline < today AND is_active=True
        response = supabase.table('campaign') \
            .update({'is_active': False}) \
            .lt('deadline', today) \
            .eq('is_active', True) \
            .execute()
            
        if response.data:
            count = len(response.data)
            print(f"   -> Success: Deactivated {count} expired campaigns.")
        else:
            print(f"   -> No expired campaigns found.")
            
    except Exception as e:
        print(f"   -> ERROR in Campaign Cleanup: {str(e)}")

def delete_rejected_clips():
    """
    Task 2: Rejected Clip Cleanup
    Finds any clip marked as 'is_deleted_by_admin' (Rejected) and permanently deletes the row.
    Since we only store URLs, no storage bucket cleanup is required.
    """
    print(f"[{datetime.now()}] Task 2: Cleaning up rejected clips...")

    try:
        # DELETE FROM submitted_clips WHERE is_deleted_by_admin = True
        response = supabase.table('submitted_clips') \
            .delete() \
            .eq('is_deleted_by_admin', True) \
            .execute()

        if response.data:
            count = len(response.data)
            print(f"   -> Success: Permanently deleted {count} rejected clips.")
        else:
            print(f"   -> No rejected clips found to clean up.")

    except Exception as e:
        print(f"   -> ERROR in Clip Cleanup: {str(e)}")

def run_scheduler():
    print(f"--- Scheduler Started at {datetime.now()} ---")
    
    # Run Task 1
    deactivate_expired_campaigns()
    
    # Run Task 2
    delete_rejected_clips()
    
    print(f"--- Scheduler Finished at {datetime.now()} ---")

if __name__ == "__main__":
    run_scheduler()