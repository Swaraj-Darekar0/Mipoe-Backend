import os
import requests
import json
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from supabase import create_client, Client
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()

# 2. Initialize Blueprint (MUST BE DEFINED BEFORE ROUTES)
payments_bp = Blueprint('payments', __name__)

# 3. Initialize Clients
# Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Cashfree Config
CASHFREE_APP_ID = os.getenv('CASHFREE_APP_ID')
CASHFREE_SECRET_KEY = os.getenv('CASHFREE_SECRET_KEY')
CASHFREE_BASE_URL = os.getenv('CASHFREE_API_URL', 'https://sandbox.cashfree.com/pg')

def get_cashfree_headers():
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "x-api-version": "2023-08-01",
        "x-client-id": CASHFREE_APP_ID,
        "x-client-secret": CASHFREE_SECRET_KEY
    }

# ==========================================
# SECTION A: CASHFREE DEPOSITS (Money In)
# ==========================================

# --- ROUTE 1: CREATE DEPOSIT SESSION ---
@payments_bp.route('/create-deposit-order', methods=['POST'])
@jwt_required()
def create_deposit_order():
    claims = get_jwt()
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = int(get_jwt_identity())
    data = request.json
    amount = data.get('amount')

    if not amount or amount < 1:
        return jsonify({'msg': 'Invalid amount'}), 400

    try:
        # 1. Fetch Brand Details
        brand_res = supabase.table('brand').select('email, username, phone').eq('id', brand_id).single().execute()
        brand_data = brand_res.data
        
        if not brand_data:
            return jsonify({'msg': 'Brand not found'}), 404

        # 2. Prepare Cashfree Order
        order_id = f"order_{brand_id}_{os.urandom(4).hex()}"
        customer_phone = brand_data.get('phone') or "9999999999"
        
        payload = {
            "order_amount": float(amount),
            "order_currency": "INR",
            "order_id": order_id,
            "customer_details": {
                "customer_id": str(brand_id),
                "customer_name": brand_data.get('username', 'Brand User'),
                "customer_email": brand_data.get('email'),
                "customer_phone": customer_phone
            },
            "order_meta": {
                "return_url": f"http://localhost:5173/brand/dashboard?order_id={order_id}"
            }
        }

        # 3. Call Cashfree API
        response = requests.post(
            f"{CASHFREE_BASE_URL}/orders",
            headers=get_cashfree_headers(),
            json=payload
        )
        
        resp_data = response.json()

        if response.status_code == 200:
            return jsonify({
                'payment_session_id': resp_data.get('payment_session_id'),
                'order_id': order_id
            }), 200
        else:
            print(f"[Cashfree Error]: {resp_data}")
            return jsonify({'msg': 'Failed to create order', 'error': resp_data.get('message')}), 400

    except Exception as e:
        print(f"[Server Error]: {str(e)}")
        return jsonify({'msg': 'Internal server error', 'error': str(e)}), 500


# --- ROUTE 2: VERIFY DEPOSIT (Server-to-Server) ---
@payments_bp.route('/verify-deposit', methods=['POST'])
@jwt_required()
def verify_deposit():
    claims = get_jwt()
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = int(get_jwt_identity())
    data = request.json
    order_id = data.get('order_id')

    if not order_id:
        return jsonify({'msg': 'Missing order_id'}), 400

    try:
        # 1. Check Status from Cashfree
        response = requests.get(
            f"{CASHFREE_BASE_URL}/orders/{order_id}",
            headers=get_cashfree_headers()
        )
        
        if response.status_code != 200:
            return jsonify({'msg': 'Could not fetch order status'}), 400

        cf_data = response.json()
        if cf_data.get('order_status') == 'PAID':
            
            # 2. Idempotency Check
            txn_check = supabase.table('transactions').select('id').eq('external_txn_id', order_id).execute()
            if txn_check.data:
                return jsonify({'msg': 'Order already processed', 'status': 'PAID'}), 200

            amount_paid = cf_data.get('order_amount')

            # 3. Update Wallet
            brand_res = supabase.table('brand').select('wallet_balance').eq('id', brand_id).single().execute()
            current_balance = brand_res.data['wallet_balance'] or 0.0
            new_balance = current_balance + float(amount_paid)

            supabase.table('brand').update({'wallet_balance': new_balance}).eq('id', brand_id).execute()

            # 4. Log Transaction
            transaction_entry = {
                'user_type': 'brand',
                'user_id': brand_id,
                'amount': float(amount_paid),
                'type': 'deposit',
                'status': 'success',
                'external_txn_id': order_id,
                'description': f'Deposit via Cashfree Order {order_id}'
            }
            supabase.table('transactions').insert([transaction_entry]).execute()

            return jsonify({'msg': 'Deposit verified', 'new_balance': new_balance}), 200
        
        return jsonify({'msg': 'Payment not completed', 'status': cf_data.get('order_status')}), 400

    except Exception as e:
        print(f"[Verification Error]: {str(e)}")
        return jsonify({'msg': 'Verification failed', 'error': str(e)}), 500


