
import os
import requests
import json
import time
from datetime import datetime
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from supabase import create_client, Client
from dotenv import load_dotenv
import traceback
import hmac
import base64
import hashlib

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
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
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
                "return_url": f"http://localhost:8080/brand/dashboard?order_id={order_id}"
            }
        }

        # 3. Call Cashfree API
        response = requests.post(
            f"{CASHFREE_BASE_URL}/orders",
            headers=get_cashfree_headers(),
            json=payload
        )
        
        try:
            resp_data = response.json()
        except ValueError:
            print(f"[Cashfree Critical Error] Status: {response.status_code}")
            print(f"[Cashfree Raw Body]: {response.text}")
            return jsonify({'msg': 'Payment Gateway Error', 'details': 'Invalid response from Cashfree'}), 502


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
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
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
            
            # 2. Idempotency Check on the correct table
            txn_check = supabase.table('brand_transactions').select('id').eq('external_txn_id', order_id).limit(1).execute()
            if txn_check.data:
                return jsonify({'msg': 'Order already processed', 'status': 'PAID'}), 200

            amount_paid = cf_data.get('order_amount')

            # 3. Update Wallet
            brand_res = supabase.table('brand').select('wallet_balance').eq('id', brand_id).single().execute()
            current_balance = brand_res.data['wallet_balance'] or 0.0
            new_balance = current_balance + float(amount_paid)

            update_response = supabase.table('brand').update({'wallet_balance': new_balance}).eq('id', brand_id).execute()

            # Verify that the update was successful
            if not update_response.data:
                print(f"[DB Update Error] Failed to update wallet for brand {brand_id}.")
                # Note: At this point, the deposit is verified but DB update failed.
                # This requires a reconciliation process or more robust error handling.
                # For now, we will return an error.
                return jsonify({'msg': 'Database update failed after payment verification.'}), 500

            # 4. Log Transaction 
            transaction_entry = {
                'brand_id': brand_id,
                'amount': float(amount_paid),
                'type': 'deposit',
                'status': 'success',
                'external_txn_id': order_id,
                'description': f'Deposit via Cashfree Order {order_id}'
            }
            supabase.table('brand_transactions').insert([transaction_entry]).execute()

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
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()

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
    user_id = get_jwt_identity()

    

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
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
    data = request.json
    campaign_id = data.get('campaign_id')
    amount = data.get('amount')

    if not campaign_id or not amount or amount <= 0:
        return jsonify({'msg': 'Missing or invalid campaign_id or amount'}), 400

    try:
        # 1. Check if campaign exists and belongs to the brand
        campaign_response = supabase.table('campaign').select('id, funds_allocated, budget').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404
        
        # 2. Check wallet balance
        brand_response = supabase.table('brand').select('wallet_balance').eq('id', brand_id).limit(1).execute()
        
        if not brand_response.data:
            return jsonify({'msg': 'Brand not found'}), 404
        
        current_balance = brand_response.data[0].get('wallet_balance', 0)
        
        if current_balance < amount:
            return jsonify({'msg': 'Insufficient wallet balance'}), 400
        
        # 3. Deduct from wallet and add to campaign
        new_balance = current_balance - amount
        current_allocated = campaign_response.data[0].get('funds_allocated', 0)
        current_budget = campaign_response.data[0].get('budget', 0)
        new_allocated = current_allocated + amount
        new_budget = current_budget + amount  # Budget is cumulative (total allocated)
        
        # Update brand wallet
        supabase.table('brand').update({'wallet_balance': new_balance}).eq('id', brand_id).execute()
        
        # Update campaign funds (both funds_allocated and budget)
        supabase.table('campaign').update({
            'funds_allocated': new_allocated,
            'budget': new_budget
        }).eq('id', campaign_id).execute()
        
        # 4. Log transaction
        transaction_data = {
            'brand_id': brand_id,
            'campaign_id': campaign_id,
            'amount': amount,
            'type': 'allocation',
            'status': 'success',
            'description': f'Allocated ₹{amount} to campaign {campaign_id}'
        }
        print(f"DEBUG: Inserting transaction with campaign_id: {campaign_id} for type: {transaction_data['type']}")
        supabase.table('brand_transactions').insert([transaction_data]).execute()
        
        print(f"[Allocation] Brand {brand_id} allocated ₹{amount} to campaign {campaign_id}")
        
        # Return updated values
        return jsonify({
            'msg': 'Funds allocated successfully',
            'allocated_amount': amount,
            'new_wallet_balance': new_balance,
            'new_funds_allocated': new_allocated,
            'new_budget': new_budget,
            'campaign_id': campaign_id
        }), 200

    except Exception as e:
        print(f"Allocate budget error: {str(e)}")
        return jsonify({'msg': 'Allocation failed', 'error': str(e)}), 500


# --- ROUTE 6: RECLAIM FUNDS ---
@payments_bp.route('/reclaim-budget', methods=['POST'])
@jwt_required()
def reclaim_budget():
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
    data = request.json
    campaign_id = data.get('campaign_id')
    amount = data.get('amount')

    if not campaign_id or not amount or amount <= 0:
        return jsonify({'msg': 'Missing or invalid campaign_id or amount'}), 400

    try:
        # 1. Check if campaign exists and belongs to the brand
        campaign_response = supabase.table('campaign').select('id, funds_allocated, funds_distributed, budget').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404
        
        campaign_data = campaign_response.data[0]
        current_allocated = campaign_data.get('funds_allocated', 0)
        current_distributed = campaign_data.get('funds_distributed', 0)
        current_budget = campaign_data.get('budget', 0)
        
        # 2. Check if there are enough unallocated funds (allocated - distributed)
        reclaimable = current_allocated - current_distributed
        
        if reclaimable < amount:
            return jsonify({
                'msg': f'Cannot reclaim ₹{amount}. Only ₹{reclaimable} available (allocated: ₹{current_allocated}, distributed: ₹{current_distributed})'
            }), 400
        
        # 3. Get current wallet balance
        brand_response = supabase.table('brand').select('wallet_balance').eq('id', brand_id).limit(1).execute()
        
        if not brand_response.data:
            return jsonify({'msg': 'Brand not found'}), 404
        
        current_balance = brand_response.data[0].get('wallet_balance', 0)
        
        # 4. Return funds to wallet and deduct from campaign
        new_balance = current_balance + amount
        new_allocated = current_allocated - amount
        new_budget = current_budget - amount # Also decrement the total budget
        
        # Update brand wallet
        supabase.table('brand').update({'wallet_balance': new_balance}).eq('id', brand_id).execute()
        
        # Update campaign funds (both funds_allocated and budget)
        supabase.table('campaign').update({
            'funds_allocated': new_allocated,
            'budget': new_budget
        }).eq('id', campaign_id).execute()
        
        # Log transaction
        transaction_data = {
            'brand_id': brand_id,
            'campaign_id': campaign_id,
            'amount': amount,
            'type': 'reclaim',
            'status': 'success',
            'description': f'Reclaimed ₹{amount} from campaign {campaign_id}'
        }
        print(f"DEBUG: Inserting transaction with campaign_id: {campaign_id} for type: {transaction_data['type']}")
        supabase.table('brand_transactions').insert([transaction_data]).execute()
        
        print(f"[Reclaim] Brand {brand_id} reclaimed ₹{amount} from campaign {campaign_id}")
        
        # Return updated values
        return jsonify({
            'msg': 'Funds reclaimed successfully',
            'reclaimed_amount': amount,
            'new_wallet_balance': new_balance,
            'new_funds_allocated': new_allocated,
            'campaign_id': campaign_id
        }), 200

    except Exception as e:
        print(f"Reclaim budget error: {str(e)}")
        return jsonify({'msg': 'Reclaim failed', 'error': str(e)}), 500


