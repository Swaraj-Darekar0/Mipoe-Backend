from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity, get_jwt, verify_jwt_in_request
from models import bcrypt, Brand, Creator, Campaign, SubmittedClip, AcceptedClip, Admin
from config import Config
from datetime import datetime, timedelta
from urllib.parse import urlencode
import requests
from utils import encrypt_token, decrypt_token
from dotenv import load_dotenv
import os
import re
from supabase import create_client, Client
from urllib.parse import unquote 
from routes.payments import payments_bp

load_dotenv()

app = Flask(__name__)
app.config.from_object(Config)
bcrypt.init_app(app)
# Debugging: Print environment variables to confirm they are loaded


# Initialize Supabase client with connection pooling
# The connection pool size is configured via SUPABASE_POOL_SIZE in config.py (default 10)
supabase: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

# Enable CORS for all routes and allow all headers
CORS(app, resources={r"/*": {"origins": "*"}})
jwt = JWTManager(app)

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
        if not all(k in data for k in ['username', 'email', 'password', 'role']):
            return jsonify({'msg': 'Missing required fields'}), 400

        email = data['email']
        username = data['username']
        password = data['password']
        role = data['role']
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        # Cross-table email check using Supabase
        # Check if email exists in any table (Brand, Creator, Admin)
        brand_exists = supabase.table('brand').select('email').eq('email', email).limit(1).execute()
        creator_exists = supabase.table('creator').select('email').eq('email', email).limit(1).execute()
        admin_exists = supabase.table('admin').select('email').eq('email', email).limit(1).execute()

        if brand_exists.data or creator_exists.data or admin_exists.data:
            return jsonify({'msg': 'Email already registered'}), 400

        if role == 'brand':
            new_user = {'username': username, 'email': email, 'password_hash': hashed_password}
            response = supabase.table('brand').insert([new_user]).execute()
        elif role == 'creator':
            new_user = {'username': username, 'email': email, 'password_hash': hashed_password, 'profile_completed': False, 'join_date': datetime.utcnow().date().isoformat()}
            response = supabase.table('creator').insert([new_user]).execute()
        elif role == 'admin':
            # Ensure only one admin with the same email
            new_user = {'username': username, 'email': email, 'password_hash': hashed_password}
            response = supabase.table('admin').insert([new_user]).execute()
        else:
            return jsonify({'msg': 'Invalid role'}), 400

        if response.data:
            return jsonify({'msg': 'User registered successfully'}), 201
        else:
            print(f"Supabase registration error: {response.status_code} - {response.count}")
            return jsonify({'msg': 'Registration failed', 'error': response.count}), 500

    except Exception as e:
        print(f"Registration error: {str(e)}")
        return jsonify({'msg': 'Registration failed', 'error': str(e)}), 500

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        if not all(k in data for k in ['email', 'password', 'role']):
            return jsonify({'msg': 'Missing email, password, or role'}), 400

        email = data['email']
        password = data['password']
        role = data['role']
        user = None

        if role == 'brand':
            response = supabase.table('brand').select('id, username, email, password_hash').eq('email', email).limit(1).execute()
            if response.data: # If brand not found, check if email exists as creator
                user_data = response.data[0]
                if bcrypt.check_password_hash(user_data['password_hash'], password):
                    user = Brand(id=user_data['id'], username=user_data['username'], email=user_data['email'], password_hash=user_data['password_hash'])
                else: return jsonify({'msg': 'Invalid credentials'}), 401
            elif supabase.table('creator').select('email').eq('email', email).limit(1).execute().data: # Check if email exists as creator
                    return jsonify({'msg': 'This email is not registered as a Brand.'}), 400
            else:
                return jsonify({'msg': 'Invalid credentials'}), 401

        elif role == 'creator':
            response = supabase.table('creator').select('id, username, email, password_hash, profile_completed').eq('email', email).limit(1).execute()
            if response.data: # If creator not found, check if email exists as brand
                user_data = response.data[0]
                if bcrypt.check_password_hash(user_data['password_hash'], password):
                    user = Creator(id=user_data['id'], username=user_data['username'], email=user_data['email'], password_hash=user_data['password_hash'], profile_completed=user_data['profile_completed'])
                else: return jsonify({'msg': 'Invalid credentials'}), 401
            elif supabase.table('brand').select('email').eq('email', email).limit(1).execute().data: # Check if email exists as brand
                    return jsonify({'msg': 'This email is not registered as a Creator.'}), 400
            else:
                return jsonify({'msg': 'Invalid credentials'}), 401

        elif role == 'admin':
            response = supabase.table('admin').select('id, username, email, password_hash').eq('email', email).limit(1).execute()
            if response.data:
                user_data = response.data[0]
                if bcrypt.check_password_hash(user_data['password_hash'], password):
                    user = Admin(id=user_data['id'], username=user_data['username'], email=user_data['email'], password_hash=user_data['password_hash'])
                else: return jsonify({'msg': 'Invalid credentials'}), 401
            else:
                return jsonify({'msg': 'Invalid credentials'}), 401
        else:
            return jsonify({'msg': 'Invalid role'}), 400

        if user:
            access_token = create_access_token(
                identity=str(user.id),
                additional_claims={'role': role}
            )
            response_data = {
                'access_token': access_token,
                'role': role,
                'username': user.username,
                'user_id': user.id
            }
            if role == 'creator':
                response_data['profile_completed'] = user.profile_completed
            return jsonify(response_data), 200
        return jsonify({'msg': 'Invalid credentials'}), 401
    except Exception as e:
        print(f"Login error: {str(e)}")
        return jsonify({'msg': 'Login failed', 'error': str(e)}), 500