# --- ROUTE 3: GET/CREATE VIRTUAL ACCOUNT (Auto Collect) ---
@payments_bp.route('/virtual-account', methods=['GET'])
@jwt_required()
def get_virtual_account():
    claims = get_jwt()
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = int(get_jwt_identity())

    try:
        # 1. Check if VA exists in DB
        res = supabase.table('brand').select('virtual_acc_number, virtual_acc_ifsc, virtual_vpa_id, email, phone, username').eq('id', brand_id).single().execute()
        brand_data = res.data

        if brand_data.get('virtual_acc_number'):
            return jsonify({
                'account_number': brand_data['virtual_acc_number'],
                'ifsc': brand_data['virtual_acc_ifsc'],
                'vpa_id': brand_data['virtual_vpa_id']
            }), 200

        # 2. Create New VA via Cashfree (MOCK FOR NOW - Requires separate CA Keys)
        v_id = f"KIPP{brand_id}"
        new_acc_num = v_id
        new_ifsc = "UTIB0CCH274"
        new_vpa = f"{v_id}@cfree"

        # 3. Save to DB
        supabase.table('brand').update({
            'virtual_acc_number': new_acc_num,
            'virtual_acc_ifsc': new_ifsc,
            'virtual_vpa_id': new_vpa
        }).eq('id', brand_id).execute()

        return jsonify({
            'account_number': new_acc_num,
            'ifsc': new_ifsc,
            'vpa_id': new_vpa
        }), 200

    except Exception as e:
        print(f"[VA Error]: {str(e)}")
        return jsonify({'msg': 'Failed to fetch virtual account'}), 500


# ==========================================
# SECTION B: ENVELOPE SYSTEM (Wallet Logic)
# ==========================================

# --- ROUTE 4: GET WALLET BALANCE ---
@payments_bp.route('/wallet-balance', methods=['GET'])
@jwt_required()
def get_wallet_balance():
    claims = get_jwt()
    role = claims.get('role')
    user_id = int(get_jwt_identity())

    try:
        table = 'brand' if role == 'brand' else 'creator'
        res = supabase.table(table).select('wallet_balance').eq('id', user_id).limit(1).execute()
        
        if not res.data:
            return jsonify({'msg': 'User not found'}), 404
            
        balance = res.data[0]['wallet_balance'] or 0.0
        
        return jsonify({'role': role, 'balance': balance, 'currency': 'INR'}), 200

    except Exception as e:
        return jsonify({'msg': 'Failed to fetch balance'}), 500


# --- ROUTE 5: ALLOCATE FUNDS ---
@payments_bp.route('/allocate-budget', methods=['POST'])
@jwt_required()
def allocate_budget():
    claims = get_jwt()
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = int(get_jwt_identity())
    data = request.json
    campaign_id = data.get('campaign_id')
    amount = data.get('amount')

    if not campaign_id or not amount or amount <= 0:
        return jsonify({'msg': 'Invalid parameters'}), 400

    try:
        # 1. Move the Money (RPC)
        res = supabase.rpc('allocate_budget', {
            'p_brand_id': brand_id, 
            'p_campaign_id': campaign_id, 
            'p_amount': float(amount)
        }).execute()

        # 2. [NEW] Activate Campaign automatically
        # Since we just added money, the campaign is now valid to run.
        supabase.table('campaigns').update({'is_active': True}).eq('id', campaign_id).execute()

        return jsonify({'msg': 'Funds allocated and Campaign Activated'}), 200

    except Exception as e:
        print(f"[Allocation Error]: {str(e)}")
        return jsonify({'msg': 'Allocation failed', 'error': str(e)}), 400

# --- ROUTE 6: RECLAIM FUNDS ---
# --- ROUTE 6: RECLAIM FUNDS ---
@payments_bp.route('/reclaim-budget', methods=['POST'])
@jwt_required()
def reclaim_budget():
    claims = get_jwt()
    if claims.get('role') != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = int(get_jwt_identity())
    data = request.json
    campaign_id = data.get('campaign_id')
    amount = data.get('amount')

    if not campaign_id or not amount or amount <= 0:
        return jsonify({'msg': 'Invalid parameters'}), 400

    try:
        # 1. Move the Money (RPC)
        res = supabase.rpc('reclaim_budget', {
            'p_brand_id': brand_id, 
            'p_campaign_id': campaign_id, 
            'p_amount': float(amount)
        }).execute()

        # 2. [NEW] Auto-Deactivate if funds hit 0
        # Fetch the new balance for this campaign
        camp_res = supabase.table('campaigns').select('funds_allocated').eq('id', campaign_id).single().execute()
        
        if camp_res.data:
            remaining_funds = camp_res.data.get('funds_allocated', 0)
            
            if remaining_funds <= 0:
                supabase.table('campaigns').update({'is_active': False}).eq('id', campaign_id).execute()
                return jsonify({'msg': 'Funds reclaimed. Campaign Deactivated (Zero Balance).'}), 200

        return jsonify({'msg': 'Funds reclaimed successfully'}), 200

    except Exception as e:
        print(f"[Reclaim Error]: {str(e)}")
        return jsonify({'msg': 'Reclaim failed', 'error': str(e)}), 400