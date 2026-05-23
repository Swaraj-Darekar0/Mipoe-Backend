import time
from dotenv import load_dotenv
load_dotenv()
import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, create_refresh_token, jwt_required, get_jwt_identity, get_jwt, verify_jwt_in_request
from models import  Brand, Creator, Campaign, SubmittedClip, AcceptedClip, Admin
from config import Config
from datetime import datetime, timedelta
from urllib.parse import urlencode
from supabase_auth.errors import AuthApiError
import requests
from utils import encrypt_token, decrypt_token
import re
import random
import string
from supabase import create_client, Client
from urllib.parse import unquote 
from routes.payments import payments_bp
from routes.instagramVerifier import verify_instagram_username
from postgrest.exceptions import APIError






app = Flask(__name__)
app.config.from_object(Config)
# Debugging: Print environment variables to confirm they are loaded
# print(f"Loaded Secret Key starts with: {app.config['JWT_SECRET_KEY'][:5]}...")

# Initialize Supabase client with connection pooling
# The connection pool size is configured via SUPABASE_POOL_SIZE in config.py (default 10)
supabase: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

# Enable CORS for all routes and allow all headers
CORS(app, resources={r"/*": {"origins": "*"}})
jwt = JWTManager(app)

# This in-memory set will store the JTI of revoked tokens.
# For production, use a persistent store like Redis or a database.
blocklist = set()


def get_profile_table(role):
    table_map = {
        'brand': 'brand',
        'creator': 'creator',
        'admin': 'admin'
    }
    return table_map.get(role)


def get_profile_by_email(role, email):
    table_name = get_profile_table(role)
    if not table_name:
        raise ValueError(f"Unsupported role: {role}")

    select_fields = 'id, username'
    if role == 'creator':
        select_fields += ', profile_completed'

    response = supabase.table(table_name).select(select_fields).eq('email', email).limit(1).execute()
    return response.data[0] if response.data else None


def ensure_profile_exists(role, email, username):
    table_name = get_profile_table(role)
    if not table_name:
        raise ValueError(f"Unsupported role: {role}")

    existing_profile = get_profile_by_email(role, email)
    if existing_profile:
        return existing_profile

    new_profile = {
        'email': email,
        'username': username,
        'password_hash': 'supabase_auth_managed'
    }

    if role == 'creator':
        new_profile['join_date'] = datetime.utcnow().strftime('%Y-%m-%d')
        new_profile['profile_completed'] = False

    try:
        insert_response = supabase.table(table_name).insert([new_profile]).execute()
        if not insert_response.data:
            raise ValueError(f"Failed to create {role} profile")
        return insert_response.data[0]
    except APIError as api_error:
        if getattr(api_error, 'code', None) == '23505':
            profile = get_profile_by_email(role, email)
            if profile:
                return profile
        raise


def get_current_user_id():
    return int(get_jwt_identity())

@jwt.token_in_blocklist_loader
def check_if_token_in_blocklist(jwt_header, jwt_payload: dict):
    """
    This function is called for every protected endpoint.
    It checks if the token's JTI (unique identifier) is in the blocklist.
    """
    jti = jwt_payload["jti"]
    return jti in blocklist

# Add explicit error handlers to help debug JWT related issues
@jwt.invalid_token_loader
def invalid_token_callback(reason):
    """This will be invoked when an invalid JWT is received (causes 422)."""
    # Log the exact reason on the server console for easier debugging
    print(f"[JWT] Invalid token: {reason}")
    return jsonify({
        'msg': 'Invalid token',
        'error': reason
    }), 422

@jwt.unauthorized_loader
def missing_token_callback(reason):
    """This will be invoked when no JWT is present in a protected endpoint (causes 401)."""
    print(f"[JWT] Missing/Unauthorized token: {reason}")
    return jsonify({
        'msg': 'Missing authorization header',
        'error': reason
    }), 401

@app.route('/register', methods=['POST'])
def register():
    try:
        data = request.json
        # Validate input
        if not all(k in data for k in ['username', 'email', 'password', 'role']):
            return jsonify({'msg': 'Missing required fields'}), 400

        email = data['email']
        username = data['username']
        password = data['password']
        role = data['role']

        if role not in ['brand', 'creator', 'admin']:
             return jsonify({'msg': 'Invalid role'}), 400

        # 1. Sign up with Supabase Auth
        try:
            auth_response = supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "username": username,
                        "role": role 
                    }
                }
            })
            
            if auth_response.user:
                profile = ensure_profile_exists(role, email, username)
                return jsonify({
                    'msg': 'User registered successfully',
                    'user_id': str(profile['id']),
                    'auth_user_id': auth_response.user.id
                }), 201

                
        except AuthApiError as auth_err:
            # Handle "User already exists" specifically
            print(f"⚠️ REGISTER ERROR: {str(auth_err)}")
            return jsonify({'msg': 'Registration failed', 'error': str(auth_err)}), 400

    except Exception as e:
        print(f"Registration error: {str(e)}")
        return jsonify({'msg': 'Registration failed', 'error': str(e)}), 500

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        if not all(k in data for k in ['email', 'password']):
            return jsonify({'msg': 'Missing email or password'}), 400

        email = data['email']
        password = data['password']

        # --- RETRY LOGIC START ---
        # We try up to 3 times to handle "Server disconnected" errors
        auth_response = None
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                auth_response = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })
                # If we get here, it worked! Break the loop.
                break 
            except Exception as e:
                error_msg = str(e).lower()
                # Only retry if it's a network/connection error
                if "disconnected" in error_msg or "timeout" in error_msg or "connection" in error_msg:
                    if attempt < max_retries - 1:
                        print(f"⚠️ [Login Retry] Connection dropped. Retrying ({attempt+1}/{max_retries})...")
                        time.sleep(1) # Wait 1 second before retrying
                        continue
                
                # If it's a real error (like wrong password), raise it immediately
                raise e
        # --- RETRY LOGIC END ---

        # 2. Extract User Info from Supabase
        user = auth_response.user
        auth_user_id = user.id
        
        # 3. Retrieve role/username and resolve the matching public profile row
        role = user.user_metadata.get('role') or data.get('role')
        username = user.user_metadata.get('username') or email.split('@')[0]

        if role not in ['brand', 'creator', 'admin']:
            return jsonify({'msg': 'Invalid or missing role on user account'}), 400

        profile = ensure_profile_exists(role, email, username)
        internal_user_id = profile['id']
        resolved_username = profile.get('username') or username

        # 4. Create tokens with flask-jwt-extended
        additional_claims = {
            "role": role,
            "username": resolved_username,
            "auth_user_id": auth_user_id
        }
        access_token = create_access_token(identity=str(internal_user_id), additional_claims=additional_claims)
        refresh_token = create_refresh_token(identity=str(internal_user_id), additional_claims=additional_claims)
        
        # 5. (Optional) Check Profile Completion for Creators
        profile_completed = bool(profile.get('profile_completed', False)) if role == 'creator' else False

        return jsonify({
            'access_token': access_token,
            'refresh_token': refresh_token,
            'role': role,
            'username': resolved_username,
            'user_id': str(internal_user_id),
            'auth_user_id': auth_user_id,
            'profile_completed': profile_completed
        }), 200

    except Exception as e:
        print(f"Login error: {str(e)}")
        # This will now only print real errors, or the final network error if all retries failed
        return jsonify({'msg': 'Login failed', 'error': str(e)}), 500

@app.route("/verify-instagram", methods=["POST"])
@app.route("/verify-instagram/", methods=["POST"])
@jwt_required()
def verify_instagram():
    creator_id = get_current_user_id()
    claims = get_jwt()
    if claims.get('role') != 'creator':
        return jsonify({'msg': 'Only creators can verify Instagram accounts'}), 403

    username = request.json.get("username")
    if not username:
        return jsonify({'msg': 'Instagram username is required'}), 400

    result = verify_instagram_username(username)

    if result.get("exists"):
        update_payload = {"instagram_username": username, "instagram_verified": True}
        try:
            supabase.table("creator").update(update_payload).eq("id", creator_id).execute()
            result['msg'] = 'Instagram account verified and linked.'
        except Exception as e:
            return jsonify({'msg': 'Failed to update profile', 'error': str(e)}), 500
    else:
        status = result.get("status")
        if status == "not_found":
            message = f"Instagram user '{username}' not found."
        elif status == "blocked":
            message = "Could not verify at this time. Please try again later."
        else:
            message = "An unknown error occurred during verification."
        return jsonify({'msg': message, 'status': status}), 400
            
    return jsonify(result)