@app.route('/api/brand/campaigns', methods=['POST'])
@jwt_required()
def create_campaign():
    claims = get_jwt()
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())
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
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())
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
                'view_threshold': c['view_threshold']
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
        # this sends only 50 rows over the network.
        response = supabase.table('campaign')\
            .select('*')\
            .eq('is_active', True)\
            .execute()
            
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
                    'view_threshold': c['view_threshold']
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
            'id': campaign_data['id'],
            'name': campaign_data['name'],
            'platform': campaign_data['platform'],
            'image_url': campaign_data['image_url'],
            'budget': campaign_data['budget'],
            'cpv': campaign_data['cpv'],
            'hashtag': campaign_data['hashtag'],
            'audio': campaign_data['audio'],
            'deadline': campaign_data['deadline'],
            'brand_id': campaign_data['brand_id'],
            'is_active': campaign_data['is_active'],
            'asset_link': campaign_data['asset_link'],
            'category': campaign_data['category'],
            'total_view_count': campaign_data['total_view_count'],
            'requirements': campaign_data['requirements'],
            'view_threshold': campaign_data['view_threshold'],
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
    if claims.get('role') != 'creator':
        print(f"Unauthorized access to creator campaigns. Role in token: {claims.get('role')}") # Debugging line
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = int(get_jwt_identity())
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
    if claims.get('role') != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = int(get_jwt_identity())
    try:
        data = request.json
        required_fields = ['campaign_id', 'clip_url']
        if not all(k in data for k in required_fields):
            return jsonify({'msg': 'Missing required fields'}), 400

        campaign_id = data['campaign_id']
        clip_url = data['clip_url']

        # Check if the campaign exists and is active
        campaign_response = supabase.table('campaign').select('id, is_active').eq('id', campaign_id).eq('is_active', True).limit(1).execute()
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not active'}), 404

        # Check if creator has already submitted the maximum allowed clips (5) for this campaign
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
        response = supabase.table('submitted_clips').insert([new_clip]).execute()

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
    if claims.get('role') != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = int(get_jwt_identity())
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
    if claims.get('role') != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = int(get_jwt_identity())

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
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())

    try:
        # 1. Fetch Details BEFORE deleting (Need image_url AND funds_allocated)
        campaign_response = supabase.table('campaign').select('id, image_url, funds_allocated').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404
            
        campaign_data = campaign_response.data[0]
        image_url = campaign_data.get('image_url')
        funds_remaining = campaign_data.get('funds_allocated', 0)
        refund_msg = ""

        # 2. THE SAFE DELETE CHECK (Refund Logic)
        if funds_remaining > 0:
            # Refund the money to the brand's wallet
            # We can use a direct RPC call or manual update. 
            # Assuming you want to keep it simple and consistent:
            rpc_res = supabase.rpc('reclaim_budget', {
                'p_brand_id': brand_id, 
                'p_campaign_id': campaign_id, 
                'p_amount': float(funds_remaining)
            }).execute()
            
            refund_msg = f" Refunded ₹{funds_remaining} to wallet."
            print(f"[Safe Delete] {refund_msg}")

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
    if not claims or claims.get('role') != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = int(get_jwt_identity())

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
    if claims.get('role') != 'admin':
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
    if claims.get('role') != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    try:
        data = request.json
        status = data.get('status')

        if status not in ['accepted', 'rejected']:
            return jsonify({'msg': 'Invalid status'}), 400
        
        # Fetch the submitted clip
        submitted_clip_response = supabase.table('submitted_clips').select('*').eq('id', clip_id).limit(1).execute()
        submitted_clip_data = submitted_clip_response.data[0] if submitted_clip_response.data else None

        if not submitted_clip_data:
            return jsonify({'msg': 'Clip not found'}), 404

        if status == 'accepted':
            # Insert into accepted_clips table
            new_accepted_clip = {
                'id': submitted_clip_data['id'], # Use same ID as submitted clip
                'creator_id': submitted_clip_data['creator_id'],
                'campaign_id': submitted_clip_data['campaign_id'],
                'clip_url': submitted_clip_data['clip_url'],
                'submitted_at': submitted_clip_data['submitted_at'], # Original submission timestamp
                'media_id': None,
                'view_count': None,
                'caption': None,
                'instagram_posted_at': None,
            }
            supabase.table('accepted_clips').insert([new_accepted_clip]).execute()

            # Delete from submitted_clips table
            supabase.table('submitted_clips').delete().eq('id', clip_id).execute()
            return jsonify({'msg': 'Clip accepted and moved to accepted_clips'}), 200

        elif status == 'rejected':
            # Update submitted_clips to mark as rejected by admin and add feedback
            update_fields = {
                'is_deleted_by_admin': True,
                'feedback': data.get('feedback') # Use feedback from request
            }
            supabase.table('submitted_clips').update(update_fields).eq('id', clip_id).execute()

            # If the clip was previously accepted, delete it from accepted_clips table
            # This handles cases where an accepted clip is later rejected (e.g., if brand finds an issue after acceptance)
            supabase.table('accepted_clips').delete().eq('id', clip_id).execute()
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
    if not claims or claims.get('role') != 'admin':
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
    if claims.get('role') != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = int(get_jwt_identity())
    try:
        # Select only the relevant profile fields
        response = supabase.table('creator').select(
            'id, username, email, profile_completed, phone, nickname, bio, join_date' # Changed phone_number to phone, added join_date
        ).eq('id', creator_id).limit(1).execute()
        creator_data = response.data[0] if response.data else None

        if creator_data:
            return jsonify({
                'id': creator_data['id'],
                'username': creator_data['username'],
                'email': creator_data['email'],
                'profile_completed': creator_data.get('profile_completed'),
                'phone': creator_data.get('phone'), # Changed phone_number to phone
                'nickname': creator_data.get('nickname'),
                'bio': creator_data.get('bio'),
                'join_date': creator_data.get('join_date') # Added join_date
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
    if claims.get('role') != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    creator_id = int(get_jwt_identity())
    try:
        data = request.json
        update_fields = {}
        # Only update fields relevant to initial profile completion
        if 'phone' in data: update_fields['phone'] = data['phone'] # Changed phone_number to phone
        if 'nickname' in data: update_fields['nickname'] = data['nickname']
        if 'bio' in data: update_fields['bio'] = data['bio']

        # Mark profile as completed after successful update
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
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())

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
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())

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
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())

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
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())

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
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())

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
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    brand_id = int(get_jwt_identity())

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

if __name__ == '__main__':
    # Start the Flask app with debug mode and auto-reloader
    app.register_blueprint(payments_bp, url_prefix='/api/payments')
    app.run(debug=True, port=5000, use_reloader=True)