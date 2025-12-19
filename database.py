"""
Database module for persisting escrow bot data - Enhanced Version
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import json

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Get database connection"""
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return None

def init_db():
    """Initialize database tables with enhanced schema"""
    conn = get_db_connection()
    if not conn:
        print("⚠️ Database not available - using in-memory storage only")
        return False
    
    try:
        cursor = conn.cursor()
        
        # Create deals table with new schema including completed status
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT UNIQUE NOT NULL,
                transaction_id INTEGER UNIQUE,
                buyer_user_id BIGINT,
                buyer_username VARCHAR(255),
                buyer_address VARCHAR(255),
                seller_user_id BIGINT,
                seller_username VARCHAR(255),
                seller_address VARCHAR(255),
                selected_token VARCHAR(50),
                selected_network VARCHAR(50),
                escrow_address VARCHAR(255),
                group_renamed BOOLEAN DEFAULT FALSE,
                trade_start_time VARCHAR(255),
                deal_details TEXT,
                completed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create deposits table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                escrow_address VARCHAR(255),
                token VARCHAR(50),
                network VARCHAR(50),
                amount NUMERIC(20, 8),
                balance NUMERIC(20, 8),
                tx_hash VARCHAR(255),
                deposit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES deals(chat_id)
            )
        """)
        
        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(255),
                first_name VARCHAR(255),
                referral_code VARCHAR(50) UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create transactions/releases table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                transaction_type VARCHAR(50),
                amount NUMERIC(20, 8),
                escrow_fee NUMERIC(20, 8),
                network_fee NUMERIC(20, 8),
                status VARCHAR(50),
                buyer_confirmed BOOLEAN DEFAULT FALSE,
                seller_confirmed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES deals(chat_id)
            )
        """)
        
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Database tables initialized successfully with enhanced schema")
        return True
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        return False

def save_deal(chat_id, deal_data):
    """Save or update deal information"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO deals (
                chat_id, transaction_id, buyer_user_id, buyer_username, buyer_address,
                seller_user_id, seller_username, seller_address, selected_token, 
                selected_network, escrow_address, trade_start_time, deal_details, group_renamed, completed
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET
                transaction_id = COALESCE(EXCLUDED.transaction_id, deals.transaction_id),
                buyer_user_id = COALESCE(EXCLUDED.buyer_user_id, deals.buyer_user_id),
                buyer_username = COALESCE(EXCLUDED.buyer_username, deals.buyer_username),
                buyer_address = COALESCE(EXCLUDED.buyer_address, deals.buyer_address),
                seller_user_id = COALESCE(EXCLUDED.seller_user_id, deals.seller_user_id),
                seller_username = COALESCE(EXCLUDED.seller_username, deals.seller_username),
                seller_address = COALESCE(EXCLUDED.seller_address, deals.seller_address),
                selected_token = COALESCE(EXCLUDED.selected_token, deals.selected_token),
                selected_network = COALESCE(EXCLUDED.selected_network, deals.selected_network),
                escrow_address = COALESCE(EXCLUDED.escrow_address, deals.escrow_address),
                trade_start_time = COALESCE(EXCLUDED.trade_start_time, deals.trade_start_time),
                deal_details = COALESCE(EXCLUDED.deal_details, deals.deal_details),
                group_renamed = COALESCE(EXCLUDED.group_renamed, deals.group_renamed),
                completed = COALESCE(EXCLUDED.completed, deals.completed),
                updated_at = CURRENT_TIMESTAMP
        """, (
            chat_id,
            deal_data.get('transaction_id'),
            deal_data.get('buyer_user_id'),
            deal_data.get('buyer_username'),
            deal_data.get('buyer_address'),
            deal_data.get('seller_user_id'),
            deal_data.get('seller_username'),
            deal_data.get('seller_address'),
            deal_data.get('selected_token'),
            deal_data.get('selected_network'),
            deal_data.get('escrow_address'),
            deal_data.get('trade_start_time'),
            deal_data.get('deal_details'),
            deal_data.get('group_renamed', False),
            deal_data.get('completed', False)
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error saving deal: {e}")
        return False

def get_deal(chat_id):
    """Retrieve deal information"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM deals WHERE chat_id = %s", (chat_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(result) if result else None
    except Exception as e:
        print(f"❌ Error retrieving deal: {e}")
        return None

def save_deposit(chat_id, deposit_data):
    """Save deposit record"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO deposits (
                chat_id, escrow_address, token, network, amount, balance, tx_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            chat_id,
            deposit_data.get('escrow_address'),
            deposit_data.get('token'),
            deposit_data.get('network'),
            deposit_data.get('amount'),
            deposit_data.get('balance'),
            deposit_data.get('tx_hash')
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error saving deposit: {e}")
        return False

def get_deposits(chat_id):
    """Get all deposits for a chat"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT * FROM deposits WHERE chat_id = %s 
            ORDER BY deposit_time DESC
        """, (chat_id,))
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return [dict(row) for row in results] if results else []
    except Exception as e:
        print(f"❌ Error retrieving deposits: {e}")
        return []

def get_deposits_by_address():
    """Get all deposits grouped by escrow address with their balances"""
    conn = get_db_connection()
    if not conn:
        return {}
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT escrow_address, chat_id, token, network, balance
            FROM deposits
            WHERE balance IS NOT NULL
            ORDER BY escrow_address, deposit_time DESC
        """)
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # Group by address and get the latest balance for each
        address_balances = {}
        for row in results:
            address = row['escrow_address']
            if address not in address_balances:
                address_balances[address] = dict(row)
        
        return address_balances
    except Exception as e:
        print(f"❌ Error retrieving deposits by address: {e}")
        return {}

def save_transaction(chat_id, transaction_data):
    """Save transaction/release record"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transactions (
                chat_id, transaction_type, amount, escrow_fee, network_fee, 
                status, buyer_confirmed, seller_confirmed
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            chat_id,
            transaction_data.get('type'),
            transaction_data.get('amount'),
            transaction_data.get('escrow_fee'),
            transaction_data.get('network_fee'),
            transaction_data.get('status', 'pending'),
            transaction_data.get('buyer_confirmed', False),
            transaction_data.get('seller_confirmed', False)
        ))
        
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        return result[0] if result else False
    except Exception as e:
        print(f"❌ Error saving transaction: {e}")
        return False

def update_transaction_status(transaction_id, status):
    """Update transaction status"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE transactions 
            SET status = %s, completed_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """, (status, transaction_id))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error updating transaction: {e}")
        return False

def save_user(user_id, user_data):
    """Save or update user information"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (user_id, username, first_name, referral_code)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                referral_code = COALESCE(EXCLUDED.referral_code, users.referral_code)
        """, (
            user_id,
            user_data.get('username'),
            user_data.get('first_name'),
            user_data.get('referral_code')
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error saving user: {e}")
        return False

def load_all_deals():
    """Load all deals from database"""
    conn = get_db_connection()
    if not conn:
        return {}
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM deals")
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        deals_dict = {}
        for row in results:
            deals_dict[row['chat_id']] = dict(row)
        return deals_dict
    except Exception as e:
        print(f"❌ Error loading deals: {e}")
        return {}