# --- ROUTE 7: DISTRIBUTE FUNDS TO CREATOR (Phase 4) ---
@payments_bp.route('/distribute-to-creator', methods=['POST'])
@jwt_required()
def distribute_to_creator():
    """
    Distribute earnings to a creator when view threshold is reached.
    Body: {
        "campaign_id": int,
        "creator_id": int,
        "view_count": int,  // total views achieved
        "cpv": float,       // cost per view (from campaign)
        "view_threshold": int  // target threshold
    }
    
    Logic:
    - Calculate earnings: (view_count / view_threshold) * cpv
    - Platform commission (10%), Creator share (90%)
    - Deduct from campaign.funds_allocated
    - Add creator_share to creator.wallet_balance
    - Increment campaign.funds_distributed
    - Log both transactions
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
    data = request.json
    campaign_id = data.get('campaign_id')
    creator_id = data.get('creator_id')
    view_count = data.get('view_count')
    cpv = data.get('cpv')
    view_threshold = data.get('view_threshold')

    if not all([campaign_id, creator_id, view_count, cpv, view_threshold]):
        return jsonify({'msg': 'Missing required fields'}), 400

    try:
        # 1. Verify campaign belongs to brand
        campaign_response = supabase.table('campaign').select('id, funds_allocated, funds_distributed, budget').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404
        
        campaign = campaign_response.data[0]
        current_allocated = campaign.get('funds_allocated', 0)
        current_distributed = campaign.get('funds_distributed', 0)
        
        # 2. Calculate earnings
        earnings = (view_count / view_threshold) * cpv
        
        # 3. Check if sufficient funds available
        available = current_allocated - current_distributed
        if available < earnings:
            return jsonify({
                'msg': f'Insufficient funds. Required: ₹{earnings:.2f}, Available: ₹{available:.2f}',
                'required': earnings,
                'available': available
            }), 400
        
        # 4. Calculate split (90% to creator, 10% platform commission)
        creator_share = earnings * 0.9
        platform_commission = earnings * 0.1
        
        # 5. Update creator wallet
        creator_response = supabase.table('creator').select('id, wallet_balance').eq('id', creator_id).limit(1).execute()
        
        if not creator_response.data:
            return jsonify({'msg': 'Creator not found'}), 404
        
        creator_wallet = creator_response.data[0].get('wallet_balance', 0)
        new_creator_wallet = creator_wallet + creator_share
        
        supabase.table('creator').update({'wallet_balance': new_creator_wallet}).eq('id', creator_id).execute()
        
        # 6. Update campaign (deduct distributed, increment funds_distributed)
        new_distributed = current_distributed + earnings
        
        supabase.table('campaign').update({'funds_distributed': new_distributed}).eq('id', campaign_id).execute()
        
        # 7. Log creator earning transaction
        creator_txn = {
            'creator_id': creator_id,
            'campaign_id': campaign_id,
            'amount': creator_share,
            'type': 'earning',
            'status': 'success',
            'description': f'Earned ₹{creator_share:.2f} from {view_count} views on campaign {campaign_id}'
        }
        print(f"DEBUG: Inserting transaction with campaign_id: {campaign_id} for type: {creator_txn['type']}")
        supabase.table('creator_transactions').insert([creator_txn]).execute()
        
        # 8. Log platform commission transaction
        commission_txn = {
            'source_brand_id': brand_id,
            'campaign_id': campaign_id,
            'amount': platform_commission,
            'type': 'commission',
            'status': 'success',
            'description': f'Platform commission (10%) from creator earnings on campaign {campaign_id}'
        }
        print(f"DEBUG: Inserting transaction with campaign_id: {campaign_id} for type: {commission_txn['type']}")
        supabase.table('platform_transactions').insert([commission_txn]).execute()
        
        print(f"[Distribution] Campaign {campaign_id}: Creator {creator_id} earned ₹{creator_share:.2f}, Platform earned ₹{platform_commission:.2f}")
        
        return jsonify({
            'msg': 'Distribution successful',
            'campaign_id': campaign_id,
            'creator_id': creator_id,
            'total_earnings': earnings,
            'creator_share': creator_share,
            'platform_commission': platform_commission,
            'view_count': view_count,
            'new_creator_wallet': new_creator_wallet,
            'new_funds_distributed': new_distributed
        }), 200

    except Exception as e:
        print(f"Distribution error: {str(e)}")
        return jsonify({'msg': 'Distribution failed', 'error': str(e)}), 500


# --- ROUTE 8: CREATOR WITHDRAWAL (Phase 5) ---
@payments_bp.route('/creator-withdraw', methods=['POST'])
@jwt_required()
def creator_withdraw():
    """
    Creator withdraws earned funds to bank account or UPI.
    Body: {
        "amount": float,
        "payout_method": "upi" | "bank",
        "upi_id": string (if upi),
        "bank_account": string (if bank),
        "ifsc": string (if bank)
    }
    
    Process:
    1. Validate creator has sufficient balance
    2. Deduct from creator.wallet_balance (immediate)
    3. Call Cashfree Payout API
    4. Log transaction with external_txn_id
    5. Return payout reference
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    creator_id = get_jwt_identity()
    data = request.json
    print(f"[/api/payments/creator-withdraw] Received withdrawal request data: {data}")
    amount = data.get('amount')
    payout_method = data.get('payout_method')  # 'upi' or 'bank'
    upi_id = data.get('upi_id')
    bank_account = data.get('bank_account')
    ifsc = data.get('ifsc')

    if not amount or amount <= 0:
        return jsonify({'msg': 'Invalid amount'}), 400
    
    if payout_method not in ['upi', 'bank']:
        return jsonify({'msg': 'Invalid payout method. Use upi or bank'}), 400
    
    if payout_method == 'upi' and not upi_id:
        return jsonify({'msg': 'UPI ID required for UPI withdrawal'}), 400
    
    if payout_method == 'bank' and not (bank_account and ifsc):
        return jsonify({'msg': 'Bank account and IFSC required for bank withdrawal'}), 400

    try:
        # 1. Check creator wallet balance
        creator_response = supabase.table('creator').select('id, wallet_balance').eq('id', creator_id).limit(1).execute()
        
        if not creator_response.data:
            return jsonify({'msg': 'Creator not found'}), 404
        
        current_balance = creator_response.data[0].get('wallet_balance', 0)
        
        if current_balance < amount:
            return jsonify({
                'msg': f'Insufficient balance. Available: ₹{current_balance:.2f}',
                'available_balance': current_balance
            }), 400
        
        # 2. Deduct from wallet (immediate)
        new_balance = current_balance - amount
        supabase.table('creator').update({'wallet_balance': new_balance}).eq('id', creator_id).execute()
        
        # 3. Call Cashfree Payout API
        # Phase 5: Actual Cashfree Payout Integration
        payout_method_map = 'NEFT' if payout_method == 'bank' else 'UPI'
        
        try:
            cashfree_app_id = os.getenv('CASHFREE_APP_ID')
            cashfree_secret_key = os.getenv('CASHFREE_SECRET_KEY')
            cashfree_api_url = os.getenv('CASHFREE_API_URL')

            print(f"[Cashfree Debug] APP_ID set: {cashfree_app_id is not None}, SECRET_KEY set: {cashfree_secret_key is not None}, API_URL set: {cashfree_api_url is not None}")
            
            if not all([cashfree_app_id, cashfree_secret_key, cashfree_api_url]):
                print("[Cashfree Error] Missing Cashfree environment variables! Check .env file.")
                supabase.table('creator').update({'wallet_balance': current_balance}).eq('id', creator_id).execute() # Refund
                return jsonify({'msg': 'Cashfree environment variables not configured on server', 'status': 'failed'}), 500

            cashfree_payout_payload = {
                'beneId': f'CREATOR_{creator_id}',
                'amount': float(amount),
                'transferMode': payout_method_map,
                'remarks': f'Creator withdrawal - Campaign payout'
            }
            
            if payout_method == 'bank':
                cashfree_payout_payload['bankAccount'] = bank_account
                cashfree_payout_payload['ifsc'] = ifsc
            else:
                cashfree_payout_payload['vpa'] = upi_id
            
            # Make Cashfree API call
            cashfree_headers = {
                'X-Client-Id': cashfree_app_id,
                'X-Client-Secret': cashfree_secret_key,
                'Content-Type': 'application/json'
            }
            
            payout_api_url = "https://sandbox.cashfree.com/payouts/v1/requestTransfer"
            print(f"[Cashfree Debug] Payout API URL: {payout_api_url}")
            print(f"[Cashfree Debug] Payout Payload: {json.dumps(cashfree_payout_payload)}")

            payout_response = requests.post(payout_api_url, json=cashfree_payout_payload, headers=cashfree_headers)
            
            if payout_response.status_code == 200:
                cashfree_response = payout_response.json()
                external_txn_id = cashfree_response.get('referenceId', f'PAYOUT_{creator_id}_{int(time.time())}')
                utr_number = cashfree_response.get('utrNumber', '')
                payout_status = 'pending'  # Set initial status to pending
                payout_msg = 'Withdrawal successfully initiated and is now pending.'
            else:
                print(f"[Cashfree API Error] Status: {payout_response.status_code}, Response: {payout_response.text}") # Log full API error
                # Refund the amount if payout fails
                supabase.table('creator').update({'wallet_balance': current_balance}).eq('id', creator_id).execute()
                return jsonify({
                    'msg': 'Payout API failed',
                    'error': payout_response.text,
                    'status': 'failed'
                }), 500
        except Exception as cashfree_error:
            # Refund the amount if API call fails
            supabase.table('creator').update({'wallet_balance': current_balance}).eq('id', creator_id).execute()
            print(f"[Cashfree Error] Unexpected error during payout API call: {str(cashfree_error)}")
            traceback.print_exc() # Print full traceback
            return jsonify({
                'msg': 'Payout processing failed',
                'error': str(cashfree_error),
                'status': 'failed'
            }), 500
        
        # 4. Log payout transaction
        payout_txn = {
            'creator_id': creator_id,
            'amount': amount,
            'type': 'withdrawal',
            'status': payout_status,
            'external_txn_id': external_txn_id,
            'description': f'Withdrawal to {payout_method.upper()}: {upi_id or bank_account}. UTR: {utr_number if payout_status == "success" else "Pending"}',
            'payout_method': payout_method,
            'utr': utr_number if payout_status == 'success' else None
        }
        supabase.table('creator_transactions').insert([payout_txn]).execute()
        
        # --- NOTIFICATION FOR WITHDRAWAL ---
        notification_message = f"Successfully initiated withdrawal of ₹{amount:.2f} to your {payout_method.upper()} account."
        notification_data = {
            "message": notification_message,
            "type": "withdrawal_initiated",
            "amount": amount,
            "payout_method": payout_method,
            "timestamp": datetime.utcnow().isoformat()
        }
        supabase.rpc('append_notification', {'p_creator_id': creator_id, 'p_new_notification': notification_data}).execute()
        
        print(f"[Withdrawal] Creator {creator_id} withdrew ₹{amount} via {payout_method}. Ref: {external_txn_id}")
        
        return jsonify({
            'msg': payout_msg,
            'amount': amount,
            'new_balance': new_balance,
            'payout_method': payout_method,
            'reference_id': external_txn_id,
            'utr': utr_number if payout_status == 'success' else '',
            'status': payout_status
        }), 200

    except Exception as e:
        print(f"Withdrawal error: {str(e)}")
        return jsonify({'msg': 'Withdrawal failed', 'error': str(e)}), 500