@app.route('/request-password-reset', methods=['POST'])
def request_password_reset():
    try:
        data = request.json
        email = data.get('email')
        
        if not email:
            return jsonify({'msg': 'Email is required'}), 400

        # This sends a magic link to the user's email
        # The link will redirect them to your frontend reset page
        response = supabase.auth.reset_password_email(
            email, 
            options={"redirect_to": "http://localhost:8080/reset-password"} # Your frontend URL
        )
        
        return jsonify({'msg': 'Password reset email sent'}), 200

    except Exception as e:
        print(f"Reset password error: {str(e)}")
        return jsonify({'msg': 'Failed to send reset email', 'error': str(e)}), 500


@app.route('/api/brand/campaigns', methods=['POST'])
@jwt_required()
def create_campaign():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()
    try:
        data = request.json
        # Note: image_url is optional here so old validations don't break immediately
        required_fields = ['platform', 'budget', 'cpv', 'hashtag', 'audio', 'deadline', 'name', 'category']
        if not all(k in data for k in required_fields):
            return jsonify({'msg': 'Missing required fields'}), 400
        
        new_campaign = {
            'brand_id': brand_id,
            'platform': data['platform'],
            'budget': float(data['budget']),
            'cpv': float(data['cpv']),
            'hashtag': data['hashtag'],
            'audio': data['audio'],
            'deadline': data['deadline'], 
            'name': data['name'],
            'category': data['category'],
            'requirements': data.get('requirements'),
            'view_threshold': data.get('view_threshold', 0),
            'asset_link': data.get('asset_link'),
            'image_url': data.get('image_url'), # <--- Ensure this line is present
            'is_active': False,
            'total_view_count': 0 
        }
        response = supabase.table('campaign').insert([new_campaign]).execute()

        if response.data:
            campaign_id = response.data[0]['id']
            return jsonify({'msg': 'Campaign created successfully', 'campaign_id': campaign_id}), 201
        else:
            print(f"Supabase create campaign error: {response.status_code} - {response.count}")
            return jsonify({'msg': 'Failed to create campaign', 'error': response.count}), 500
    except Exception as e:
        print(f"Create campaign error: {str(e)}")
        return jsonify({'msg': 'Failed to create campaign', 'error': str(e)}), 500

