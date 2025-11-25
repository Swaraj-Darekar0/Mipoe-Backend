import os
import re
import time
from datetime import datetime
from instagrapi import Client
from instagrapi.exceptions import ClientError
from pydantic import ValidationError
from dotenv import load_dotenv
from supabase import create_client, Client as SupabaseClient
from typing import List, Dict, Any, Optional

load_dotenv()

# Instagram credentials from environment variables
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not all([SUPABASE_URL, supabase_key]):
    raise ValueError("Missing Supabase configuration. Please set SUPABASE_URL and SUPABASE_KEY environment variables.")

# Initialize Supabase client
try:
    supabase: SupabaseClient = create_client(SUPABASE_URL, supabase_key)
except Exception as e:
    print(f"Error initializing Supabase client: {e}")
    raise

def get_accepted_clips_with_url() -> List[Dict[str, Any]]:
    """Fetch all accepted clips that have a clip_url from Supabase."""
    try:
        response = supabase.table('accepted_clips') \
            .select('id, clip_url, campaign_id') \
            .not_.is_('clip_url', 'null') \
            .execute()
        return response.data if response.data else []
    except Exception as e:
        print(f"Error fetching accepted clips: {e}")
        return []

def extract_media_id_from_url(url: str) -> Optional[str]:
    """Extracts the shortcode (media_id) from an Instagram URL."""
    # This regex is designed to capture the shortcode from various Instagram URL formats.
    # It looks for a sequence of alphanumeric characters and hyphens after /p/ or /reel/.
    match = re.search(r'/(?:p|reel)/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    return None

def update_clip_metrics(clip_id: int, updates: Dict[str, Any]) -> bool:
    """Update clip metrics in Supabase."""
    try:
        supabase.table('accepted_clips') \
            .update(updates) \
            .eq('id', clip_id) \
            .execute()
        return True
    except Exception as e:
        print(f"Error updating clip {clip_id}: {e}")
        return False

def update_campaign_views(campaign_id: int) -> Optional[int]:
    """Update campaign's total_view_count in Supabase."""
    try:
        # Get sum of all view_counts for this campaign's accepted clips
        response = supabase.table('accepted_clips') \
            .select('view_count') \
            .eq('campaign_id', campaign_id) \
            .execute()
        
        # Calculate the sum in Python
        total_views = sum(clip.get('view_count', 0) for clip in response.data) if response.data else 0
        
        # Update the campaign's total_view_count
        supabase.table('campaign') \
            .update({'total_view_count': total_views}) \
            .eq('id', campaign_id) \
            .execute()
        return total_views
    except Exception as e:
        print(f"Error updating campaign {campaign_id} views: {e}")
        return None

def fetch_and_update_metrics():
    print("Starting Instagram metrics fetching process...")

    if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
        print("Instagram username or password not set in environment variables. Please set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD.")
        return

    cl = Client()
    try:
        # Attempt to load session from file to avoid re-login
        if os.path.exists("instagrapi.json"):
            cl.load_settings("instagrapi.json")
            print("Instagrapi session loaded from file.")
        
        cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
        cl.dump_settings("instagrapi.json")  # Save session for next time
        print("Successfully logged into Instagram.")

    except Exception as e:
        print(f"Error logging into Instagram: {e}")
        print("Please ensure your Instagram credentials are correct and there are no 2FA issues.")
        return

    # Fetch all accepted clips with clip URLs
    accepted_clips = get_accepted_clips_with_url()
    
    if not accepted_clips:
        print("No accepted clips with clip URLs found to fetch metrics for.")
        return

    print(f"Found {len(accepted_clips)} accepted clips. Fetching metrics...")
    processed_campaigns = set()

    for clip in accepted_clips:
        clip_url = clip.get('clip_url')
        if not clip_url:
            print(f"Skipping clip ID {clip.get('id')}: No clip_url found.")
            continue
        
        # Extract media_id from the URL
        media_id = extract_media_id_from_url(clip_url)
        if not media_id:
            print(f"Skipping clip ID {clip.get('id')}: Could not extract media_id from URL: {clip_url}")
            continue

        try:
            # Convert the alphanumeric media_id to its numeric primary key
            media_pk = cl.media_pk_from_code(media_id)
            media_info = cl.media_info(media_pk)
            
            # Prepare updates
            updates = {
                'view_count': media_info.play_count,
                'media_id': media_id,
                'instagram_posted_at': media_info.taken_at.isoformat() if media_info.taken_at else None,
                'caption': media_info.caption_text
            }

            # Update clip metrics in Supabase
            if update_clip_metrics(clip['id'], updates):
                print(f"Updated metrics for clip ID {clip['id']} (Media ID: {media_id}). "
                      f"View Count: {media_info.play_count}, "
                      f"Caption: {(media_info.caption_text or '')[:50]}..., "
                      f"Posted At: {media_info.taken_at}")
                
                # Track campaigns that need view count updates
                if clip.get('campaign_id'):
                    processed_campaigns.add(clip['campaign_id'])
            
            time.sleep(2)  # Be polite to the API, avoid hitting rate limits
        
        except ValidationError as e:
            print(f"Error fetching metrics for clip ID {clip.get('id')} (Media ID: {media_id}): Data validation error - {e}")
            print(f"Skipping clip {clip.get('id')}. The returned data from Instagram is not as expected.")
            continue
        
        except ClientError as e:
            print(f"Error fetching metrics for clip ID {clip.get('id')} (Media ID: {media_id}): Client error - {e}")
            print(f"Skipping clip {clip.get('id')}.")
            continue

        except Exception as e:
            print(f"Error fetching metrics for clip ID {clip.get('id')} (Media ID: {media_id}): Unexpected error - {e}")
            continue
    
    # Update view counts for all affected campaigns
    for campaign_id in processed_campaigns:
        total_views = update_campaign_views(campaign_id)
        if total_views is not None:
            print(f"Updated campaign {campaign_id} total views to {total_views}")

    print("Instagram metrics fetching process completed.")

if __name__ == "__main__":
    fetch_and_update_metrics()