# --- ROUTE 15: SAVE CREATOR PAYOUT DETAILS (Phase 5 - Prerequisite) ---
@payments_bp.route('/creator/payout-details', methods=['POST', 'PUT'])
@jwt_required()
def save_payout_details():
    """
    Save or update creator's payout details (bank account or UPI).
    Creators must save these before they can withdraw.
    
    Body: {
        "payout_method": "upi" | "bank",
        "upi_id": string (if upi),
        "bank_account": string (if bank),
        "ifsc": string (if bank),
        "account_holder_name": string (if bank)
    }
    
    Response: Saved payout details (without sensitive bank info fully masked)
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    creator_id = get_jwt_identity()
    data = request.json
    payout_method = data.get('payout_method')
    
    if payout_method not in ['upi', 'bank']:
        return jsonify({'msg': 'Invalid payout method. Use upi or bank'}), 400
    
    try:
        if payout_method == 'upi':
            upi_id = data.get('upi_id')
            if not upi_id or '@' not in upi_id:
                return jsonify({'msg': 'Invalid UPI ID format'}), 400
            
            # Save to creator table
            supabase.table('creator').update({
                'payout_method': 'upi',
                'upi_id': upi_id,
                'bank_account': None,
                'ifsc': None,
                'account_holder_name': None
            }).eq('id', creator_id).execute()
            
            print(f"[Payout Details] Creator {creator_id} saved UPI: {upi_id}")
            
            return jsonify({
                'msg': 'UPI details saved successfully',
                'payout_method': 'upi',
                'upi_id': upi_id
            }), 200
        
        else:  # bank
            bank_account = data.get('bank_account')
            ifsc = data.get('ifsc')
            account_holder_name = data.get('account_holder_name')
            
            if not bank_account or not ifsc or len(bank_account) < 9:
                return jsonify({'msg': 'Invalid bank account or IFSC'}), 400
            
            # Save to creator table
            supabase.table('creator').update({
                'payout_method': 'bank',
                'bank_account': bank_account,
                'ifsc': ifsc,
                'account_holder_name': account_holder_name,
                'upi_id': None
            }).eq('id', creator_id).execute()
            
            # Mask account for response
            masked_account = bank_account[-4:].rjust(len(bank_account), '*')
            
            print(f"[Payout Details] Creator {creator_id} saved Bank: {masked_account}")
            
            return jsonify({
                'msg': 'Bank account details saved successfully',
                'payout_method': 'bank',
                'bank_account': masked_account,
                'ifsc': ifsc,
                'account_holder_name': account_holder_name
            }), 200

    except Exception as e:
        print(f"Save payout details error: {str(e)}")
        return jsonify({'msg': 'Failed to save payout details', 'error': str(e)}), 500


# --- ROUTE 16: GET CREATOR PAYOUT DETAILS (Phase 5 - Retrieve) ---
@payments_bp.route('/creator/payout-details', methods=['GET'])
@jwt_required()
def get_payout_details():
    """
    Get creator's saved payout details (for verification before withdrawal).
    Masks sensitive information.
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    creator_id = get_jwt_identity()
    
    try:
        creator_response = supabase.table('creator').select('id, payout_method, upi_id, bank_account, ifsc, account_holder_name').eq('id', creator_id).limit(1).execute()
        
        if not creator_response.data:
            return jsonify({'msg': 'Creator not found'}), 404
        
        creator = creator_response.data[0]
        payout_method = creator.get('payout_method')
        
        if not payout_method:
            return jsonify({
                'msg': 'No payout details saved',
                'payout_method': None
            }), 200
        
        response_data = {
            'msg': 'Payout details retrieved successfully',
            'payout_method': payout_method
        }
        
        if payout_method == 'upi':
            response_data['upi_id'] = creator.get('upi_id')
        else:
            bank_account = creator.get('bank_account', '')
            response_data['bank_account'] = bank_account[-4:].rjust(len(bank_account), '*')
            response_data['ifsc'] = creator.get('ifsc')
            response_data['account_holder_name'] = creator.get('account_holder_name')
        
        return jsonify(response_data), 200

    except Exception as e:
        print(f"Get payout details error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve payout details', 'error': str(e)}), 500


# --- ROUTE 17: VERIFY PAYOUT DETAILS (Phase 5 - Validation) ---
@payments_bp.route('/creator/verify-payout-details', methods=['POST'])
@jwt_required()
def verify_payout_details():
    """
    Verify creator's payout details are complete before withdrawal.
    Phase 5: Validation step to prevent failed withdrawals.
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    creator_id = get_jwt_identity()
    
    try:
        creator_response = supabase.table('creator').select('id, payout_method, upi_id, bank_account, ifsc').eq('id', creator_id).limit(1).execute()
        
        if not creator_response.data:
            return jsonify({'msg': 'Creator not found'}), 404
        
        creator = creator_response.data[0]
        payout_method = creator.get('payout_method')
        
        if not payout_method:
            return jsonify({
                'msg': 'Payout details not configured',
                'verified': False,
                'missing': ['payout_method']
            }), 400
        
        missing = []
        
        if payout_method == 'upi':
            if not creator.get('upi_id'):
                missing.append('upi_id')
        else:
            if not creator.get('bank_account'):
                missing.append('bank_account')
            if not creator.get('ifsc'):
                missing.append('ifsc')
        
        if missing:
            return jsonify({
                'msg': 'Payout details incomplete',
                'verified': False,
                'missing': missing
            }), 400
        
        return jsonify({
            'msg': 'Payout details are valid and complete',
            'verified': True,
            'payout_method': payout_method
        }), 200

    except Exception as e:
        print(f"Verify payout details error: {str(e)}")
        return jsonify({'msg': 'Failed to verify payout details', 'error': str(e)}), 500


# --- ROUTE 18: GET WITHDRAWAL HISTORY (Phase 5 - Tracking) ---
@payments_bp.route('/creator/withdrawals', methods=['GET'])
@jwt_required()
def get_withdrawal_history():
    """
    Get creator's withdrawal history with status and amounts.
    Filters: status (pending/success/failed), limit, offset
    
    Response: List of withdrawal transactions
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    creator_id = get_jwt_identity()
    status_filter = request.args.get('status')  # pending, success, failed
    limit = request.args.get('limit', 20, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    try:
        # Build query for withdrawal transactions from creator_transactions table
        query = supabase.table('creator_transactions').select(
            'id, type, amount, status, created_at, external_txn_id, payout_method, utr, campaign_id' # Select new fields
        ).eq('creator_id', creator_id) # Filter by creator_id
        
        if status_filter and status_filter in ['pending', 'success', 'failed']:
            query = query.eq('status', status_filter)
        
        # Order by most recent first
        query = query.order('created_at', desc=True)
        
        # Apply pagination
        result = query.range(offset, offset + limit - 1).execute()
        
        withdrawals = []
        for txn in result.data:
            withdrawals.append({
                'id': txn['id'],
                'amount': txn['amount'],
                'status': txn['status'],
                'payout_method': txn.get('payout_method'), # Get directly from column
                'reference_id': txn.get('external_txn_id'), # Get directly from column
                'utr': txn.get('utr'), # Get directly from column
                'created_at': txn['created_at']
            })
        
        print(f"[Withdrawal History] Creator {creator_id}: {len(withdrawals)} withdrawals")
        
        return jsonify({
            'msg': 'Withdrawal history retrieved',
            'withdrawals': withdrawals,
            'count': len(withdrawals),
            'limit': limit,
            'offset': offset
        }), 200

    except Exception as e:
        print(f"Get withdrawal history error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve withdrawal history', 'error': str(e)}), 500


# --- NEW ROUTE: GET CREATOR NOTIFICATIONS ---
@payments_bp.route('/creator/notifications/<creator_id>', methods=['GET'])
@jwt_required()
def get_creator_notifications(creator_id):
    """
    Retrieves the notification array for the specified creator.
    """
    claims = get_jwt()
    print(f"DEBUG: JWT Claims: {claims}")
    user_role = claims.get('role')
    if user_role != 'creator' or str(get_jwt_identity()) != str(creator_id):
        return jsonify({'msg': 'Unauthorized'}), 403
    
    try:
        # Call the RPC function to get notifications
        response = supabase.rpc('get_creator_notifications', {'p_creator_id': creator_id}).execute()
        
        # The RPC function directly returns the array or an empty array
        notifications = response.data
        
        if notifications:
            # Sort in descending order of timestamp
            notifications.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
        return jsonify({
            'msg': 'Notifications retrieved successfully',
            'notifications': notifications
        }), 200

    except Exception as e:
        print(f"Get creator notifications error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve notifications', 'error': str(e)}), 500


# --- ROUTE 9: GET TRANSACTION HISTORY ---
# --- ROUTE 9: GET TRANSACTION HISTORY ---
@payments_bp.route('/transactions/<user_type>/<user_id>', methods=['GET']) # ✅ Ensure <int:> is removed
@jwt_required()
def get_transactions(user_type, user_id):
    claims = get_jwt()
    
    # 👇 FIX 1: Extract role from 'user_metadata' (not top-level claims)
    user_role = claims.get('role')
    current_user_id = get_jwt_identity()
    
    # Debugging (Optional: helps you see what's happening)
    print(f"DEBUG: Token ID: {current_user_id} | URL ID: {user_id}")
    print(f"DEBUG: Token Role: {user_role} | URL Role: {user_type}")

    # 👇 FIX 2: Check ID and Role
    if str(current_user_id) != str(user_id) or user_role != user_type:
        return jsonify({'msg': 'Unauthorized'}), 403
    
    target_table = None
    id_column = None
    select_fields = None

    if user_type == 'brand':
        target_table = 'brand_transaction_detailed_view' # Or your view name
        id_column = 'brand_id'
        select_fields = '*'
    elif user_type == 'creator':
        target_table = 'creator_transactions'
        id_column = 'creator_id'
        select_fields = '*, campaign(name)'
    else:
        return jsonify({'msg': 'Invalid user type'}), 400

    try:
        # Build query
        query = supabase.table(target_table).select(select_fields).eq(id_column, user_id)
        
        # Apply filters (campaign_id, status, etc.)
        campaign_id_filter = request.args.get('campaign_id')
        if campaign_id_filter:
            query = query.eq('campaign_id', int(campaign_id_filter))
        
        status = request.args.get('status')
        if status:
            query = query.eq('status', status)
            
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        # Execute
        response = query.order('created_at', desc=True).range(offset, offset + limit - 1).execute()
        
        # Map response to frontend format
        formatted_transactions = []
        for txn in response.data:
            formatted_txn = {
                'id': txn.get('id'),
                'user_type': user_type,
                'user_id': user_id,
                'campaign_id': txn.get('campaign_id'),
                'amount': txn.get('amount'),
                'type': txn.get('type'),
                'status': txn.get('status'),
                'description': txn.get('description'),
                'created_at': txn.get('created_at'),
                'external_txn_id': txn.get('external_txn_id'),
                'campaign': {'name': txn.get('campaign', {}).get('name')} if isinstance(txn.get('campaign'), dict) else None
            }
            formatted_transactions.append(formatted_txn)
        
        return jsonify({
            'msg': 'Transactions retrieved successfully',
            'transactions': formatted_transactions,
            'count': len(formatted_transactions)
        }), 200

    except Exception as e:
        print(f"Get transactions error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve transactions', 'error': str(e)}), 500

# --- ROUTE 10: REFUND CAMPAIGN (Phase 3 - The Exit/Safety Valve) ---
@payments_bp.route('/refund-campaign', methods=['POST'])
@jwt_required()
def refund_campaign():
    """
    Refund unused budget when brand deletes/closes a campaign.
    Returns: funds_allocated - funds_distributed back to brand wallet
    
    Body: {
        "campaign_id": int
    }
    
    Logic:
    1. Verify campaign belongs to brand
    2. Calculate refundable = funds_allocated - funds_distributed
    3. Return refundable amount to brand wallet
    4. Reset campaign: funds_allocated = 0, funds_distributed = 0
    5. Log refund transaction
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
    data = request.json
    campaign_id = data.get('campaign_id')

    if not campaign_id:
        return jsonify({'msg': 'Missing campaign_id'}), 400

    try:
        # 1. Verify campaign belongs to brand
        campaign_response = supabase.table('campaign').select('id, funds_allocated, funds_distributed').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found or not authorized'}), 404
        
        campaign = campaign_response.data[0]
        funds_allocated = campaign.get('funds_allocated', 0)
        funds_distributed = campaign.get('funds_distributed', 0)
        
        # 2. Calculate refundable amount (unspent funds)
        refundable = funds_allocated - funds_distributed
        
        if refundable <= 0:
            return jsonify({
                'msg': 'No funds to refund. All allocated amounts have been distributed.',
                'refundable': refundable
            }), 400
        
        # 3. Get current brand wallet balance
        brand_response = supabase.table('brand').select('wallet_balance').eq('id', brand_id).limit(1).execute()
        current_balance = brand_response.data[0].get('wallet_balance', 0)
        new_balance = current_balance + refundable
        
        # 4. Update brand wallet
        supabase.table('brand').update({'wallet_balance': new_balance}).eq('id', brand_id).execute()
        
        # 5. Reset campaign allocation
        supabase.table('campaign').update({
            'funds_allocated': 0,
            'funds_distributed': 0
        }).eq('id', campaign_id).execute()
        
        # 6. Log refund transaction
        refund_txn = {
            'brand_id': brand_id,
            'campaign_id': campaign_id,
            'amount': refundable,
            'type': 'refund',
            'status': 'success',
            'description': f'Refunded ₹{refundable:.2f} from campaign {campaign_id}'
        }
        print(f"DEBUG: Inserting transaction with campaign_id: {campaign_id} for type: {refund_txn['type']}")
        supabase.table('brand_transactions').insert([refund_txn]).execute()
        
        print(f"[Refund] Campaign {campaign_id}: Brand {brand_id} refunded ₹{refundable:.2f}")
        
        return jsonify({
            'msg': 'Campaign refunded successfully',
            'campaign_id': campaign_id,
            'refundable_amount': refundable,
            'funds_allocated': funds_allocated,
            'funds_distributed': funds_distributed,
            'new_wallet_balance': new_balance
        }), 200

    except Exception as e:
        print(f"Refund error: {str(e)}")
        return jsonify({'msg': 'Refund failed', 'error': str(e)}), 500


# --- ROUTE 11: GET CAMPAIGN FINANCIAL SUMMARY (Phase 3 - Visibility) ---
@payments_bp.route('/campaign-summary/<int:campaign_id>', methods=['GET'])
@jwt_required()
def get_campaign_summary(campaign_id):
    """
    Get financial summary for a campaign.
    Shows: allocated, distributed, refundable, platform commission earned
    
    Response includes:
    - funds_allocated: Total amount locked for campaign
    - funds_distributed: Total paid to creators
    - refundable: funds_allocated - funds_distributed
    - platform_earnings: 10% of funds_distributed
    - remaining_budget: unspent allocation
    """
    claims = get_jwt()
    user_role = claims.get('role')
    user_id = get_jwt_identity()

    try:
        # Get campaign details
        campaign_response = supabase.table('campaign').select('id, brand_id, funds_allocated, funds_distributed, budget, cpv, view_threshold, total_view_count, deadline').eq('id', campaign_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found'}), 404
        
        campaign = campaign_response.data[0]
        
        # Verify authorization (brand that created it)
        if user_role == 'brand' and campaign.get('brand_id') != user_id:
            return jsonify({'msg': 'Unauthorized'}), 403
        
        funds_allocated = campaign.get('funds_allocated', 0)
        funds_distributed = campaign.get('funds_distributed', 0)
        refundable = funds_allocated - funds_distributed
        platform_earnings = funds_distributed * 0.1
        
        # Get creator count for this campaign
        creators_response = supabase.table('accepted_clips').select('creator_id').eq('campaign_id', campaign_id).execute()
        creator_count = len(set([c.get('creator_id') for c in creators_response.data])) if creators_response.data else 0
        
        return jsonify({
            'msg': 'Campaign summary retrieved successfully',
            'campaign_id': campaign_id,
            'budget': campaign.get('budget'),
            'cpv': campaign.get('cpv'),
            'view_threshold': campaign.get('view_threshold'),
            'total_view_count': campaign.get('total_view_count', 0),
            'deadline': campaign.get('deadline'),
            'financial_summary': {
                'funds_allocated': funds_allocated,
                'funds_distributed': funds_distributed,
                'refundable': refundable,
                'platform_earnings': platform_earnings,
                'utilization_percentage': (funds_distributed / funds_allocated * 100) if funds_allocated > 0 else 0
            },
            'participation': {
                'creator_count': creator_count,
                'total_clips': len(creators_response.data) if creators_response.data else 0
            }
        }), 200

    except Exception as e:
        print(f"Get campaign summary error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve campaign summary', 'error': str(e)}), 500


# --- ROUTE 12: CALCULATE CREATOR EARNINGS (Phase 4 - Performance Loop Helper) ---
@payments_bp.route('/calculate-earnings/<int:campaign_id>/<creator_id>', methods=['GET'])
@jwt_required()
def calculate_earnings(campaign_id, creator_id):
    """
    Calculate current earnings for a creator on a specific campaign.
    
    Phase 4: The Performance Loop
    - Fetch all clips for creator on campaign
    - Get total view count
    - Calculate earnings based on CPV and view threshold
    - Show how much has been paid vs pending
    
    Query params:
    - include_clips: true/false (include individual clip data)
    """
    claims = get_jwt()
    user_role = claims.get('role')
    user_id = get_jwt_identity()
    
    # Allow brand to check their creator earnings or creator to check own
    is_authorized = (user_role == 'brand') or (user_role == 'creator' and user_id == creator_id)
    
    if not is_authorized:
        return jsonify({'msg': 'Unauthorized'}), 403

    try:
        # 1. Get campaign details
        campaign_response = supabase.table('campaign').select('id, cpv, view_threshold, brand_id').eq('id', campaign_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found'}), 404
        
        campaign = campaign_response.data[0]
        cpv = campaign.get('cpv', 0)
        view_threshold = campaign.get('view_threshold', 0)
        brand_id = campaign.get('brand_id')
        
        # 2. If user is creator, verify they have clips on this campaign
        if user_role == 'creator':
            creator_clips = supabase.table('accepted_clips').select('id').eq('campaign_id', campaign_id).eq('creator_id', creator_id).limit(1).execute()
            if not creator_clips.data:
                return jsonify({'msg': 'You have no clips on this campaign'}), 404
        
        # 3. Get all clips for this creator on this campaign
        clips_response = supabase.table('accepted_clips').select('id, view_count, amount_paid, clip_url, submitted_at').eq('campaign_id', campaign_id).eq('creator_id', creator_id).execute()
        
        clips = clips_response.data if clips_response.data else []
        total_views = sum([c.get('view_count', 0) for c in clips])
        total_paid = sum([c.get('amount_paid', 0) for c in clips])
        
        # 4. Calculate total earnings (based on total views)
        total_earnings = (total_views / view_threshold) * cpv if view_threshold > 0 else 0
        
        # 5. Calculate breakdown
        creator_share = total_earnings * 0.9
        platform_commission = total_earnings * 0.1
        pending_earnings = total_earnings - total_paid
        pending_creator_share = pending_earnings * 0.9
        
        # 6. Include clips if requested
        include_clips = request.args.get('include_clips', 'false').lower() == 'true'
        clips_data = clips if include_clips else []
        
        return jsonify({
            'msg': 'Earnings calculated successfully',
            'campaign_id': campaign_id,
            'creator_id': creator_id,
            'campaign_metrics': {
                'cpv': cpv,
                'view_threshold': view_threshold,
                'brand_id': brand_id
            },
            'performance': {
                'total_clips': len(clips),
                'total_views': total_views,
                'milestones_reached': total_views // view_threshold if view_threshold > 0 else 0
            },
            'earnings': {
                'total_earned': total_earnings,
                'creator_share': creator_share,
                'platform_commission': platform_commission,
                'total_already_paid': total_paid,
                'pending_amount': pending_earnings,
                'pending_creator_share': pending_creator_share
            },
            'clips': clips_data if include_clips else None
        }), 200

    except Exception as e:
        print(f"Calculate earnings error: {str(e)}")
        return jsonify({'msg': 'Failed to calculate earnings', 'error': str(e)}), 500


# --- ROUTE 13: BULK DISTRIBUTE TO MULTIPLE CREATORS (Phase 4 - Batch Processing) ---
@payments_bp.route('/bulk-distribute', methods=['POST'])
@jwt_required()
def bulk_distribute():
    """
    Distribute earnings to multiple creators at once.
    Useful for periodic payout runs.
    
    Body: {
        "distributions": [
            {
                "campaign_id": int,
                "creator_id": int,
                "view_count": int,
                "cpv": float,
                "view_threshold": int
            },
            ...
        ]
    }
    
    Response: List of distribution results with success/failure status
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
    data = request.json
    distributions = data.get('distributions', [])

    if not distributions or not isinstance(distributions, list):
        return jsonify({'msg': 'Invalid distributions payload'}), 400

    try:
        results = []
        total_distributed = 0
        successful = 0
        failed = 0
        
        for dist in distributions:
            campaign_id = dist.get('campaign_id')
            creator_id = dist.get('creator_id')
            view_count = dist.get('view_count')
            cpv = dist.get('cpv')
            view_threshold = dist.get('view_threshold')

            try:
                # Verify campaign belongs to brand
                campaign_response = supabase.table('campaign').select('id, funds_allocated, funds_distributed').eq('id', campaign_id).eq('brand_id', brand_id).limit(1).execute()
                
                if not campaign_response.data:
                    results.append({
                        'campaign_id': campaign_id,
                        'creator_id': creator_id,
                        'status': 'failed',
                        'reason': 'Campaign not found or not authorized'
                    })
                    failed += 1
                    continue
                
                campaign = campaign_response.data[0]
                current_allocated = campaign.get('funds_allocated', 0)
                current_distributed = campaign.get('funds_distributed', 0)
                
                # Calculate earnings
                earnings = (view_count / view_threshold) * cpv if view_threshold > 0 else 0
                
                # Check funds
                available = current_allocated - current_distributed
                if available < earnings:
                    results.append({
                        'campaign_id': campaign_id,
                        'creator_id': creator_id,
                        'status': 'failed',
                        'reason': f'Insufficient funds. Required: ₹{earnings:.2f}, Available: ₹{available:.2f}'
                    })
                    failed += 1
                    continue
                
                # Calculate split
                creator_share = earnings * 0.9
                platform_commission = earnings * 0.1
                
                # Update creator wallet
                creator_response = supabase.table('creator').select('id, wallet_balance').eq('id', creator_id).limit(1).execute()
                
                if not creator_response.data:
                    results.append({
                        'campaign_id': campaign_id,
                        'creator_id': creator_id,
                        'status': 'failed',
                        'reason': 'Creator not found'
                    })
                    failed += 1
                    continue
                
                creator_wallet = creator_response.data[0].get('wallet_balance', 0)
                new_creator_wallet = creator_wallet + creator_share
                
                supabase.table('creator').update({'wallet_balance': new_creator_wallet}).eq('id', creator_id).execute()
                
                # Update campaign
                new_distributed = current_distributed + earnings
                supabase.table('campaign').update({'funds_distributed': new_distributed}).eq('id', campaign_id).execute()
                
                # Log transactions
                creator_txn = {
                    'creator_id': creator_id,
                    'campaign_id': campaign_id,
                    'amount': creator_share,
                    'type': 'earning',
                    'status': 'success',
                    'description': f'Earned ₹{creator_share:.2f} from {view_count} views on campaign {campaign_id}'
                }
                print(f"DEBUG: Inserting transaction with campaign_id: {campaign_id} for type: {creator_txn['type']}")
                supabase.table('creator_transactions').insert([creator_txn]).execute()
                
                commission_txn = {
                    'source_brand_id': brand_id,
                    'campaign_id': campaign_id,
                    'amount': platform_commission,
                    'type': 'commission',
                    'status': 'success',
                    'description': f'Platform commission (10%) from creator earnings on campaign {campaign_id}'
                }
                print(f"DEBUG: Inserting transaction with campaign_id: {campaign_id} for type: {commission_txn['type']}")
                supabase.table('platform_transactions').insert([commission_txn]).execute()
                
                results.append({
                    'campaign_id': campaign_id,
                    'creator_id': creator_id,
                    'status': 'success',
                    'total_earnings': earnings,
                    'creator_share': creator_share,
                    'platform_commission': platform_commission,
                    'new_creator_wallet': new_creator_wallet
                })
                
                total_distributed += earnings
                successful += 1
                
            except Exception as e:
                print(f"Bulk distribution error for campaign {campaign_id}, creator {creator_id}: {str(e)}")
                results.append({
                    'campaign_id': campaign_id,
                    'creator_id': creator_id,
                    'status': 'failed',
                    'reason': str(e)
                })
                failed += 1
        
        print(f"[Bulk Distribution] Brand {brand_id}: {successful} successful, {failed} failed. Total: ₹{total_distributed:.2f}")
        
        return jsonify({
            'msg': 'Bulk distribution completed',
            'summary': {
                'total_requested': len(distributions),
                'successful': successful,
                'failed': failed,
                'total_distributed': total_distributed
            },
            'results': results
        }), 200

    except Exception as e:
        print(f"Bulk distribute error: {str(e)}")
        return jsonify({'msg': 'Bulk distribution failed', 'error': str(e)}), 500


# --- ROUTE 19: REQUEST MID-CAMPAIGN REFUND (Phase 6 - Refund Flow) ---
@payments_bp.route('/request-refund', methods=['POST'])
@jwt_required()
def request_refund():
    """
    Brand requests a mid-campaign refund for unspent/unallocated budget.
    Creates refund audit entry for admin approval.
    
    Body: {
        "campaign_id": int,
        "requested_amount": float,
        "reason": "string (e.g., 'Low performance', 'Budget reallocation')"
    }
    
    Response: Refund audit entry with status 'pending'
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
    data = request.json
    campaign_id = data.get('campaign_id')
    requested_amount = data.get('requested_amount')
    reason = data.get('reason', 'Mid-campaign refund requested')
    
    try:
        # Verify campaign belongs to brand
        campaign_response = supabase.table('campaign').select(
            'id, brand_id, budget, funds_allocated, funds_distributed'
        ).eq('id', campaign_id).limit(1).execute()
        
        if not campaign_response.data:
            return jsonify({'msg': 'Campaign not found'}), 404
        
        campaign = campaign_response.data[0]
        if campaign['brand_id'] != brand_id:
            return jsonify({'msg': 'Unauthorized - campaign does not belong to this brand'}), 403
        
        # Calculate refundable amount
        allocated = float(campaign.get('funds_allocated', 0))
        distributed = float(campaign.get('funds_distributed', 0))
        refundable = allocated - distributed
        
        if requested_amount > refundable:
            return jsonify({
                'msg': 'Requested refund exceeds refundable amount',
                'requested': requested_amount,
                'refundable': refundable,
                'allocated': allocated,
                'distributed': distributed
            }), 400
        
        if requested_amount <= 0:
            return jsonify({'msg': 'Refund amount must be greater than 0'}), 400
        
        # Create refund audit entry (status: pending)
        refund_audit = {
            'brand_id': brand_id,
            'campaign_id': campaign_id,
            'refund_type': 'mid_campaign',
            'requested_amount': float(requested_amount),
            'allocated_amount': allocated,
            'distributed_amount': distributed,
            'refundable_amount': refundable,
            'status': 'pending',
            'reason': reason,
            'metadata': {
                'requested_at': datetime.utcnow().isoformat(),
                'campaign_name': campaign.get('name', 'Unknown')
            }
        }
        
        audit_response = supabase.table('refund_audits').insert([refund_audit]).execute()
        audit_record = audit_response.data[0]
        
        print(f"[Refund Request] Brand {brand_id} requested ₹{requested_amount} refund for campaign {campaign_id}")
        
        return jsonify({
            'msg': 'Refund request submitted for admin approval',
            'refund_id': audit_record['id'],
            'campaign_id': campaign_id,
            'requested_amount': requested_amount,
            'refundable_amount': refundable,
            'status': 'pending',
            'created_at': audit_record['created_at']
        }), 200

    except Exception as e:
        print(f"Request refund error: {str(e)}")
        return jsonify({'msg': 'Failed to request refund', 'error': str(e)}), 500


# --- ROUTE 20: GET BRAND REFUND REQUESTS (Phase 6 - Status Tracking) ---
@payments_bp.route('/refund-requests', methods=['GET'])
@jwt_required()
def get_refund_requests():
    """
    Get all refund requests for a brand with filters.
    Filters: status (pending/approved/rejected/completed), campaign_id, limit, offset
    
    Response: List of refund audit records
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'brand':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id = get_jwt_identity()
    status_filter = request.args.get('status')  # pending, approved, rejected, completed
    campaign_id_filter = request.args.get('campaign_id')
    limit = request.args.get('limit', 20, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    try:
        # Build query
        query = supabase.table('refund_audits').select(
            'id, campaign_id, refund_type, requested_amount, allocated_amount, distributed_amount, refundable_amount, approved_amount, status, reason, created_at, updated_at, completed_at'
        ).eq('brand_id', brand_id)
        
        if status_filter and status_filter in ['pending', 'approved', 'rejected', 'completed', 'failed']:
            query = query.eq('status', status_filter)
        
        if campaign_id_filter:
            query = query.eq('campaign_id', int(campaign_id_filter))
        
        # Order by most recent first
        result = query.order('created_at', desc=True).range(offset, offset + limit - 1).execute()
        
        refund_requests = []
        for audit in result.data:
            refund_requests.append({
                'refund_id': audit['id'],
                'campaign_id': audit['campaign_id'],
                'type': audit['refund_type'],
                'requested_amount': audit['requested_amount'],
                'approved_amount': audit['approved_amount'],
                'refundable_amount': audit['refundable_amount'],
                'status': audit['status'],
                'reason': audit['reason'],
                'created_at': audit['created_at'],
                'updated_at': audit['updated_at'],
                'completed_at': audit['completed_at']
            })
        
        print(f"[Refund Requests] Brand {brand_id}: {len(refund_requests)} requests found")
        
        return jsonify({
            'msg': 'Refund requests retrieved successfully',
            'refund_requests': refund_requests,
            'count': len(refund_requests),
            'limit': limit,
            'offset': offset
        }), 200

    except Exception as e:
        print(f"Get refund requests error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve refund requests', 'error': str(e)}), 500


# --- ROUTE 21: APPROVE/PROCESS REFUND (Phase 6 - Admin) ---
@payments_bp.route('/admin/approve-refund', methods=['POST'])
@jwt_required()
def approve_refund():
    """
    Admin approves a refund request and processes the wallet credit.
    Immediately credits brand wallet and logs transaction.
    
    Body: {
        "refund_id": int,
        "approved_amount": float (optional, defaults to requested_amount),
        "approval_reason": "string (optional)"
    }
    
    Response: Updated refund audit with status 'completed'
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    admin_id = get_jwt_identity()
    data = request.json
    refund_id = data.get('refund_id')
    approved_amount = data.get('approved_amount')
    approval_reason = data.get('approval_reason', 'Admin approved refund')
    
    try:
        # Get refund audit
        audit_response = supabase.table('refund_audits').select('*').eq('id', refund_id).limit(1).execute()
        
        if not audit_response.data:
            return jsonify({'msg': 'Refund request not found'}), 404
        
        audit = audit_response.data[0]
        
        if audit['status'] != 'pending':
            return jsonify({'msg': f"Refund already {audit['status']}"}), 400
        
        # If approved_amount not specified, use requested_amount
        if approved_amount is None:
            approved_amount = audit['requested_amount']
        
        # Validate approved amount
        if approved_amount > audit['refundable_amount']:
            return jsonify({
                'msg': 'Approved amount exceeds refundable amount',
                'refundable': audit['refundable_amount'],
                'approved': approved_amount
            }), 400
        
        if approved_amount <= 0:
            return jsonify({'msg': 'Approved amount must be greater than 0'}), 400
        
        brand_id = audit['brand_id']
        campaign_id = audit['campaign_id']
        
        # Get brand current wallet balance
        brand_response = supabase.table('brand').select('wallet_balance').eq('id', brand_id).limit(1).execute()
        brand = brand_response.data[0]
        old_balance = float(brand.get('wallet_balance', 0))
        new_balance = old_balance + approved_amount
        
        # Update brand wallet
        supabase.table('brand').update({'wallet_balance': new_balance}).eq('id', brand_id).execute()
        
        # Update refund audit
        supabase.table('refund_audits').update({
            'status': 'completed',
            'approved_amount': approved_amount,
            'processed_by_admin_id': admin_id,
            'updated_at': datetime.utcnow().isoformat(),
            'completed_at': datetime.utcnow().isoformat(),
            'metadata': {
                'approval_reason': approval_reason,
                'processed_at': datetime.utcnow().isoformat()
            }
        }).eq('id', refund_id).execute()
        
        # Log transaction
        refund_txn = {
            'brand_id': brand_id,
            'campaign_id': campaign_id,
            'amount': float(approved_amount),
            'type': 'refund',
            'status': 'success',
            'description': f"Refund approved: {audit['refund_type']} - {approval_reason}",
            'refund_audit_id': refund_id,
            'refund_reason': audit['reason'],
            'metadata': {
                'processed_by_admin': admin_id,
                'refund_type': audit['refund_type']
            }
        }
        print(f"DEBUG: Inserting transaction with campaign_id: {campaign_id} for type: {refund_txn['type']}")
        supabase.table('brand_transactions').insert([refund_txn]).execute()
        
        print(f"[Refund Approved] Admin {admin_id} approved ₹{approved_amount} refund for campaign {campaign_id}, brand {brand_id}")
        
        return jsonify({
            'msg': 'Refund approved and processed successfully',
            'refund_id': refund_id,
            'campaign_id': campaign_id,
            'approved_amount': approved_amount,
            'brand_wallet_updated': new_balance,
            'status': 'completed',
            'processed_at': datetime.utcnow().isoformat()
        }), 200

    except Exception as e:
        print(f"Approve refund error: {str(e)}")
        return jsonify({'msg': 'Failed to approve refund', 'error': str(e)}), 500


# --- ROUTE 22: REJECT REFUND (Phase 6 - Admin) ---
@payments_bp.route('/admin/reject-refund', methods=['POST'])
@jwt_required()
def reject_refund():
    """
    Admin rejects a refund request with explanation.
    
    Body: {
        "refund_id": int,
        "rejection_reason": "string"
    }
    
    Response: Updated refund audit with status 'rejected'
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    admin_id = get_jwt_identity()
    data = request.json
    refund_id = data.get('refund_id')
    rejection_reason = data.get('rejection_reason', 'Refund rejected by admin')
    
    try:
        # Get refund audit
        audit_response = supabase.table('refund_audits').select('*').eq('id', refund_id).limit(1).execute()
        
        if not audit_response.data:
            return jsonify({'msg': 'Refund request not found'}), 404
        
        audit = audit_response.data[0]
        
        if audit['status'] != 'pending':
            return jsonify({'msg': f"Refund already {audit['status']}"}), 400
        
        # Update refund audit
        supabase.table('refund_audits').update({
            'status': 'rejected',
            'rejection_reason': rejection_reason,
            'processed_by_admin_id': admin_id,
            'updated_at': datetime.utcnow().isoformat(),
            'metadata': {
                'rejected_at': datetime.utcnow().isoformat()
            }
        }).eq('id', refund_id).execute()
        
        print(f"[Refund Rejected] Admin {admin_id} rejected refund {refund_id}: {rejection_reason}")
        
        return jsonify({
            'msg': 'Refund rejected successfully',
            'refund_id': refund_id,
            'status': 'rejected',
            'rejection_reason': rejection_reason
        }), 200

    except Exception as e:
        print(f"Reject refund error: {str(e)}")
        return jsonify({'msg': 'Failed to reject refund', 'error': str(e)}), 500


# --- ROUTE 23: GET REFUND STATUS (Phase 6 - Status Tracking) ---
@payments_bp.route('/refund-status/<int:refund_id>', methods=['GET'])
@jwt_required()
def get_refund_status(refund_id):
    """
    Check status of a specific refund request.
    Brand can check their own refunds, admin can check any.
    
    Response: Detailed refund status with timeline
    """
    claims = get_jwt()
    user_role = claims.get('role')
    user_id = get_jwt_identity()
    
    try:
        # Get refund audit
        audit_response = supabase.table('refund_audits').select('*').eq('id', refund_id).limit(1).execute()
        
        if not audit_response.data:
            return jsonify({'msg': 'Refund not found'}), 404
        
        audit = audit_response.data[0]
        
        # Check authorization
        if user_role == 'brand' and audit['brand_id'] != user_id:
            return jsonify({'msg': 'Unauthorized'}), 403
        
        response_data = {
            'msg': 'Refund status retrieved successfully',
            'refund_id': audit['id'],
            'campaign_id': audit['campaign_id'],
            'status': audit['status'],
            'type': audit['refund_type'],
            'requested_amount': audit['requested_amount'],
            'refundable_amount': audit['refundable_amount'],
            'approved_amount': audit['approved_amount'],
            'reason': audit['reason'],
            'rejection_reason': audit['rejection_reason'],
            'timeline': {
                'created_at': audit['created_at'],
                'updated_at': audit['updated_at'],
                'completed_at': audit['completed_at']
            }
        }
        
        # If approved, show transaction details
        if audit['status'] == 'completed':
            txn_response = supabase.table('transactions').select(
                'id, created_at, amount, status'
            ).eq('refund_audit_id', refund_id).limit(1).execute()
            
            if txn_response.data:
                txn = txn_response.data[0]
                response_data['transaction'] = {
                    'id': txn['id'],
                    'amount': txn['amount'],
                    'status': txn['status'],
                    'created_at': txn['created_at']
                }
        
        print(f"[Refund Status] User {user_id} ({user_role}) checked refund {refund_id}: {audit['status']}")
        
        return jsonify(response_data), 200

    except Exception as e:
        print(f"Get refund status error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve refund status', 'error': str(e)}), 500


# --- ROUTE 24: GET REFUND AUDIT TRAIL (Phase 6 - Audit) ---
@payments_bp.route('/admin/refund-audit-trail', methods=['GET'])
@jwt_required()
def get_refund_audit_trail():
    """
    Admin endpoint to see all refunds across all brands.
    Filters: brand_id, campaign_id, status, date_range
    
    Response: Complete audit trail
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'admin':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    brand_id_filter = request.args.get('brand_id', type=int)
    campaign_id_filter = request.args.get('campaign_id', type=int)
    status_filter = request.args.get('status')
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    try:
        # Build query
        query = supabase.table('refund_audits').select(
            'id, brand_id, campaign_id, refund_type, requested_amount, approved_amount, status, reason, rejection_reason, processed_by_admin_id, created_at, completed_at'
        )
        
        if brand_id_filter:
            query = query.eq('brand_id', brand_id_filter)
        
        if campaign_id_filter:
            query = query.eq('campaign_id', campaign_id_filter)
        
        if status_filter:
            query = query.eq('status', status_filter)
        
        # Order by most recent first
        result = query.order('created_at', desc=True).range(offset, offset + limit - 1).execute()
        
        audit_trail = []
        total_refunded = 0
        pending_amount = 0
        
        for audit in result.data:
            if audit['status'] == 'completed':
                total_refunded += float(audit['approved_amount'] or 0)
            elif audit['status'] == 'pending':
                pending_amount += float(audit['requested_amount'] or 0)
            
            audit_trail.append({
                'refund_id': audit['id'],
                'brand_id': audit['brand_id'],
                'campaign_id': audit['campaign_id'],
                'type': audit['refund_type'],
                'requested_amount': audit['requested_amount'],
                'approved_amount': audit['approved_amount'],
                'status': audit['status'],
                'reason': audit['reason'],
                'created_at': audit['created_at'],
                'completed_at': audit['completed_at']
            })
        
        print(f"[Refund Audit Trail] Retrieved {len(audit_trail)} refund records")
        
        return jsonify({
            'msg': 'Refund audit trail retrieved successfully',
            'audit_trail': audit_trail,
            'count': len(audit_trail),
            'summary': {
                'total_refunded': total_refunded,
                'pending_approval': pending_amount,
                'limit': limit,
                'offset': offset
            }
        }), 200
    except Exception as e:
        print(f"Get refund audit trail error: {str(e)}")
        return jsonify({'msg': 'Failed to retrieve refund audit trail', 'error': str(e)}), 500

# --- NEW ROUTE: REVERT FAILED WITHDRAWAL ---
@payments_bp.route('/creator/revert-withdrawal', methods=['POST'])
@jwt_required()
def revert_failed_withdrawal():
    """
    Manually reverts a failed withdrawal transaction.
    This gives the user a clear action to take and re-syncs the wallet state.
    """
    claims = get_jwt()
    user_role = claims.get('role')
    if user_role != 'creator':
        return jsonify({'msg': 'Unauthorized'}), 403
    
    creator_id = get_jwt_identity()
    data = request.json
    transaction_id = data.get('transaction_id')

    if not transaction_id:
        return jsonify({'msg': 'Missing transaction_id'}), 400

    try:
        # 1. Fetch the failed transaction and verify ownership and status
        txn_response = supabase.table('creator_transactions').select(
            '*'
        ).eq('id', transaction_id).eq('creator_id', creator_id).eq('status', 'failed').limit(1).execute()
        
        if not txn_response.data:
            return jsonify({'msg': 'Failed transaction not found, or it cannot be reverted'}), 404
        
        transaction = txn_response.data[0]
        revert_amount = float(transaction.get('amount', 0))
        original_description = transaction.get('description') or 'Withdrawal'

        # 2. Get current wallet balance
        creator_response = supabase.table('creator').select('wallet_balance').eq('id', creator_id).limit(1).execute()
        
        if not creator_response.data:
            return jsonify({'msg': 'Creator not found'}), 404
            
        current_balance = float(creator_response.data[0].get('wallet_balance', 0))
        new_balance = current_balance + revert_amount
        
        # 3. Update wallet balance
        supabase.table('creator').update({'wallet_balance': new_balance}).eq('id', creator_id).execute()
        
        # 4. Annotate and mark the original failed withdrawal as reverted
        supabase.table('creator_transactions').update({
            'status': 'reverted',
            'description': f"{original_description} (reverted manually)"
        }).eq('id', transaction_id).execute()
        
        # 5. Log the revert transaction for clarity
        revert_txn_log = {
            'creator_id': creator_id,
            'amount': revert_amount,
            'type': 'earning',
            'status': 'success',
            'description': f'Reverted failed withdrawal (Txn ID: {transaction_id})',
            'external_txn_id': f'REVERT_{transaction_id}'
        }
        supabase.table('creator_transactions').insert([revert_txn_log]).execute()

        print(f"[Revert] Creator {creator_id} reverted failed transaction {transaction_id}. Amount: {revert_amount}")

        return jsonify({
            'msg': 'Failed withdrawal successfully reverted.',
            'reverted_amount': revert_amount,
            'new_balance': new_balance
        }), 200

    except Exception as e:
        print(f"Revert withdrawal error: {str(e)}")
        return jsonify({'msg': 'Failed to revert withdrawal', 'error': str(e)}), 500
