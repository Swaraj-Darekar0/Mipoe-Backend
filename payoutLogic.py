# payout_logic.py
import os
from supabase import create_client
from datetime import datetime
import math
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()
# Initialize Supabase (Ensure env vars are loaded)
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def run_hourly_payouts():
    print(f"[{datetime.now()}] Starting Hourly Payout Calculation...")
    
    # 1. Fetch Active Campaigns & Their Clips
    # We only care about clips in ACTIVE campaigns where funds > 0
    # (Simplified query for demo - in production you might join tables)
    try:
        # Get active campaigns first
        # We only need funds_allocated now, not funds_distributed
        active_campaigns = supabase.table('campaign').select('id, name, cpv, view_threshold, funds_allocated, brand_id').eq('is_active', True).gt('funds_allocated', 0).execute()
        
        if not active_campaigns.data:
            print("No active campaigns with funds found.")
            return

        for campaign in active_campaigns.data:
            campaign_id = campaign['id']
            campaign_name = campaign['name'] # Added: Campaign name for notifications
            cpv = campaign['cpv']
            threshold = campaign['view_threshold']
            # funds_allocated is now the single source of truth for the available budget
            funds_available_in_campaign = campaign['funds_allocated']
            
            # Fetch accepted clips for this campaign
            clips = supabase.table('accepted_clips').select('*').eq('campaign_id', campaign_id).execute()
            
            for clip in clips.data:
                clip_id = clip['id']
                creator_id = clip['creator_id']
                old_view_count = clip['view_count'] or 0
                amount_paid_so_far = clip['amount_paid'] or 0.0
                
                # 2. FETCH LIVE METRICS (Mocking Hiker API for now)
                current_views = old_view_count + 0 # MOCK: Simulating growth
                
                # 3. THE CUMULATIVE MILESTONE MATH
                total_milestones = math.floor(current_views / threshold)
                
                if total_milestones <= 0:
                    continue

                total_earnings_should_be = total_milestones * cpv
                amount_due_now = total_earnings_should_be - amount_paid_so_far
                
                # 4. SAFETY CHECKS
                if amount_due_now <= 0:
                    continue # Already paid up to date
                
                # Check if funds_available_in_campaign is sufficient for the payout
                if funds_available_in_campaign < amount_due_now:
                    print(f"⚠️ Campaign {campaign_id} has insufficient funds (₹{funds_available_in_campaign}) for payout of ₹{amount_due_now}!")
                    # Skip this payout and continue to the next clip
                    continue

                # 5. EXECUTE LEDGER TRANSACTION
                creator_share = amount_due_now * 0.90
                platform_share = amount_due_now * 0.10
                
                print(f"💰 Paying Clip {clip_id}: ₹{creator_share} (Platform: ₹{platform_share})")

                # A. Update Campaign (Decrement funds_allocated)
                new_funds_allocated = funds_available_in_campaign - amount_due_now
                supabase.table('campaign').update({
                    'funds_allocated': new_funds_allocated
                }).eq('id', campaign_id).execute()
                
                # B. Update Creator Wallet (Credit 90%)
                supabase.rpc('increment_creator_wallet', {'p_user_id': creator_id, 'p_amount': creator_share}).execute()
                
                # C. Update Platform Wallet (Credit 10%)
                supabase.rpc('increment_platform_wallet', {'p_amount': platform_share}).execute()
                
                # D. Update Clip Record (Sync views and paid amount)
                supabase.table('accepted_clips').update({
                    'view_count': current_views,
                    'last_view_count': old_view_count, # History
                    'amount_paid': total_earnings_should_be
                }).eq('id', clip_id).execute()

                # E. Log Creator Earning Transaction
                creator_txn = {
                    'creator_id': creator_id,
                    'campaign_id': campaign_id,
                    'amount': creator_share,
                    'type': 'earning',
                    'status': 'success',
                    'description': f'Earned ₹{creator_share:.2f} from {current_views} views on campaign {campaign_id} (Clip {clip_id})'
                }
                supabase.table('creator_transactions').insert([creator_txn]).execute()

                # --- NOTIFICATION FOR CREATOR EARNING ---
                notification_message = f"You earned ₹{creator_share:.2f} from your clip ({clip_id}) in campaign '{campaign_name}'."
                notification_data = {
                    "message": notification_message,
                    "type": "earning_payout",
                    "campaign_id": campaign_id,
                    "clip_id": clip_id,
                    "amount": creator_share,
                    "timestamp": datetime.utcnow().isoformat()
                }
                supabase.rpc('append_notification', {'p_creator_id': creator_id, 'p_new_notification': notification_data}).execute()


                # F. Log Platform Commission Transaction
                commission_txn = {
                    'source_brand_id': campaign['brand_id'],
                    'campaign_id': campaign_id,
                    'amount': platform_share,
                    'type': 'commission',
                    'status': 'success',
                    'description': f'Platform commission (10%) from creator earnings on campaign {campaign_id} (Clip {clip_id})'
                }
                supabase.table('platform_transactions').insert([commission_txn]).execute()
                
                # Update local variable for the next clip in the same campaign
                funds_available_in_campaign = new_funds_allocated


    except Exception as e:
        print(f"❌ Error in payout logic: {e}")

if __name__ == "__main__":
    run_hourly_payouts()