@app.route('/api/brand/campaigns', methods=['GET'])
@jwt_required()
def list_campaigns():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()
    try:
        response = supabase.table('campaign').select('*').eq('brand_id', brand_id).execute()
        campaigns_data = response.data
        
        if campaigns_data:
            result = [{
                'id': c['id'],
                'name': c['name'],
                'platform': c['platform'],
                'budget': c['budget'],
                'cpv': c['cpv'],
                'hashtag': c['hashtag'],
                'audio': c['audio'],
                'deadline': c['deadline'], # Assuming Supabase returns YYYY-MM-DD
                'is_active': c['is_active'],
                'category': c['category'],
                'total_view_count': c['total_view_count'],
                'requirements': c['requirements'],
                'image_url': c['image_url'],
                'view_threshold': c['view_threshold'],
                'funds_allocated': c.get('funds_allocated', 0),
                'funds_distributed': c.get('funds_distributed', 100)  # ADD THIS LINE
            } for c in campaigns_data]
            return jsonify(result), 200
        else:
            return jsonify([]), 200 # Return empty array if no campaigns found
    except Exception as e:
        print(f"List campaigns error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch campaigns', 'error': str(e)}), 500

@app.route('/api/campaigns', methods=['GET'])
def get_all_campaigns():
    try:
        # OPTIMIZED: Let the database do the work.
        # Only ask for rows where is_active is TRUE.
        # If you have 10,000 campaigns but only 50 are active, 
        # this sends only 50 rows over the network.\

        response = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # OPTIMIZED: Only fetch active campaigns
                response = supabase.table('campaign')\
                    .select('*')\
                    .eq('is_active', True)\
                    .execute()
                break # Success!
            except Exception as e:
                if "disconnected" in str(e).lower() and attempt < max_retries - 1:
                    print(f"⚠️ [Campaigns Retry] Connection dropped. Retrying...")
                    time.sleep(0.5)
                    continue
                raise e
        # --- RETRY LOGIC END ---
        
            
        campaigns_data = response.data
        
        if campaigns_data:
            result = [
                {
                    'id': c['id'],
                    'name': c['name'],
                    'platform': c['platform'],
                    'budget': c['budget'],
                    'cpv': c['cpv'],
                    'hashtag': c['hashtag'],
                    'audio': c['audio'],
                    'deadline': c['deadline'],
                    'brand_id': c['brand_id'],
                    'is_active': c['is_active'],
                    'category': c.get('category'),  # Default to 'fashion_clothing' if not set
                    'asset_link': c.get('asset_link'),  # Optional field
                    'total_view_count': c['total_view_count'],
                    'requirements': c['requirements'],
                    'image_url': c['image_url'],
                    'view_threshold': c['view_threshold'],
                    'funds_distributed': c.get('funds_distributed', 100) # Add this line
                }
                for c in campaigns_data
            ]
            return jsonify(result), 200
        else:
            return jsonify([]), 200
    except Exception as e:
        print(f"Get all campaigns error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch campaigns', 'error': str(e)}), 500

@app.route('/api/campaigns/<int:campaign_id>', methods=['GET'])
def get_campaign_by_id(campaign_id):
    try:
        # Get campaign data
        response = supabase.table('campaign').select('*').eq('id', campaign_id).limit(1).execute()
        campaign_data = response.data[0] if response.data else None

        if not campaign_data:
            return jsonify({'msg': 'Campaign not found'}), 404
            
        # Get accepted clips for this campaign
        accepted_clips_response = supabase.table('accepted_clips').select('*').eq('campaign_id', campaign_id).execute()
        accepted_clips = []
        
        if accepted_clips_response.data:
            for clip in accepted_clips_response.data:
                # Get creator username
                creator_response = supabase.table('creator').select('username').eq('id', clip['creator_id']).limit(1).execute()
                creator_username = creator_response.data[0]['username'] if creator_response.data else 'Unknown Creator'
                
                accepted_clips.append({
                    'id': clip['id'],
                    'campaign_id': clip['campaign_id'],
                    'creator_id': clip['creator_id'],
                    'creator_name': creator_username,
                    'clip_url': clip['clip_url'],
                    'media_id': clip.get('media_id'),
                    'view_count': clip.get('view_count', 0),
                    'caption': clip.get('caption'),
                    'instagram_posted_at': clip.get('instagram_posted_at'),
                    'submitted_at': clip.get('submitted_at')
                })
        
        # Separate clips with None, 0, or duplicate view counts
        view_count_map = {}
        clips_to_sort = []
        clips_without_ranking = []
        
        # First pass: group clips by their view_count
        for clip in accepted_clips:
            view_count = clip.get('view_count')
            if view_count is None or view_count == 0:
                clips_without_ranking.append(clip)
                continue
                
            if view_count not in view_count_map:
                view_count_map[view_count] = []
            view_count_map[view_count].append(clip)
        
        # Second pass: add clips to either sorted list or without_ranking list
        for view_count, clips in view_count_map.items():
            if len(clips) == 1:  # Only one clip with this view_count
                clips_to_sort.append((view_count, clips[0]))
            else:  # Multiple clips with same view_count
                clips_without_ranking.extend(clips)
        
        # Sort the clips that have unique view counts
        accepted_clips_sorted = [clip for _, clip in sorted(clips_to_sort, key=lambda x: x[0], reverse=True)]
        
        # Combine the sorted clips with the unranked ones
        all_clips = accepted_clips_sorted + clips_without_ranking
        
        # Calculate creator rankings based on total views across all their clips in this campaign
        creator_rankings = {}
        for clip in accepted_clips_sorted:
            creator_id = clip['creator_id']
            if creator_id not in creator_rankings:
                creator_rankings[creator_id] = {
                    'creator_id': creator_id,
                    'creator_name': clip['creator_name'],
                    'total_views': 0,
                    'clip_count': 0
                }
            creator_rankings[creator_id]['total_views'] += clip.get('view_count', 0)
            creator_rankings[creator_id]['clip_count'] += 1
        
        # Convert to list and sort by total_views in descending order
        creator_rankings_list = sorted(creator_rankings.values(), key=lambda x: x['total_views'], reverse=True)

        return jsonify({
            'id': campaign_data.get('id'),
            'name': campaign_data.get('name'),
            'platform': campaign_data.get('platform'),
            'image_url': campaign_data.get('image_url'),
            'budget': campaign_data.get('budget', 0),
            'cpv': campaign_data.get('cpv', 0),
            'hashtag': campaign_data.get('hashtag', ''),
            'audio': campaign_data.get('audio', ''),
            'deadline': campaign_data.get('deadline'),
            'brand_id': campaign_data.get('brand_id'),
            'funds_allocated': campaign_data.get('funds_allocated', 0),
            'funds_distributed': campaign_data.get('funds_distributed', 0),
            'is_active': campaign_data.get('is_active'),
            'asset_link': campaign_data.get('asset_link'),
            'category': campaign_data.get('category'),
            'total_view_count': campaign_data.get('total_view_count', 0),
            'requirements': campaign_data.get('requirements', ''),
            'view_threshold': campaign_data.get('view_threshold', 0),
            'accepted_clips': accepted_clips_sorted,
            'creator_rankings': creator_rankings_list
        }), 200

    except Exception as e:
        print(f"Get campaign by ID error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch campaign details', 'error': str(e)}), 500

@app.route('/api/creator/your-campaigns', methods=['GET'])
@jwt_required()
def get_creator_campaigns():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        print(f"Unauthorized access to creator campaigns. Role in token: {claims.get('role')}") # Debugging line
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = get_current_user_id()
    try:
        # Fetch campaigns that the creator has submitted clips for, or that are active.
        # This will get all campaigns a creator is associated with (either submitted a clip or accepted).
        submitted_campaign_ids_response = supabase.table('submitted_clips').select('campaign_id').eq('creator_id', creator_id).execute()
        accepted_campaign_ids_response = supabase.table('accepted_clips').select('campaign_id').eq('creator_id', creator_id).execute()
        
        submitted_campaign_ids = {s['campaign_id'] for s in submitted_campaign_ids_response.data} if submitted_campaign_ids_response.data else set()
        accepted_campaign_ids = {a['campaign_id'] for a in accepted_campaign_ids_response.data} if accepted_campaign_ids_response.data else set()
        
        all_relevant_campaign_ids = list(submitted_campaign_ids.union(accepted_campaign_ids))

        if not all_relevant_campaign_ids:
            return jsonify([]), 200

        # Fetch details of these campaigns that are also active
        campaigns_response = supabase.table('campaign').select('*').in_('id', all_relevant_campaign_ids).eq('is_active', True).execute()
        campaigns_data = campaigns_response.data
        
        result = []
        for campaign_data in campaigns_data:
            # Fetch submitted clips for this specific campaign and creator
            submitted_clips_for_campaign = supabase.table('submitted_clips').select('id, clip_url, submitted_at, is_deleted_by_admin, feedback').eq('campaign_id', campaign_data['id']).eq('creator_id', creator_id).execute()
            # Fetch accepted clips for this specific campaign and creator
            accepted_clips_for_campaign = supabase.table('accepted_clips').select('id, clip_url, submitted_at, media_id, view_count, caption, instagram_posted_at').eq('campaign_id', campaign_data['id']).eq('creator_id', creator_id).execute()

            # Map to expected frontend structure. Frontend will infer status.
            mapped_submitted_clips = [{
                'id': clip['id'],
                'clip_url': clip['clip_url'],
                'submitted_at': clip['submitted_at'],
                'is_deleted_by_admin': clip['is_deleted_by_admin'],
                'feedback': clip['feedback'],
                'status': 'pending' # Frontend expects a status, so we provide a placeholder
            } for clip in submitted_clips_for_campaign.data] if submitted_clips_for_campaign.data else []

            mapped_accepted_clips = [{
                'id': clip['id'],
                'clip_url': clip['clip_url'],
                'submitted_at': clip['submitted_at'],
                'media_id': clip['media_id'],
                'view_count': clip['view_count'],
                'caption': clip['caption'],
                'instagram_posted_at': clip['instagram_posted_at'],
                'status': 'accepted' # Frontend expects a status
            } for clip in accepted_clips_for_campaign.data] if accepted_clips_for_campaign.data else []

            campaign_info = {
                'id': campaign_data['id'],
                'name': campaign_data['name'],
                'platform': campaign_data['platform'],
                'budget': campaign_data['budget'],
                'cpv': campaign_data['cpv'],
                'hashtag': campaign_data['hashtag'],
                'asset_link': campaign_data['asset_link'],
                'category': campaign_data['category'],
                'audio': campaign_data['audio'],
                'deadline': campaign_data['deadline'],
                'brand_id': campaign_data['brand_id'],
                'is_active': campaign_data['is_active'],
                'category': campaign_data['category'],
                'total_view_count': campaign_data['total_view_count'],
                'requirements': campaign_data['requirements'],
                'image_url': campaign_data['image_url'],
                'view_threshold': campaign_data['view_threshold'],
                'funds_allocated': campaign_data.get('funds_allocated',0),
                'funds_distributed': campaign_data.get('funds_distributed', 0),
                'submitted_clips': mapped_submitted_clips,                
                'accepted_clips': mapped_accepted_clips
            }
            # Only add campaign to result if it has submitted or accepted clips
            if campaign_info['submitted_clips'] or campaign_info['accepted_clips']:
                result.append(campaign_info)
            
        return jsonify(result), 200

    except Exception as e:
        print(f"Get creator campaigns error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch creator campaigns', 'error': str(e)}), 500

@app.route('/api/creator/submit-clip', methods=['POST'])
@jwt_required()
def submit_clip():
    claims = get_jwt()
    user_role = claims.get('role')
    
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    creator_id = get_current_user_id()
    
    try:
        data = request.json
        required_fields = ['campaign_id', 'clip_url']
        if not all(k in data for k in required_fields):
            return jsonify({'msg': 'Missing required fields'}), 400

        campaign_id = data['campaign_id']
        clip_url = data['clip_url']

        # 1. Check if campaign exists/active
        # (Reads are usually fast, but you could wrap this in retry too if needed)
        campaign_response = supabase.table('campaign').select('id, is_active').eq('id', campaign_id).eq('is_active', True).limit(1).execute()
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not active'}), 404

        # 2. Check submission limit
        existing_clips_response = supabase.table('submitted_clips').select('id', count='exact').eq('creator_id', creator_id).eq('campaign_id', campaign_id).execute()
        if len(existing_clips_response.data or []) >= 5:
            return jsonify({'msg': 'You have reached the maximum limit of 5 submissions for this campaign.'}), 400

        new_clip = {
            'campaign_id': campaign_id,
            'creator_id': creator_id,
            'clip_url': clip_url,
            'submitted_at': datetime.utcnow().isoformat(),
            'is_deleted_by_admin': False,
            'feedback': None
        }

        # --- RETRY LOGIC START ---
        # This handles the "Server disconnected" error on Insert
        response = None
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                response = supabase.table('submitted_clips').insert([new_clip]).execute()
                # If successful, break the loop
                break 
            except Exception as e:
                # Check for network/disconnect errors
                error_msg = str(e).lower()
                is_network_error = "disconnected" in error_msg or "timeout" in error_msg or "connection" in error_msg
                
                if is_network_error and attempt < max_retries - 1:
                    print(f"⚠️ [Submit Clip] Connection dropped. Retrying ({attempt+1}/{max_retries})...")
                    time.sleep(0.5) # Wait 500ms before retrying
                    continue
                
                # If it's a real error (or we ran out of retries), raise it
                raise e
        # --- RETRY LOGIC END ---

        if response.data:
            return jsonify({'msg': 'Clip submitted successfully', 'clip_id': response.data[0]['id']}), 201
        else:
            print(f"Supabase submit clip error: {response.status_code} - {response.count}")
            return jsonify({'msg': 'Failed to submit clip', 'error': response.count}), 500

    except Exception as e:
        print(f"Submit clip error: {str(e)}")
        return jsonify({'msg': 'Failed to submit clip', 'error': str(e)}), 500

@app.route('/api/creator/campaign-clips', methods=['GET'])
@jwt_required()
def get_creator_clips_for_campaign():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = get_current_user_id()
    campaign_id = request.args.get('campaign_id', type=int)

    if not campaign_id:
        return jsonify({'msg': 'Missing campaign_id parameter'}), 400

    try:
        # Fetch submitted clips for a specific campaign by the current creator
        submitted_response = supabase.table('submitted_clips').select('id, campaign_id, creator_id, clip_url, submitted_at, is_deleted_by_admin, feedback').eq('creator_id', creator_id).eq('campaign_id', campaign_id).execute()
        submitted_clips_data = submitted_response.data if submitted_response.data else []

        # Fetch accepted clips for a specific campaign by the current creator
        accepted_response = supabase.table('accepted_clips').select('id, campaign_id, creator_id, clip_url, submitted_at, media_id, view_count, caption, instagram_posted_at').eq('creator_id', creator_id).eq('campaign_id', campaign_id).execute()
        accepted_clips_data = accepted_response.data if accepted_response.data else []

        result = []

        # Add submitted clips with 'in_review' or 'rejected' status
        for c in submitted_clips_data:
            status = 'rejected' if c.get('is_deleted_by_admin') else 'in_review'
            result.append({
                'id': c['id'],
                'campaign_id': c['campaign_id'],
                'creator_id': c['creator_id'],
                'clip_url': c['clip_url'],
                'submitted_at': c['submitted_at'],
                'status': status, # Inferred status
                'is_deleted_by_admin': c.get('is_deleted_by_admin', False),
                'feedback': c.get('feedback'),
                'media_id': None,
                'view_count': None,
                'caption': None,
                'instagram_posted_at': None
            })

        # Add accepted clips with 'accepted' status
        for c in accepted_clips_data:
            result.append({
                'id': c['id'],
                'campaign_id': c['campaign_id'],
                'creator_id': c['creator_id'],
                'clip_url': c['clip_url'],
                'submitted_at': c['submitted_at'],
                'status': 'accepted', # Inferred status
                'is_deleted_by_admin': False, # Accepted clips are not marked as deleted by admin
                'feedback': None, # Accepted clips do not have feedback
                'media_id': c.get('media_id'),
                'view_count': c.get('view_count'),
                'caption': c.get('caption'),
                'instagram_posted_at': c.get('instagram_posted_at'),
                # 'accepted_date': c.get('accepted_date') # Add accepted_date
            })

        return jsonify(result), 200
    except Exception as e:
        print(f"Get creator clips for campaign error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch clips', 'error': str(e)}), 500

@app.route('/api/creator/accepted-clip-details/<int:submitted_clip_id>', methods=['GET'])
@jwt_required()
def get_accepted_clip_details(submitted_clip_id):
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = get_current_user_id()

    try:
        # Fetch the accepted clip using the submitted_clip_id
        # Note: accepted_clips table uses its own 'id', not 'submitted_clip_id'.
        # The previous logic was incorrect if submitted_clip_id was meant to be the ID in accepted_clips.
        # Assuming submitted_clip_id passed here is the clip's ID (common for both tables once a clip moves)
        response = supabase.table('accepted_clips').select('*').eq('id', submitted_clip_id).eq('creator_id', creator_id).limit(1).execute()
        accepted_clip_data = response.data[0] if response.data else None

        if not accepted_clip_data:
            return jsonify({'msg': 'Accepted clip not found'}), 404

        return jsonify({
            'id': accepted_clip_data['id'],
            'campaign_id': accepted_clip_data['campaign_id'],
            'creator_id': accepted_clip_data['creator_id'],
            'clip_url': accepted_clip_data['clip_url'],
            'submitted_at': accepted_clip_data['submitted_at'],
            'media_id': accepted_clip_data['media_id'],
            'view_count': accepted_clip_data['view_count'],
            'caption': accepted_clip_data['caption'],
            'instagram_posted_at': accepted_clip_data['instagram_posted_at'],
        }), 200

    except Exception as e:
        print(f"Get accepted clip details error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch accepted clip details', 'error': str(e)}), 500



# ... inside your routes ...

@app.route('/api/brand/campaigns/<int:campaign_id>', methods=['DELETE', 'OPTIONS'])
def delete_campaign(campaign_id):
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    verify_jwt_in_request()
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()

    try:
        # 1. Fetch Details BEFORE deleting (Need image_url AND funds_allocated)
        campaign_response = supabase.table('campaign').select('id, image_url, funds_allocated, funds_distributed').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404
            
        campaign_data = campaign_response.data[0]
        image_url = campaign_data.get('image_url')
        funds_allocated = campaign_data.get('funds_allocated', 0)
        funds_distributed = campaign_data.get('funds_distributed', 0)
        refundable = funds_allocated - funds_distributed
        refund_msg = ""

        # 2. THE SAFE DELETE CHECK (Refund Logic - Phase 3)
        if refundable > 0:
            # Return unspent funds to brand wallet
            brand_response = supabase.table('brand').select('wallet_balance').eq('id', brand_id).limit(1).execute()
            current_balance = brand_response.data[0].get('wallet_balance', 0)
            new_balance = current_balance + refundable
            
            supabase.table('brand').update({'wallet_balance': new_balance}).eq('id', brand_id).execute()
            
            # Log refund transaction
            refund_txn = {
                'user_type': 'brand',
                'user_id': brand_id,
                'campaign_id': campaign_id,
                'amount': refundable,
                'type': 'refund',
                'status': 'success',
                'description': f'Refunded ₹{refundable:.2f} from deleted campaign {campaign_id}'
            }
            supabase.table('transactions').insert([refund_txn]).execute()
            
            refund_msg = f" Refunded ₹{refundable} to wallet."
            print(f"[Safe Delete] Campaign {campaign_id}: {refund_msg}")

        # 3. Delete Database Records (Cascading Delete manually if needed)
        supabase.table('submitted_clips').delete().eq('campaign_id', campaign_id).execute()
        supabase.table('accepted_clips').delete().eq('campaign_id', campaign_id).execute()
        response = supabase.table('campaign').delete().eq('id', campaign_id).execute()

        # 4. Storage Cleanup (Robust Logic)
        if image_url:
            try:
                decoded_url = unquote(image_url)
                if 'campaign-images/' in decoded_url:
                    file_path = decoded_url.split('campaign-images/')[1]
                    supabase.storage.from_('campaign-images').remove([file_path])
            except Exception as img_error:
                print(f"[Cleanup Warning] Failed to delete image: {str(img_error)}")

        return jsonify({'msg': f'Campaign deleted.{refund_msg}'}), 200

    except Exception as e:
        print(f"Delete campaign error: {str(e)}")
        return jsonify({'msg': 'Failed to delete campaign', 'error': str(e)}), 500

@app.route('/api/creator/clip/<int:clip_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required(optional=True)
def delete_clip(clip_id):
    if request.method == 'OPTIONS':
        return jsonify({'msg': 'OK'}), 200

    verify_jwt_in_request()
    claims = get_jwt()
    user_role = claims.get('role')
    if not user_role or user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = get_current_user_id()

    try:
        # OPTIMIZATION 1: Single-Step Delete for Submitted Clips
        # We add .eq('creator_id', creator_id) directly to the delete command.
        # If the clip belongs to someone else, 'response.data' will be empty. Secure & Fast.
        response = supabase.table('submitted_clips').delete().eq('id', clip_id).eq('creator_id', creator_id).execute()
        
        if response.data:
            return jsonify({'msg': 'Submitted clip deleted successfully'}), 200
        
        # ---------------------------------------------------------
        
        # OPTIMIZATION 2: Logic for Accepted Clips (Must keep 2 steps for math)
        # We still need to fetch 'view_count' first, so we can't optimize the read away.
        accepted_clip_response = supabase.table('accepted_clips').select('id, campaign_id, view_count').eq('id', clip_id).eq('creator_id', creator_id).limit(1).execute()

        if accepted_clip_response.data:
            accepted_data = accepted_clip_response.data[0]
            campaign_id = accepted_data['campaign_id']
            clip_views = accepted_data.get('view_count') or 0

            # Delete the row
            del_response = supabase.table('accepted_clips').delete().eq('id', clip_id).execute()
            
            # Update Campaign Analytics
            if del_response.data:
                # Fetch current total to ensure accuracy (Read-Modify-Write)
                camp_res = supabase.table('campaign').select('total_view_count').eq('id', campaign_id).limit(1).execute()
                if camp_res.data:
                    current_total = camp_res.data[0]['total_view_count'] or 0
                    new_total = max(0, current_total - clip_views)
                    
                    supabase.table('campaign').update({'total_view_count': new_total}).eq('id', campaign_id).execute()

                return jsonify({'msg': 'Accepted clip deleted and views updated'}), 200

        # If we reached here, it wasn't found in either table
        return jsonify({'msg': 'Clip not found or not authorized'}), 404

    except Exception as e:
        print(f"Delete clip error: {str(e)}")
        return jsonify({'msg': 'Failed to delete clip', 'error': str(e)}), 500

@app.route('/api/admin/campaigns', methods=['GET'])
@jwt_required()
def admin_get_campaigns():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    try:
        response = supabase.table('campaign').select('*, brand:brand(username)').execute() # Fetch related brand username
        campaigns_data = response.data
        
        if campaigns_data:
            result = []
            for c in campaigns_data:
                # Fetch submitted clips for the current campaign
                submitted_clips_response = supabase.table('submitted_clips').select('id, creator_id, clip_url, submitted_at, is_deleted_by_admin, feedback').eq('campaign_id', c['id']).execute()
                submitted_clips_data = submitted_clips_response.data if submitted_clips_response.data else []

                # Fetch accepted clips for the current campaign
                accepted_clips_response = supabase.table('accepted_clips').select('id, creator_id, clip_url, submitted_at, media_id, view_count, caption, instagram_posted_at').eq('campaign_id', c['id']).execute()
                accepted_clips_data = accepted_clips_response.data if accepted_clips_response.data else []

                result.append({
                    'id': c['id'],
                    'name': c['name'],
                    'platform': c['platform'],
                    'budget': c['budget'],
                    'cpv': c['cpv'],
                    'hashtag': c['hashtag'],
                    'audio': c['audio'],
                    'deadline': c['deadline'],
                    'brand_id': c['brand_id'],
                    'brand_username': c['brand']['username'] if c.get('brand') else None,
                    'is_active': c['is_active'],
                    'total_view_count': c['total_view_count'],
                    'requirements': c['requirements'],
                    'view_threshold': c['view_threshold'],
                    'submitted_clips': submitted_clips_data,
                    'accepted_clips': accepted_clips_data
                })
            return jsonify(result), 200
        else:
            return jsonify([]), 200
    except Exception as e:
        print(f"Admin get campaigns error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch campaigns', 'error': str(e)}), 500

@app.route('/api/admin/clip/<int:clip_id>', methods=['PUT'])
@jwt_required()
def admin_update_clip(clip_id):
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    try:
        data = request.json
        status = data.get('status')

        if status not in ['accepted', 'rejected']:
            return jsonify({'msg': 'Invalid status'}), 400
        
        # Fetch the submitted clip data to get creator_id and campaign_id
        submitted_clip_response = supabase.table('submitted_clips').select('*').eq('id', clip_id).limit(1).execute()
        submitted_clip_data = submitted_clip_response.data[0] if submitted_clip_response.data else None

        if not submitted_clip_data:
            return jsonify({'msg': 'Clip not found'}), 404

        creator_id = submitted_clip_data['creator_id']
        campaign_id = submitted_clip_data['campaign_id']
        clip_url = submitted_clip_data['clip_url']

        # Fetch campaign name for the notification message
        campaign_response = supabase.table('campaign').select('name').eq('id', campaign_id).limit(1).execute()
        campaign_name = campaign_response.data[0]['name'] if campaign_response.data else f'Campaign {campaign_id}'

        if status == 'accepted':
            # Insert into accepted_clips table
            new_accepted_clip = {
                'id': clip_id, # Use same ID as submitted clip
                'creator_id': creator_id,
                'campaign_id': campaign_id,
                'clip_url': clip_url,
                'submitted_at': submitted_clip_data['submitted_at'], # Original submission timestamp
                'media_id': None,
                'view_count': None,
                'caption': None,
                'instagram_posted_at': None,
            }
            supabase.table('accepted_clips').insert([new_accepted_clip]).execute()

            # Delete from submitted_clips table
            supabase.table('submitted_clips').delete().eq('id', clip_id).execute()
            
            # --- NOTIFICATION FOR ACCEPTED CLIP ---
            notification_message = f"Your submission for '{campaign_name}' (Clip ID: {clip_id}) was approved!"
            notification_data = {
                "message": notification_message,
                "type": "clip_approved",
                "campaign_id": campaign_id,
                "clip_id": clip_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            supabase.rpc('append_notification', {'p_creator_id': creator_id, 'p_new_notification': notification_data}).execute()

            return jsonify({'msg': 'Clip accepted and moved to accepted_clips'}), 200

        elif status == 'rejected':
            # Update submitted_clips to mark as rejected by admin and add feedback
            update_fields = {
                'is_deleted_by_admin': True,
                'feedback': data.get('feedback') # Use feedback from request
            }
            supabase.table('submitted_clips').update(update_fields).eq('id', clip_id).execute()

            # If the clip was previously accepted, delete it from accepted_clips table
            supabase.table('accepted_clips').delete().eq('id', clip_id).execute()

            # --- NOTIFICATION FOR REJECTED CLIP ---
            notification_message = f"Your submission for '{campaign_name}' (Clip ID: {clip_id}) was rejected. Reason: {data.get('feedback', 'No feedback provided.')}"
            notification_data = {
                "message": notification_message,
                "type": "clip_rejected",
                "campaign_id": campaign_id,
                "clip_id": clip_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            supabase.rpc('append_notification', {'p_creator_id': creator_id, 'p_new_notification': notification_data}).execute()
            
            return jsonify({'msg': 'Clip marked as rejected for creator'}), 200

        else:
            return jsonify({'msg': 'Invalid status'}), 400

    except Exception as e:
        print(f"Admin update clip error: {str(e)}")
        return jsonify({'msg': 'Failed to update clip', 'error': str(e)}), 500

@app.route('/api/admin/clip/<int:clip_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required(optional=True) # Allow OPTIONS requests without JWT
def delete_clip_admin(clip_id):
    if request.method == 'OPTIONS':
        # Preflight request, no need to process JWT
        return jsonify({'msg': 'OK'}), 200

    claims = get_jwt()
    user_role = claims.get('role')
    if not user_role or user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403

    try:
        # Check if the clip exists in accepted_clips first (it has view_count)
        accepted_clip_response = supabase.table('accepted_clips').select('id, campaign_id, view_count').eq('id', clip_id).limit(1).execute()

        if accepted_clip_response.data:
            accepted_clip_data = accepted_clip_response.data[0]
            campaign_id = accepted_clip_data['campaign_id']
            clip_view_count = accepted_clip_data['view_count'] or 0

            # Delete from accepted_clips
            supabase.table('accepted_clips').delete().eq('id', clip_id).execute()
            
            # Update total_view_count for the campaign
            current_campaign_response = supabase.table('campaign').select('total_view_count').eq('id', campaign_id).limit(1).execute()
            current_view_count = current_campaign_response.data[0]['total_view_count'] if current_campaign_response.data else 0
            updated_view_count = max(0, current_view_count - clip_view_count)
            supabase.table('campaign').update({'total_view_count': updated_view_count}).eq('id', campaign_id).execute()
            
            # Also try to delete from submitted_clips (in case it still exists for some reason, e.g., if re-accepted manually)
            supabase.table('submitted_clips').delete().eq('id', clip_id).execute()

            return jsonify({'msg': 'Accepted clip and associated submitted clip deleted successfully'}), 200
        
        # If not found in accepted_clips, check submitted_clips
        submitted_clip_response = supabase.table('submitted_clips').select('id').eq('id', clip_id).limit(1).execute()
        
        if submitted_clip_response.data:
            # If it's a submitted clip, just delete it (no view_count impact)
            supabase.table('submitted_clips').delete().eq('id', clip_id).execute()
            return jsonify({'msg': 'Submitted clip deleted successfully'}), 200
        else:
            # If response.count is 0, it means the clip was not found or already deleted.
            # In this context, the desired state (clip not existing) is achieved.
            return jsonify({'msg': 'Submitted clip already deleted or not found'}), 200

        return jsonify({'msg': 'Clip not found'}), 404

    except Exception as e:
        print(f"Admin delete clip error: {str(e)}")
        return jsonify({'msg': 'Failed to delete clip', 'error': str(e)}), 500

@app.route('/api/creator/profile', methods=['GET'])
@jwt_required()
def get_creator_profile():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = get_current_user_id()
    try:
        # Select only the relevant profile fields
        response = supabase.table('creator').select(
            'id, username, email, profile_completed, phone, nickname, bio, join_date, instagram_username, instagram_verified'
        ).eq('id', creator_id).limit(1).execute()
        creator_data = response.data[0] if response.data else None

        if creator_data:
            return jsonify({
                'id': creator_data['id'],
                'username': creator_data['username'],
                'email': creator_data['email'],
                'profile_completed': creator_data.get('profile_completed'),
                'phone': creator_data.get('phone'),
                'nickname': creator_data.get('nickname'),
                'bio': creator_data.get('bio'),
                'join_date': creator_data.get('join_date'),
                'instagram_username': creator_data.get('instagram_username'),
                'instagram_verified': creator_data.get('instagram_verified')
            }), 200
        else:
            return jsonify({'msg': 'Creator not found'}), 404
    except Exception as e:
        print(f"Get creator profile error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch creator profile', 'error': str(e)}), 500

@app.route('/api/creator/profile', methods=['PUT'])
@jwt_required()
def update_creator_profile():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = get_current_user_id()
    try:
        data = request.json
        update_fields = {}
        
        if 'instagram_username' in data:
            insta_username = data['instagram_username']
            if insta_username:
                verification_result = verify_instagram_username(insta_username)
                if verification_result.get("exists"):
                    update_fields['instagram_username'] = insta_username
                    update_fields['instagram_verified'] = True
                else:
                    return jsonify({'msg': f'Instagram user @{insta_username} not found.'}), 400
            else:
                update_fields['instagram_username'] = None
                update_fields['instagram_verified'] = False

        if 'phone' in data: update_fields['phone'] = data['phone']
        if 'nickname' in data: update_fields['nickname'] = data['nickname']
        if 'bio' in data: update_fields['bio'] = data['bio']

        if 'phone' in update_fields or 'nickname' in update_fields or 'bio' in update_fields:
            update_fields['profile_completed'] = True

        if not update_fields:
            return jsonify({'msg': 'No fields to update'}), 400

        response = supabase.table('creator').update(update_fields).eq('id', creator_id).execute()

        if response.data:
            return jsonify({'msg': 'Creator profile updated successfully'}), 200
        else:
            print(f"Supabase update creator profile error: {response.status_code} - {response.count}")
            return jsonify({'msg': 'Failed to update creator profile', 'error': response.count}), 500
    except Exception as e:
        print(f"Update creator profile error: {str(e)}")
        return jsonify({'msg': 'Failed to update creator profile', 'error': str(e)}), 500

@app.route('/api/brand/campaigns/<int:campaign_id>/image', methods=['PUT', 'OPTIONS'])
@jwt_required(optional=True)
def update_campaign_image(campaign_id):
    # 1. Handle CORS preflight for this specific route
    if request.method == 'OPTIONS':
        return jsonify({'msg': 'OK'}), 200

    # 2. Verify Auth
    verify_jwt_in_request()
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()

    try:
        data = request.json
        new_image_url = data.get('image_url')
        
        if not new_image_url:
            return jsonify({'msg': 'Missing image_url field'}), 400

        # 3. Verify Ownership
        campaign_response = supabase.table('campaign').select('id').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404

        # 4. Update Database
        response = supabase.table('campaign').update({'image_url': new_image_url}).eq('id', campaign_id).execute()

        if response.data:
            return jsonify({'msg': 'Campaign image updated successfully'}), 200
        else:
            return jsonify({'msg': 'Failed to update campaign image'}), 500

    except Exception as e:
        print(f"Update campaign image error: {str(e)}")
        return jsonify({'msg': 'Failed to update campaign image', 'error': str(e)}), 500

@app.route('/api/brand/campaigns/<int:campaign_id>/budget', methods=['PUT'])
@jwt_required()
def update_campaign_budget(campaign_id):
    claims = get_jwt()
    user_role = claims.get('role')
    
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()

    try:
        data = request.json
        new_budget = data.get('budget')
        if new_budget is None:
            return jsonify({'msg': 'Missing budget field'}), 400

        # Verify the campaign belongs to the brand
        campaign_response = supabase.table('campaign').select('id').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404

        response = supabase.table('campaign').update({'budget': new_budget}).eq('id', campaign_id).execute()

        if response.data:
            return jsonify({'msg': 'Campaign budget updated successfully'}), 200
        else:
            return jsonify({'msg': 'Failed to update campaign budget'}), 500

    except Exception as e:
        print(f"Update campaign budget error: {str(e)}")
        return jsonify({'msg': 'Failed to update campaign budget', 'error': str(e)}), 500

@app.route('/api/brand/campaigns/<int:campaign_id>/requirements', methods=['PUT'])
@jwt_required()
def update_campaign_requirements(campaign_id):
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()

    try:
        data = request.json
        new_requirements = data.get('requirements')

        # Verify the campaign belongs to the brand
        campaign_response = supabase.table('campaign').select('id').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404

        response = supabase.table('campaign').update({'requirements': new_requirements}).eq('id', campaign_id).execute()

        if response.data:
            return jsonify({'msg': 'Campaign requirements updated successfully'}), 200
        else:
            return jsonify({'msg': 'Failed to update campaign requirements'}), 500

    except Exception as e:
        print(f"Update campaign requirements error: {str(e)}")
        return jsonify({'msg': 'Failed to update campaign requirements', 'error': str(e)}), 500

@app.route('/api/brand/campaigns/<int:campaign_id>/status', methods=['PUT'])
@jwt_required()
def update_campaign_status(campaign_id):
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()

    try:
        data = request.json
        new_status = data.get('is_active')
        if new_status is None or not isinstance(new_status, bool):
            return jsonify({'msg': 'Missing or invalid is_active field (must be boolean)'}), 400

        # Verify the campaign belongs to the brand
        campaign_response = supabase.table('campaign').select('id').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404

        response = supabase.table('campaign').update({'is_active': new_status}).eq('id', campaign_id).execute()

        if response.data:
            return jsonify({'msg': 'Campaign status updated successfully'}), 200
        else:
            return jsonify({'msg': 'Failed to update campaign status'}), 500

    except Exception as e:
        print(f"Update campaign status error: {str(e)}")
        return jsonify({'msg': 'Failed to update campaign status', 'error': str(e)}), 500

@app.route('/api/brand/campaigns/<int:campaign_id>/view_threshold', methods=['PUT'])
@jwt_required()
def update_campaign_view_threshold(campaign_id):
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()

    try:
        data = request.json
        new_threshold = data.get('view_threshold')
        if new_threshold is None or not isinstance(new_threshold, (int, float)) or new_threshold < 0:
            return jsonify({'msg': 'Missing or invalid view_threshold field (must be non-negative number)'}), 400

        # Verify the campaign belongs to the brand
        campaign_response = supabase.table('campaign').select('id').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404

        response = supabase.table('campaign').update({'view_threshold': new_threshold}).eq('id', campaign_id).execute()

        if response.data:
            return jsonify({'msg': 'Campaign view threshold updated successfully'}), 200
        else:
            return jsonify({'msg': 'Failed to update campaign view threshold'}), 500

    except Exception as e:
        print(f"Update campaign view threshold error: {str(e)}")
        return jsonify({'msg': 'Failed to update campaign view threshold', 'error': str(e)}), 500

@app.route('/api/brand/campaigns/<int:campaign_id>/deadline', methods=['PUT'])
@jwt_required()
def update_campaign_deadline(campaign_id):
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()

    try:
        data = request.json
        new_deadline_str = data.get('deadline')
        if not new_deadline_str:
            return jsonify({'msg': 'Missing deadline field'}), 400

        # Validate date format
        try:
            datetime.strptime(new_deadline_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'msg': 'Invalid deadline format. Use YYYY-MM-DD.'}), 400

        # Verify the campaign belongs to the brand
        campaign_response = supabase.table('campaign').select('id').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404

        response = supabase.table('campaign').update({'deadline': new_deadline_str}).eq('id', campaign_id).execute()

        if response.data:
            return jsonify({'msg': 'Campaign deadline updated successfully'}), 200
        else:
            return jsonify({'msg': 'Failed to update campaign deadline'}), 500

    except Exception as e:
        print(f"Update campaign deadline error: {str(e)}")
        return jsonify({'msg': 'Failed to update campaign deadline', 'error': str(e)}), 500

@app.route('/api/brand/campaigns/<int:campaign_id>/pending-payouts', methods=['GET'])
@jwt_required()
def get_pending_payouts(campaign_id):
    """
    Get pending creator payouts for a campaign.
    Shows creators who have reached view threshold but haven't been paid yet.
    
    Phase 3: Helps brands identify when distributions are due.
    """
    verify_jwt_in_request()
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_current_user_id()
    
    try:
        # 1. Verify campaign belongs to brand
        campaign_response = supabase.table('campaign').select('id, cpv, view_threshold, total_view_count').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404
        
        campaign = campaign_response.data[0]
        cpv = campaign.get('cpv', 0)
        view_threshold = campaign.get('view_threshold', 0)
        total_views = campaign.get('total_view_count', 0)
        
        # 2. Get all creators with accepted clips on this campaign
        accepted_clips_response = supabase.table('accepted_clips').select('creator_id, view_count, amount_paid').eq('campaign_id', campaign_id).execute()
        
        if not accepted_clips_response.data:
            return jsonify({
                'msg': 'No clips submitted for this campaign',
                'campaign_id': campaign_id,
                'pending_payouts': []
            }), 200
        
        # 3. Group by creator and calculate pending amounts
        creator_views = {}
        creator_paid = {}
        
        for clip in accepted_clips_response.data:
            creator_id = clip.get('creator_id')
            view_count = clip.get('view_count', 0)
            amount_paid = clip.get('amount_paid', 0)
            
            if creator_id not in creator_views:
                creator_views[creator_id] = 0
                creator_paid[creator_id] = 0
            
            creator_views[creator_id] += view_count
            creator_paid[creator_id] += amount_paid
        
        # 4. Calculate pending payouts
        pending_payouts = []
        
        for creator_id, total_views_creator in creator_views.items():
            # Calculate earnings for this creator
            earnings = (total_views_creator / view_threshold) * cpv if view_threshold > 0 else 0
            already_paid = creator_paid[creator_id]
            pending = earnings - already_paid
            
            if pending > 0:
                creator_response = supabase.table('creator').select('username').eq('id', creator_id).limit(1).execute()
                creator_name = creator_response.data[0].get('username', f'Creator {creator_id}') if creator_response.data else f'Creator {creator_id}'
                
                pending_payouts.append({
                    'creator_id': creator_id,
                    'creator_name': creator_name,
                    'total_views': total_views_creator,
                    'total_earnings': earnings,
                    'already_paid': already_paid,
                    'pending_amount': pending,
                    'creator_share': pending * 0.9,
                    'platform_commission': pending * 0.1
                })
        
        return jsonify({
            'msg': 'Pending payouts retrieved successfully',
            'campaign_id': campaign_id,
            'campaign_metrics': {
                'cpv': cpv,
                'view_threshold': view_threshold,
                'total_campaign_views': total_views
            },
            'pending_count': len(pending_payouts),
            'total_pending_amount': sum([p['pending_amount'] for p in pending_payouts]),
            'pending_payouts': pending_payouts
        }), 200
        
    except Exception as e:
        print(f"Get pending payouts error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve pending payouts', 'error': str(e)}), 500

# --- BRAND PROFILE ENDPOINTS ---
@app.route('/api/brand/profile', methods=['GET'])
@jwt_required()
def get_brand_profile():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()

    try:
        response = supabase.table('brand').select('id, username, email, phone').eq('id', brand_id).limit(1).execute()
        brand_data = response.data[0] if response.data else None

        if not brand_data:
            return jsonify({'msg': 'Brand not found'}), 404
        
        return jsonify({
            'id': brand_data['id'],
            'username': brand_data['username'],
            'email': brand_data['email'],
            'phone': brand_data.get('phone') # phone might be null
        }), 200

    except Exception as e:
        print(f"Get brand profile error: {str(e)}")
        return jsonify({'msg': 'Failed to fetch brand profile', 'error': str(e)}), 500

@app.route('/api/brand/profile', methods=['PUT'])
@jwt_required()
def update_brand_profile():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = get_current_user_id()
    data = request.json

    update_fields = {}
    if 'username' in data:
        update_fields['username'] = data['username']
    if 'phone' in data:
        update_fields['phone'] = data['phone']

    if not update_fields:
        return jsonify({'msg': 'No fields to update'}), 400

    try:
        response = supabase.table('brand').update(update_fields).eq('id', brand_id).execute()
        
        if response.data:
            return jsonify({'msg': 'Brand profile updated successfully'}), 200
        else:
            return jsonify({'msg': 'Failed to update brand profile', 'error': response.count}), 500

    except Exception as e:
        print(f"Update brand profile error: {str(e)}")
        return jsonify({'msg': 'Failed to update brand profile', 'error': str(e)}), 500


@app.route('/api/admin/clip/<int:clip_id>/view-count', methods=['PUT'])
@jwt_required()
def update_clip_view_count(clip_id):
    """
    Phase 4: Update view count for an accepted clip.
    This triggers the performance loop - allows tracking real-time metrics.
    
    Body: {
        "view_count": int
    }
    
    Response includes:
    - Updated clip data
    - Campaign total views updated
    - Earnings calculations (if applicable)
    """
    verify_jwt_in_request()
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    data = request.json
    new_view_count = data.get('view_count')
    
    if new_view_count is None or new_view_count < 0:
        return jsonify({'msg': 'Invalid view count'}), 400
    
    try:
        # Get existing clip
        clip_response = supabase.table('accepted_clips').select('id, campaign_id, view_count').eq('id', clip_id).limit(1).execute()
        
        if not clip_response.data:
            return jsonify({'msg': 'Clip not found'}), 404
        
        clip = clip_response.data[0]
        campaign_id = clip.get('campaign_id')
        old_view_count = clip.get('view_count', 0)
        view_count_diff = new_view_count - old_view_count
        
        # Update clip view count
        supabase.table('accepted_clips').update({'view_count': new_view_count}).eq('id', clip_id).execute()
        
        # Update campaign total view count
        campaign_response = supabase.table('campaign').select('total_view_count').eq('id', campaign_id).limit(1).execute()
        
        if campaign_response.data:
            current_total = campaign_response.data[0].get('total_view_count', 0)
            new_total = current_total + view_count_diff
            supabase.table('campaign').update({'total_view_count': new_total}).eq('id', campaign_id).execute()
        
        print(f"[View Count Update] Clip {clip_id}: {old_view_count} → {new_view_count} views (diff: {view_count_diff:+d})")
        
        return jsonify({
            'msg': 'View count updated successfully',
            'clip_id': clip_id,
            'campaign_id': campaign_id,
            'old_view_count': old_view_count,
            'new_view_count': new_view_count,
            'view_count_diff': view_count_diff
        }), 200
        
    except Exception as e:
        print(f"Update view count error: {str(e)}")
        return jsonify({'msg': 'Failed to update view count', 'error': str(e)}), 500

@app.route('/api/admin/campaign/<int:campaign_id>/update-views', methods=['PUT'])
@jwt_required()
def update_campaign_view_count(campaign_id):
    """
    Phase 4: Bulk update campaign view count from Instagram metrics.
    Recalculates total from all accepted clips on the campaign.
    
    Body: {
        "total_view_count": int  (optional - if not provided, recalculate from clips)
    }
    
    Response: Updated campaign data
    """
    verify_jwt_in_request()
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    data = request.json
    new_total_views = data.get('total_view_count')
    
    try:
        # Get all accepted clips for campaign
        clips_response = supabase.table('accepted_clips').select('view_count').eq('campaign_id', campaign_id).execute()
        
        if new_total_views is None:
            # Recalculate from clips
            new_total_views = sum([c.get('view_count', 0) for c in clips_response.data]) if clips_response.data else 0
        
        # Update campaign
        campaign_response = supabase.table('campaign').select('total_view_count').eq('id', campaign_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found'}), 404
        
        old_total = campaign_response.data[0].get('total_view_count', 0)
        
        supabase.table('campaign').update({'total_view_count': new_total_views}).eq('id', campaign_id).execute()
        
        print(f"[Campaign View Update] Campaign {campaign_id}: {old_total} → {new_total_views} views")
        
        return jsonify({
            'msg': 'Campaign view count updated successfully',
            'campaign_id': campaign_id,
            'old_total_views': old_total,
            'new_total_views': new_total_views,
            'view_diff': new_total_views - old_total,
            'clip_count': len(clips_response.data) if clips_response.data else 0
        }), 200
        
    except Exception as e:
        print(f"Update campaign views error: {str(e)}")
        return jsonify({'msg': 'Failed to update campaign views', 'error': str(e)}), 500

@app.route('/api/admin/analytics/campaign-performance/<int:campaign_id>', methods=['GET'])
@jwt_required()
def get_campaign_performance_analytics(campaign_id):
    """
    Phase 4: Get comprehensive performance analytics for a campaign.
    Shows view trends, creator performance, earning distribution, etc.
    """
    verify_jwt_in_request()
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    try:
        # Get campaign details
        campaign_response = supabase.table('campaign').select('id, cpv, view_threshold, total_view_count, funds_allocated, funds_distributed').eq('id', campaign_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found'}), 404
        
        campaign = campaign_response.data[0]
        cpv = campaign.get('cpv', 0)
        view_threshold = campaign.get('view_threshold', 0)
        total_views = campaign.get('total_view_count', 0)
        funds_allocated = campaign.get('funds_allocated', 0)
        funds_distributed = campaign.get('funds_distributed', 0)
        
        # Get all accepted clips
        clips_response = supabase.table('accepted_clips').select('id, creator_id, view_count, amount_paid, submitted_at').eq('campaign_id', campaign_id).execute()
        clips = clips_response.data if clips_response.data else []
        
        # Group by creator
        creator_performance = {}
        
        for clip in clips:
            creator_id = clip.get('creator_id')
            view_count = clip.get('view_count', 0)
            amount_paid = clip.get('amount_paid', 0)
            
            if creator_id not in creator_performance:
                creator_response = supabase.table('creator').select('username').eq('id', creator_id).limit(1).execute()
                creator_name = creator_response.data[0].get('username', f'Creator {creator_id}') if creator_response.data else f'Creator {creator_id}'
                
                creator_performance[creator_id] = {
                    'creator_id': creator_id,
                    'creator_name': creator_name,
                    'total_views': 0,
                    'clips': 0,
                    'total_earned': 0,
                    'total_paid': 0,
                    'pending': 0
                }
            
            creator_performance[creator_id]['total_views'] += view_count
            creator_performance[creator_id]['clips'] += 1
            creator_performance[creator_id]['total_paid'] += amount_paid
        
        # Calculate earnings for each creator
        for creator_id, perf in creator_performance.items():
            earned = (perf['total_views'] / view_threshold) * cpv if view_threshold > 0 else 0
            perf['total_earned'] = earned
            perf['pending'] = earned - perf['total_paid']
        
        # Sort by views descending
        sorted_creators = sorted(creator_performance.values(), key=lambda x: x['total_views'], reverse=True)
        
        # Calculate overall metrics
        total_clips = len(clips)
        total_earned = sum([p['total_earned'] for p in creator_performance.values()])
        total_pending = sum([p['pending'] for p in creator_performance.values()])
        utilization = (total_earned / funds_allocated * 100) if funds_allocated > 0 else 0
        
        return jsonify({
            'msg': 'Campaign performance analytics retrieved successfully',
            'campaign_id': campaign_id,
            'overview': {
                'total_clips': total_clips,
                'total_creators': len(creator_performance),
                'total_views': total_views,
                'milestones_reached': total_views // view_threshold if view_threshold > 0 else 0,
                'cpv': cpv,
                'view_threshold': view_threshold
            },
            'financial': {
                'funds_allocated': funds_allocated,
                'funds_distributed': funds_distributed,
                'total_earned': total_earned,
                'total_pending': total_pending,
                'utilization_percentage': utilization,
                'remaining_budget': funds_allocated - funds_distributed
            },
            'creator_performance': sorted_creators
        }), 200
        
    except Exception as e:
        print(f"Get campaign performance analytics error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve analytics', 'error': str(e)}), 500

@app.route('/api/auth/google-sync', methods=['POST'])
@jwt_required()
def google_sync():
    """
    Called by frontend after Google OAuth login.
    Ensures the user exists in public.brand or public.creator tables.
    This function is designed to be idempotent.
    """
    claims = get_jwt()
    user_id = get_jwt_identity()
    
    role = claims.get('role')
    jwt_username = claims.get('username') 
    
    # Fetch user data from Supabase to get email (and potentially full_name/name)
    try:
        user_response = supabase.auth.admin.get_user(user_id)
        email = user_response.user.email if user_response.user else None
    except Exception as e:
        print(f"Error fetching user from Supabase in google_sync: {e}")
        return jsonify({'msg': 'Failed to sync Google user: could not retrieve user data', 'error': str(e)}), 500

    if not role or not email:
        return jsonify({'msg': 'Missing role or email'}), 400

    try:
        table_name = 'brand' if role == 'brand' else 'creator'
        
        # 1. Check if profile already exists. This is the first check.
        exists_response = supabase.table(table_name).select('id').eq('id', user_id).execute()
        
        if exists_response.data:
            return jsonify({'msg': 'User already synced'}), 200

        # 2. Generate a username
        # Use username from JWT claims if available, otherwise derive from email
        base_name = jwt_username or email.split('@')[0] 
        clean_name = re.sub(r'[^a-zA-Z0-9]', '', base_name).lower()
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        username = f"{clean_name}_{random_suffix}"

        # 3. Prepare Insert Data
        new_record = {
            'id': user_id,
            'email': email,
            'username': username,
            'password_hash': 'google_oauth_managed', 
        }
        
        if role == 'creator':
            new_record['join_date'] = datetime.utcnow().strftime('%Y-%m-%d')
            new_record['profile_completed'] = False

        # 4. Insert into Public Table
        insert_response = supabase.table(table_name).insert([new_record]).execute()
        
        if insert_response.data:
            print(f"[Google Sync] Created new {role}: {username} ({user_id})")
            return jsonify({'msg': 'User profile created successfully', 'username': username}), 201
        else:
            # This 'else' block might not be hit if an APIError is raised.
            # Included for completeness.
            return jsonify({'msg': 'Failed to create profile due to an unknown insert error'}), 500

    except APIError as e:
        # This is the key change: Catch the specific error from Supabase
        if e.code == '23505': # 23505 is the PostgreSQL code for 'unique_violation'
            print(f"[Google Sync] Race condition handled: User {user_id} already exists.")
            return jsonify({'msg': 'User already synced (race condition handled)'}), 200
        else:
            # If it's a different API error, report it
            print(f"Google sync API error: {str(e)}")
            return jsonify({'msg': 'Sync failed due to database error', 'error': str(e)}), 500
    except Exception as e:
        # Catch any other general exceptions
        print(f"Google sync generic error: {str(e)}")
        return jsonify({'msg': 'Sync failed', 'error': str(e)}), 500

@app.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    """
    Refresh endpoint. Given a valid refresh token,
    return a new access token.
    """
    identity = get_jwt_identity()
    claims = get_jwt()
    additional_claims = {
        "role": claims.get("role"),
        "username": claims.get("username")
    }
    access_token = create_access_token(identity=identity, additional_claims=additional_claims)
    return jsonify(access_token=access_token)

@app.route('/logout', methods=['DELETE'])
@jwt_required(verify_type=False) # True by default, but we need to accept both token types
def logout():
    """
    Endpoint for revoking tokens. It will add the current token's JTI to the blocklist.
    """
    token = get_jwt()
    jti = token["jti"]
    token_type = token["type"]
    blocklist.add(jti)
    return jsonify(msg=f"{token_type.capitalize()} token successfully revoked"), 200

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint to verify the API is running and can connect to the database"""
    try:
        # Test database connection
        result = supabase.table('brand').select('count', count='exact').execute()
        
        # Test JWT configuration
        test_token = create_access_token(identity={'id': 'test', 'role': 'admin'})
        
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'jwt': 'configured',
            'timestamp': datetime.utcnow().isoformat()
        }), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'database': 'connection failed',
            'timestamp': datetime.utcnow().isoformat()
        }), 500

# Register the payments blueprint with the correct prefix
app.register_blueprint(payments_bp, url_prefix='/api/payments')

if __name__ == '__main__':
    # Start the Flask app with debug mode and auto-reloader
    app.run(debug=True, port=5000, use_reloader=True)
