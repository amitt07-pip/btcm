import sys
import types
if sys.version_info >= (3, 13):
    sys.modules["imghdr"] = types.ModuleType("imghdr")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberUpdated
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, ChatMemberHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import CreateChatRequest, ExportChatInviteRequest
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.errors import FloodWaitError
import os
import re
import hashlib
import base64
import asyncio
import random
from datetime import datetime, timedelta
import pytz
from PIL import Image, ImageDraw, ImageFont
import io
import aiohttp
import json
from database import init_db, save_deal, get_deal, save_deposit, save_transaction, save_user, load_all_deals, get_deposits_by_address, get_deposits

# Bot token from environment variable
BOT_TOKEN = os.getenv("ESCROW_BOT_TOKEN", "")

# Telethon user client credentials
API_ID = os.getenv("TELEGRAM_API_ID", "")
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING", "")

def normalize_session_string(session_str):
    """
    Clean session string by removing whitespace and newlines
    """
    if not session_str:
        return ""
    # Strip surrounding whitespace and collapse embedded newlines
    cleaned = session_str.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    return cleaned

def validate_session_string(session_str):
    """
    Validate that session string is valid base64 and has proper format
    Returns (is_valid, error_message)
    """
    if not session_str:
        return False, "Session string is empty"
    
    try:
        # Normalize first
        cleaned = normalize_session_string(session_str)
        
        # Check minimum length
        if len(cleaned) < 100:
            return False, "Session string is too short (likely truncated)"
        
        # Try to decode as base64
        # Add padding if needed
        missing_padding = len(cleaned) % 4
        if missing_padding:
            cleaned += '=' * (4 - missing_padding)
        
        decoded = base64.b64decode(cleaned)
        
        # Check decoded payload is not empty
        if len(decoded) < 50:
            return False, "Session string decoded to invalid data"
        
        return True, "Valid"
    
    except Exception as e:
        return False, f"Invalid base64 format: {str(e)}"

# Normalize and validate session string
if SESSION_STRING:
    SESSION_STRING = normalize_session_string(SESSION_STRING)
    is_valid, error_msg = validate_session_string(SESSION_STRING)
    if not is_valid:
        print(f"❌ TELEGRAM_SESSION_STRING validation failed: {error_msg}")
        print("⚠️ Please run 'python telethon_login.py' in Shell to generate a valid session string")
        print("⚠️ Make sure to copy the ENTIRE session string (it's very long)")
        SESSION_STRING = ""  # Clear invalid session

# Admin user IDs (comma-separated)
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "7472359048,7880967664,8453993167,2001575810,5825027777,6864194951,8093808661,5229586098,7962772947")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(",") if admin_id.strip()]

# Notification channel ID
NOTIFICATION_CHANNEL_ID = -1003266978268

# Blockchain API keys
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "")

# USDT contract addresses
BSC_USDT_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# Initialize Telethon user client (for group creation)
user_client = None
user_client_started = False  # Track if Telethon client successfully started
if API_ID and API_HASH:
    try:
        if SESSION_STRING:
            # Use string session (preferred for cloud environments)
            user_client = TelegramClient(
                StringSession(SESSION_STRING),
                int(API_ID),
                API_HASH
            )
            print("✅ Telethon client initialized with string session")
        elif PHONE:
            # Fallback to file-based session
            user_client = TelegramClient(
                "escrow_user_session",
                int(API_ID),
                API_HASH
            )
            print("⚠️ Using file-based session. Please convert to string session to avoid conflicts.")
    except Exception as e:
        print(f"❌ Failed to initialize Telethon client: {e}")
        user_client = None

# Track buyer and seller declarations per chat
escrow_roles = {}  # {chat_id: {'buyer': {...}, 'seller': {...}}}

def load_deals_from_database():
    """Load all deals from database into escrow_roles on startup"""
    global escrow_roles
    try:
        deals = load_all_deals()
        for chat_id, deal_data in deals.items():
            escrow_roles[chat_id] = {
                'transaction_id': deal_data.get('transaction_id'),
                'buyer': {
                    'user_id': deal_data.get('buyer_user_id'),
                    'username': deal_data.get('buyer_username'),
                    'address': deal_data.get('buyer_address')
                } if deal_data.get('buyer_user_id') else None,
                'seller': {
                    'user_id': deal_data.get('seller_user_id'),
                    'username': deal_data.get('seller_username'),
                    'address': deal_data.get('seller_address')
                } if deal_data.get('seller_user_id') else None,
                'selected_token': deal_data.get('selected_token'),
                'selected_network': deal_data.get('selected_network'),
                'escrow_address': deal_data.get('escrow_address'),
                'trade_start_time': deal_data.get('trade_start_time'),
                'group_renamed': deal_data.get('group_renamed')
            }
        if deals:
            print(f"✅ Loaded {len(deals)} deals from database")
    except Exception as e:
        print(f"⚠️ Error loading deals from database: {e}")

def load_balances_from_database():
    """Load escrow balances from database into monitored_addresses on startup"""
    global monitored_addresses
    try:
        address_balances = get_deposits_by_address()
        for address, deposit_data in address_balances.items():
            token = deposit_data.get('token')
            network = deposit_data.get('network')
            network_label = network  # Default to network name
            
            # Try to get network_label from TOKEN_DEFINITIONS
            if token and network and token in TOKEN_DEFINITIONS:
                if network in TOKEN_DEFINITIONS[token]['networks']:
                    network_label = TOKEN_DEFINITIONS[token]['networks'][network]['label'].upper()
            
            monitored_addresses[address] = {
                'chat_id': deposit_data.get('chat_id'),
                'network': network,
                'token': token,
                'network_label': network_label,
                'total_balance': float(deposit_data.get('balance', 0)),
                'last_check': datetime.now()
            }
        if address_balances:
            print(f"✅ Loaded balances for {len(address_balances)} addresses")
    except Exception as e:
        print(f"⚠️ Error loading balances from database: {e}")

# Track monitored addresses for deposit detection
monitored_addresses = {}  # {address: {'chat_id': ..., 'network': ..., 'last_check': ..., 'total_balance': 0}}

# Track address rotation index for each token/network pair
fake_deposit_addresses = {}  # {chat_id: {'BEP20': '0x...', 'TRC20': 'T...'}}
address_rotation_index = {}  # {token_network: index}

# Token definitions with networks and addresses (with rotation support)
# Each network can have multiple addresses that will be rotated
TOKEN_DEFINITIONS = {
    "DOGE": {
        "display": "DOGE",
        "networks": {
            "DOGE": {
                "label": "Dogecoin",
                "addresses": ["D8j5et7K4m4tt6TCkovr2n5Ruyb3mYUeFA"]
            }
        }
    },
    "TRX": {
        "display": "TRX",
        "networks": {
            "TRC20": {
                "label": "TRC20",
                "addresses": [
                    "TDAyZ8PB1MnFXPywHDgrHwa3zkwwXB3WDR",
                    "TXFyTRL3vau3DJe6kyxqUeazoscN8dRrHB"
                ]
            }
        }
    },
    "USDC": {
        "display": "USDC",
        "networks": {
            "BEP20": {
                "label": "BEP20",
                "addresses": ["0xa3D0e7da537057cbeC62A48235FbEc8BB38B4E08"]
            },
            "POLYGON": {
                "label": "Polygon",
                "addresses": ["0xa3D0e7da537057cbeC62A48235FbEc8BB38B4E08"]
            },
            "OPTIMISM": {
                "label": "Optimism",
                "addresses": ["0xa3D0e7da537057cbeC62A48235FbEc8BB38B4E08"]
            }
        }
    },
    "BUSD": {
        "display": "BUSD",
        "networks": {
            "BEP20": {
                "label": "BEP20",
                "addresses": ["0xa3D0e7da537057cbeC62A48235FbEc8BB38B4E08"]
            }
        }
    },
    "LTC": {
        "display": "LTC",
        "networks": {
            "LTC": {
                "label": "Litecoin",
                "addresses": [
                    "LRPJK6HbLvkFsYdqB953yqrUZYDUVdzGFL",  # Original
                    "ltc1qfu7asf36pmg5kc4wge5dcz6t5yd3pyn3d86w66"   # New
                ]
            }
        }
    },
    "SOL": {
        "display": "SOL",
        "networks": {
            "SOL": {
                "label": "Solana",
                "addresses": ["HmqfCsepGq8KNBLKZ2jSyLNsiKYjQK8mpYEmjkQi9weE"]
            }
        }
    },
    "ETH": {
        "display": "ETH",
        "networks": {
            "ETH": {
                "label": "Ethereum",
                "addresses": ["0xa3D0e7da537057cbeC62A48235FbEc8BB38B4E08"]
            },
            "BEP20": {
                "label": "BEP20",
                "addresses": ["0xa3D0e7da537057cbeC62A48235FbEc8BB38B4E08"]
            }
        }
    },
    "BTC": {
        "display": "BTC",
        "networks": {
            "BTC": {
                "label": "Bitcoin",
                "addresses": [
                    "bc1qak4axkk5qw6046p7yl9qxlvtuqq6dm74557ewn",  # Original
                    "bc1q43nwc38ashvvzhakw7ma7227yzd3yfkmpudl48"   # New
                ]
            }
        }
    },
    "BNB": {
        "display": "BNB",
        "networks": {
            "BEP20": {
                "label": "BEP20",
                "addresses": ["0xa3D0e7da537057cbeC62A48235FbEc8BB38B4E08"]
            }
        }
    },
    "USDT": {
        "display": "USDT",
        "networks": {
            "BEP20": {
                "label": "BEP20",
                "addresses": ["0xa3D0e7da537057cbeC62A48235FbEc8BB38B4E08"]
            },
            "TRC20": {
                "label": "TRC20",
                "addresses": [
                    "TDAyZ8PB1MnFXPywHDgrHwa3zkwwXB3WDR",
                    "TXFyTRL3vau3DJe6kyxqUeazoscN8dRrHB"
                ]
            }
        }
    }
}

def get_rotated_address(token, network):
    """Get a rotated address from the available addresses for this token/network.
    Alternates between addresses in a round-robin fashion."""
    
    if token not in TOKEN_DEFINITIONS:
        return None
    
    if network not in TOKEN_DEFINITIONS[token]["networks"]:
        return None
    
    addresses = TOKEN_DEFINITIONS[token]["networks"][network].get("addresses", [])
    
    if not addresses:
        return None
    
    # Use round-robin rotation
    key = f"{token}_{network}"
    
    # Get current index for this token/network pair, default to 0
    current_index = address_rotation_index.get(key, 0)
    
    # Select the address at current index
    selected_address = addresses[current_index]
    
    # Update index for next call (wrap around to 0 when reaching end)
    address_rotation_index[key] = (current_index + 1) % len(addresses)
    
    return selected_address

def generate_referral_code(user_id):
    """Generate a unique referral code for a user based on their ID"""
    hash_object = hashlib.sha256(str(user_id).encode())
    hash_bytes = hash_object.digest()
    b64_encoded = base64.b64encode(hash_bytes).decode('utf-8')
    referral_code = b64_encoded.replace('/', '').replace('+', '').replace('=', '')[:15].upper()
    return f"ref_{referral_code}"


def build_log_message(chat_id):
    """Build the log message text from escrow_roles data"""
    if chat_id not in escrow_roles:
        return None
    
    roles = escrow_roles[chat_id]
    
    initiator = roles.get('log_initiator', 'N/A')
    buyer_info = roles.get('buyer')
    seller_info = roles.get('seller')
    buyer_text = buyer_info['username'] if buyer_info else "Not Set"
    seller_text = seller_info['username'] if seller_info else "Not Set"
    deal_amount = roles.get('deal_amount', 'Not Set')
    status = roles.get('log_status', 'Group Assigned')
    
    msg = (
        f"<b>NEW ESCROW DEAL CREATED</b>\n\n"
        f"<b>🆔 Chat ID:</b> <code>{chat_id}</code>\n"
        f"<b>👤 Initiated by:</b> {initiator}\n"
        f"<b>🛒 Buyer:</b> {buyer_text}\n"
        f"<b>🏪 Seller:</b> {seller_text}\n"
        f"<b>💰 Deal Amount:</b> {deal_amount}\n"
        f"<b>📦 Group Type:</b> P2P\n"
        f"<b>📊 Current Status:</b> {status}"
    )
    
    total_deposit = roles.get('log_total_deposit')
    if total_deposit is not None:
        msg += f"\n\n<b>TOTAL DEPOSIT:</b> <code>{total_deposit}</code>"
    
    return msg


async def send_log_message(context, chat_id):
    """Send the initial log message to the notification channel"""
    try:
        msg_text = build_log_message(chat_id)
        if not msg_text:
            return
        
        sent = await context.bot.send_message(
            chat_id=NOTIFICATION_CHANNEL_ID,
            text=msg_text,
            parse_mode='HTML'
        )
        escrow_roles[chat_id]['log_message_id'] = sent.message_id
        print(f"✅ Log message sent for chat {chat_id}")
    except Exception as e:
        print(f"Failed to send log message: {e}")


async def update_log_message(context_or_bot, chat_id):
    """Update the existing log message in the notification channel.
    Accepts either a context object or a bot object directly."""
    try:
        if chat_id not in escrow_roles:
            return
        
        log_message_id = escrow_roles[chat_id].get('log_message_id')
        if not log_message_id:
            return
        
        msg_text = build_log_message(chat_id)
        if not msg_text:
            return
        
        bot = getattr(context_or_bot, 'bot', context_or_bot)
        await bot.edit_message_text(
            chat_id=NOTIFICATION_CHANNEL_ID,
            message_id=log_message_id,
            text=msg_text,
            parse_mode='HTML'
        )
    except Exception as e:
        print(f"Failed to update log message: {e}")


def generate_group_photo(buyer_username, seller_username):
    """Generate group photo with buyer and seller usernames"""
    try:
        # Open the new template image
        img = Image.open(os.path.join(os.path.dirname(__file__), "photo_4913955247265352489_x_1762874099369.jpg"))
        draw = ImageDraw.Draw(img)
        
        # Try to use fonts that match the template style (Impact-like bold)
        try:
            font = None
            font_paths = [
                "/nix/store/59p03gp3vzbrhd7xjiw3npgbdd68x3y0-dejavu-fonts-2.37/share/fonts/truetype/DejaVuSansCondensed-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
            
            for font_path in font_paths:
                try:
                    # Use size 36 to match example image
                    font = ImageFont.truetype(font_path, 36)
                    break
                except:
                    continue
            
            if font is None:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
        
        # Positions based on the example image (photo_4913955247265352490_x_1762874208640.jpg)
        # Buyer username appears after "Buyer 📝" on the same line
        # Seller username appears after "Seller ~" on the same line
        # Positioning matches the exact placement in the example
        buyer_position = (265, 452)  # Position for buyer username (after "Buyer 📝")
        seller_position = (265, 497)  # Position for seller username (after "Seller ~")
        
        # Draw buyer username in white
        draw.text(buyer_position, buyer_username, fill="white", font=font)
        
        # Draw seller username in white  
        draw.text(seller_position, seller_username, fill="white", font=font)
        
        # Save to bytes buffer
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG')
        buffer.seek(0)
        
        return buffer
    except Exception as e:
        print(f"Error generating group photo: {e}")
        return None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    # Handle deep-link: /start instructions
    if context.args and context.args[0] == "instructions":
        instructions_message = """📘 GUIDE " HOW TO USE @Easy_Escorw_Bot ( Escrow Bot ) " FOR SAFE AND FASTEST HASSLE-FREE ESCROW 🚀  

Step 1 : Use /escrow command in the DM of the Bot.  
( It will auto-create a safe escrow group and drop the link so that buyer and seller can join via that link. ) 🔗👥  

Step 2 : Use /dd command to initiate the process of escrow where you will get the format to express your deal and info.  
( It will include quantity, rate, TnC's agreed upon by both parties. ) 📝🤝  

Step 3 : Use /buyer ( your address ) if you are a buyer 🛒 or /seller ( your address ) if you are a seller 🏪 to verify address and continue the deal.  
( Provide your crypto address which will be used in case of release or refund. ) 💳🔐  

Step 4 : Choose the token and network by /token command and then either party has to accept it. ✅💱  

Step 5 : Use /deposit command to deposit the asset within the bot.  
( Note : Bot will give the deposit address and it has a time limit to deposit ⏳, you have to deposit within that given time. ) ⏰💸  

Step 6 : Once verified by the bot, you can continue the deal.  
( Bot will send the real-time deposit details in the chat. ) 📊💬  

Step 7 : After a successful deal, you can release the asset to the party by using /release ( amount / all ).  
( Thus, the bot will itself release the asset to the party and send the verification in the chat. ) 🎉💼  

🚨 IN CASE OF ANY DISPUTE OR ISSUE, YOU CAN FEEL FREE TO USE /dispute COMMAND, AND SUPPORT WILL JOIN YOU SHORTLY. 🛎️👩‍💻"""
        await update.message.reply_text(instructions_message)
        return

    welcome_message = """💫 @Easy_Escorw_Bot 💫
Your Trustworthy Telegram Escrow Service

Welcome to @Easy_Escorw_Bot. This bot provides a reliable escrow service for your transactions on Telegram.
Avoid scams, your funds are safeguarded throughout your deals. If you run into any issues, simply type /dispute and an arbitrator will join the group chat within 24 hours.

🎟 ESCROW FEE:
1.0% Flat

🌐 [UPDATES](https://t.me/+tO0cDuOe3aRmNmY8) - [VOUCHES](https://t.me/+7mgZcxgqeDEyZjc0) ☑️

💬 Proceed with /escrow (to start with a new escrow)

⚠️ IMPORTANT - Make sure coin is same of Buyer and Seller else you may loose your coin.

💡 Type /menu to summon a menu with all bots features"""
    
    keyboard = [
        [InlineKeyboardButton("COMMANDS LIST 🤖", callback_data="commands_list")],
        [InlineKeyboardButton("☎️ CONTACT", callback_data="contact")],
        [InlineKeyboardButton("Updates 🔃", url="https://t.me/+tO0cDuOe3aRmNmY8"), 
         InlineKeyboardButton("Vouches ✔️", url="https://t.me/+7mgZcxgqeDEyZjc0")],
        [InlineKeyboardButton("WHAT IS ESCROW ❔", callback_data="what_is_escrow"),
         InlineKeyboardButton("Instructions 🧑‍🏫", callback_data="instructions")],
        [InlineKeyboardButton("Terms 📝", callback_data="terms")],
        [InlineKeyboardButton("Invites 👤", callback_data="invites")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=reply_markup)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu command - placeholder for now"""
    await update.message.reply_text("📋 Menu functionality coming soon...")

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /escrow command - directly create P2P escrow group using Telethon"""
    from telethon.tl.functions.channels import CreateChannelRequest, InviteToChannelRequest, EditAdminRequest, LeaveChannelRequest
    from telethon.tl.functions.messages import ExportChatInviteRequest, UpdatePinnedMessageRequest
    from telethon.tl.types import ChatAdminRights
    
    waiting_msg = await update.message.reply_text("<b>Creating a safe trading place for you please wait, please wait...</b>", parse_mode='HTML')
    
    if not user_client:
        error_msg = "❌ Group creation is not configured. Please contact the bot administrator."
        await waiting_msg.edit_text(error_msg)
        return
    
    # Verify Telethon client is connected
    if not user_client_started or not user_client.is_connected():
        error_msg = "❌ Group creation service is currently unavailable. Please contact the bot administrator."
        await waiting_msg.edit_text(error_msg)
        return
    
    try:
        # Get user info
        user = update.effective_user
        
        # Generate random 8-digit number starting with 9
        random_number = random.randint(90000000, 99999999)
        group_name = f"Auto Escrow By Easy Bot"
        
        # Create a megagroup (supergroup) using Telethon
        result = await user_client(CreateChannelRequest(
            title=group_name,
            about="",
            megagroup=True
        ))
        
        # Get the created channel
        channel = result.chats[0]
        channel_id = channel.id
        
        # Get bot entity
        bot_username = (await context.bot.get_me()).username
        bot_entity = await user_client.get_entity(bot_username)
        
        # Add the bot to the group
        await user_client(InviteToChannelRequest(
            channel=channel_id,
            users=[bot_entity]
        ))
        
        # Promote bot to admin with full permissions
        admin_rights = ChatAdminRights(
            change_info=True,
            delete_messages=True,
            ban_users=True,
            invite_users=True,
            pin_messages=True,
            add_admins=True,
            manage_call=True,
            other=True
        )
        
        await user_client(EditAdminRequest(
            channel=channel_id,
            user_id=bot_entity,
            admin_rights=admin_rights,
            rank="Admin"
        ))
        
        # Promote userbot as anonymous admin
        me = await user_client.get_me()
        anon_rights = ChatAdminRights(
            change_info=True,
            delete_messages=True,
            pin_messages=True,
            anonymous=True,
            other=True
        )
        
        await user_client(EditAdminRequest(
            channel=channel_id,
            user_id=me,
            admin_rights=anon_rights,
            rank="Admin"
        ))
        
        # Store the transaction ID
        bot_chat_id = int(f"-100{channel_id}")
        if bot_chat_id not in escrow_roles:
            escrow_roles[bot_chat_id] = {}
        escrow_roles[bot_chat_id]['transaction_id'] = random_number
        
        # Store log info for the initiator
        initiator_username = f"@{user.username}" if user.username else user.first_name
        escrow_roles[bot_chat_id]['log_initiator'] = initiator_username
        escrow_roles[bot_chat_id]['log_status'] = "Group Assigned"
        
        # Send and pin welcome message first
        welcome_text = """<b>📍 Hey there traders! Welcome to our escrow service.
⚠️ IMPORTANT - Make sure coin and network is same of Buyer and Seller else you may loose your coin.
⚠️ IMPORTANT - Make sure the /buyer address and /seller address are of same chain else you may loose your coin.


✅ Please start with /dd command and if you have any doubts please use /start command.</b>"""
        
        sent_message = await user_client.send_message(
            entity=channel_id,
            message=welcome_text,
            parse_mode='html'
        )
        
        await user_client(UpdatePinnedMessageRequest(
            peer=channel_id,
            id=sent_message.id,
            silent=True
        ))
        
        # Delete service messages (e.g. "created group", "invited user") but not pin notifications
        try:
            from telethon.tl.types import (
                MessageActionChannelCreate, MessageActionChatCreate,
                MessageActionChatAddUser, MessageActionChatJoinedByLink
            )
            delete_actions = (
                MessageActionChannelCreate, MessageActionChatCreate,
                MessageActionChatAddUser, MessageActionChatJoinedByLink
            )
            async for message in user_client.iter_messages(channel_id, limit=20):
                if message.action and isinstance(message.action, delete_actions):
                    await user_client.delete_messages(channel_id, [message.id])
        except Exception as e:
            print(f"⚠️ Failed to delete service messages: {e}")
        
        # Generate invite link after welcome message is sent
        invite_result = await user_client(ExportChatInviteRequest(
            peer=channel_id,
            usage_limit=2
        ))
        invite_link = invite_result.link
        print(f"✅ Invite link created: {invite_link}")
        
        # Get user's full name
        user_full_name = user.first_name
        if user.last_name:
            user_full_name += f" {user.last_name}"
        
        # Send the link to user
        success_message = f"""<b><u>Escrow Group Created</u></b>

<b>Creator: {user_full_name}</b>

<b>Join this escrow group and share the link with the buyer and seller.</b>

<b>{invite_link}</b>

<blockquote>⚠️ Note: This link is for 2 members only—third parties are not allowed to join.</blockquote>"""
        
        await waiting_msg.edit_text(success_message, parse_mode='HTML')
        print(f"✅ Link posted to user by bot token")
        
        # Send log message to notification channel
        await send_log_message(context, bot_chat_id)
        
    except FloodWaitError as e:
        await waiting_msg.edit_text(f"⏳ Rate limit hit. Please wait {e.seconds} seconds and try again.")
    except Exception as e:
        error_message = f"❌ Failed to create escrow group.\n\nPlease try again or contact support.\n\nError: {str(e)}"
        await waiting_msg.edit_text(error_message)

async def dispute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /dispute command - notify admins"""
    chat = update.effective_chat
    
    # Only work in groups/supergroups
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text(
            "<b>⚠️ This command can only be used in escrow groups.</b>",
            parse_mode='HTML'
        )
        return
    
    # Reply to the user
    await update.message.reply_text(
        "<b>ℹ️ Dispute has been raised, Kindly wait till our admin joins you.</b>",
        parse_mode='HTML'
    )
    
    # Create an invite link for the group
    try:
        # Create invite link with no member limit (admins can join)
        chat_invite = await context.bot.create_chat_invite_link(chat_id=chat.id)
        invite_link = chat_invite.invite_link
        
        # Get group title
        group_title = chat.title or "Escrow Group"
        
        # Send invite link to all admins
        for admin_id in ADMIN_IDS:
            try:
                admin_message = f"""<b>🚨 DISPUTE RAISED</b>

<b>Group:</b> {group_title}
<b>Chat ID:</b> <code>{chat.id}</code>

<b>Join the group to resolve the dispute:</b>
{invite_link}"""
                
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_message,
                    parse_mode='HTML'
                )
            except Exception as e:
                print(f"Failed to send dispute notification to admin {admin_id}: {e}")
                
    except Exception as e:
        print(f"Error creating invite link for dispute: {e}")
        await update.message.reply_text(
            "<b>⚠️ Failed to notify admins. Please contact support directly.</b>",
            parse_mode='HTML'
        )

async def dd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /dd command - deal details form and rename group with transaction ID"""
    chat_id = update.effective_chat.id
    
    # Initialize escrow_roles for this chat if not exists
    if chat_id not in escrow_roles:
        escrow_roles[chat_id] = {}
    
    # Generate transaction ID (8-digit number starting with 9) if not already exists
    transaction_id = escrow_roles[chat_id].get('transaction_id')
    if not transaction_id:
        transaction_id = random.randint(90000000, 99999999)
        escrow_roles[chat_id]['transaction_id'] = transaction_id
    
    # Rename the group to "Auto Escrow By Easy Bot (XXXXXXXX)"
    try:
        new_title = f"Auto Escrow By Easy Bot ({transaction_id})"
        await context.bot.set_chat_title(chat_id=chat_id, title=new_title)
        escrow_roles[chat_id]['group_renamed'] = True
        print(f"✅ Renamed group {chat_id} to: {new_title}")
    except Exception as e:
        print(f"Failed to rename group {chat_id}: {e}")
    
    dd_message = """Hello there,
Kindly tell deal details i.e.

<code>Quantity -
Rate -
Conditions (if any) -</code>

Remember without it disputes wouldn't be resolved. Once filled proceed with Specifications of the seller or buyer with /seller or /buyer <b>[CRYPTO ADDRESS]</b>"""
    
    keyboard = [[InlineKeyboardButton("How To Use Bot ❔", url="https://t.me/Easy_Escorw_Bot?start=instructions")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(dd_message, parse_mode='HTML', reply_markup=reply_markup)


async def handle_dd_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture Quantity from a filled /dd form response and update the log"""
    if not update.message or not update.message.text:
        return
    
    chat_id = update.effective_chat.id
    text = update.message.text
    
    if chat_id not in escrow_roles:
        return
    
    # Already captured
    if escrow_roles[chat_id].get('deal_amount') and escrow_roles[chat_id]['deal_amount'] != 'Not Set':
        return
    
    match = re.search(r'[Qq]uantity\s*[-:]\s*(.+)', text)
    if match:
        quantity = match.group(1).strip()
        if quantity:
            escrow_roles[chat_id]['deal_amount'] = quantity
            await update_log_message(context, chat_id)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "commands_list":
        commands_message = """📌 AVAILABLE COMMANDS

Here you have a full command list, incase you do like to move through the bot using commands instead of the buttons.

/start - A command to start interacting with the bot
/whatisescrow - A command to tell you more about escrow
/instructions - A command with text instructions
/terms - A command to bring out our TOS
/dispute - A command to contact the admins
/menu - A command to bring out a menu for the bot
/contact - A command to get admin's contact
/commands - A command to get commands list
/stats - A command to check user stats
/vouch - A command to vouch for the bot
/newdeal - A command to start a new deal
/tradeid - A command to get trade id for a chat
/dd - A command to add deal details
/escrow - A command to get a escrow group link
/token - A command to select token for the escrow
/deposit - A command to generate deposit address
/verify - A command to verify wallet address.
/dispute - A command to raise a dispute request
/balance - A command to check the balance of the escrow address
/release - A command to release the funds in the escrow
/refund - A command to refund the funds in the escrow
/seller - A command to set the seller
/buyer - A command to set the buyer
/setfee - A command to set custom trade fee
/save - A command to save default addresses for various chains.
/saved - A command to check saved addresses
/referral - A command to check your referrals"""
        
        keyboard = [[InlineKeyboardButton("BACK", callback_data="back_to_start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(commands_message, reply_markup=reply_markup)
    
    elif query.data == "contact":
        contact_message = """☎️ CONTACT ARBITRATOR

💬 Type /dispute

💡 Incase you're not getting a response can reach out to @bsr_official"""
        
        keyboard = [[InlineKeyboardButton("⬅️ BACK", callback_data="back_to_start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(contact_message, reply_markup=reply_markup)
    
    elif query.data == "what_is_escrow":
        await query.answer("**Coming Soon...**", show_alert=True)
    
    elif query.data == "instructions":
        instructions_message = """📘 GUIDE " HOW TO USE @Easy_Escorw_Bot ( Escrow Bot ) " FOR SAFE AND FASTEST HASSLE-FREE ESCROW 🚀  

Step 1 : Use /escrow command in the DM of the Bot.  
( It will auto-create a safe escrow group and drop the link so that buyer and seller can join via that link. ) 🔗👥  

Step 2 : Use /dd command to initiate the process of escrow where you will get the format to express your deal and info.  
( It will include quantity, rate, TnC's agreed upon by both parties. ) 📝🤝  

Step 3 : Use /buyer ( your address ) if you are a buyer 🛒 or /seller ( your address ) if you are a seller 🏪 to verify address and continue the deal.  
( Provide your crypto address which will be used in case of release or refund. ) 💳🔐  

Step 4 : Choose the token and network by /token command and then either party has to accept it. ✅💱  

Step 5 : Use /deposit command to deposit the asset within the bot.  
( Note : Bot will give the deposit address and it has a time limit to deposit ⏳, you have to deposit within that given time. ) ⏰💸  

Step 6 : Once verified by the bot, you can continue the deal.  
( Bot will send the real-time deposit details in the chat. ) 📊💬  

Step 7 : After a successful deal, you can release the asset to the party by using /release ( amount / all ).  
( Thus, the bot will itself release the asset to the party and send the verification in the chat. ) 🎉💼  

🚨 IN CASE OF ANY DISPUTE OR ISSUE, YOU CAN FEEL FREE TO USE /dispute COMMAND, AND SUPPORT WILL JOIN YOU SHORTLY. 🛎️👩‍💻"""
        
        keyboard = [[InlineKeyboardButton("⬅️ BACK", callback_data="back_to_start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(instructions_message, reply_markup=reply_markup)
    
    elif query.data == "terms":
        terms_message = """📜 TERMS

Our terms of usage are simple.

🎟 Fees
1.0% for P2P and 1.0% for OTC Flat.

Transactions fee will be applicable.

TAKE THIS INTO ACCOUNT WHEN DEPOSITING FUNDS

1️⃣ Record/screenshot the desktop while your perform any testing of logins or data, or recording of physcial items being opened, this is to provide evidence that the data does not work, if the data is working and you are happy to release the funds, you can delete the recording.

FAILURE TO PRODUCE SUFFICIENT EVIDENCE OF TESTING WILL RESULT IN LOSS OF FUNDS

2️⃣ Before you purchase any information, please take the time to learn what you are buying

IT IS NOT THE RESPONSIBILITY OF THE SELLER TO EXPLAIN HOW TO USE THE INFORMATION, ALTHOUGH IT MAY HELP MAKE TRANSACTIONS RUN SMOOTHER IF VENDORS HELP BUYERS

3️⃣ Buyer should ONLY EVER release funds when they RECEIVE WHAT YOU PAID FOR.

WE ARE NOT RESPONSIBLE FOR YOU RELEASING EARLY AND CAN NOT RETRIEVE FUNDS BACK

4️⃣ Users should use trusted local wallets such as electrum.org or exodus wallet to prevent any issues with KYC wallets like Coinbase or Paxful.

ONLINE WALLETS CAN BE SLOW AND BLOCK ACCOUNTS

5️⃣ Our fee's are taken from the balance in the wallet (1.0% for P2P and 1.0% for OTC), so make sure you take that into account when depositing funds.

WE ARE A SERVICE BARE THAT IN MIND

6️⃣ Make sure Coin and Netwwork are same for Buyer and Seller, else you may lose your funds."""
        
        keyboard = [[InlineKeyboardButton("⬅️ BACK", callback_data="back_to_start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(terms_message, reply_markup=reply_markup)
    
    elif query.data == "invites":
        user_id = query.from_user.id
        referral_code = generate_referral_code(user_id)
        
        invites_message = f"""📍 Total Invites: 0 👤  
📍 Tickets: 0 🎟  

💡 Note: Each voucher equals 25.0% off on fees!  

⚡️ For every new user you invite, you get 2 fee tickets.  
⚡️ For every old user (who has already interacted with the bot), you get 1 fee tickets, you can invite them via your referral link too—for the first time ! Yes, you heard it right! We value your previous invites and reward you for them as well.  

Send the link below to users and earn fee reduction tickets for free once they complete minimum $1 worth of Escrows.  

Your Invite Link: 
https://t.me/Easy_Escorw_Bot?start={referral_code}

Start sharing and enjoy CRAZY fee discounts! 🎉"""
        
        keyboard = [[InlineKeyboardButton("⬅️ BACK", callback_data="back_to_start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(invites_message, reply_markup=reply_markup)
    
    elif query.data.startswith("token_"):
        # Handle token selection using TOKEN_DEFINITIONS
        token = query.data.replace("token_", "")
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        # Validate token exists
        if token not in TOKEN_DEFINITIONS:
            await query.answer("⚠️ Invalid token selected!", show_alert=True)
            return
        
        # Check if user is the initiator (only initiator can select token/network)
        if chat_id in escrow_roles:
            token_initiator = escrow_roles[chat_id].get('token_initiator')
            buyer_info = escrow_roles[chat_id].get('buyer')
            seller_info = escrow_roles[chat_id].get('seller')
            
            if token_initiator and buyer_info and seller_info:
                # Determine the other party
                if token_initiator == buyer_info['user_id']:
                    other_party_id = seller_info['user_id']
                else:
                    other_party_id = buyer_info['user_id']
                
                # If someone other than the initiator is clicking, restrict them
                if user_id == other_party_id:
                    await query.answer("⚠️ Only the person who initiated /token can select token and network. You can only accept or reject!", show_alert=True)
                    return
        
        await query.answer()
        
        # Store selected token
        if chat_id not in escrow_roles:
            escrow_roles[chat_id] = {}
        escrow_roles[chat_id]['token'] = token
        
        print(f"Token selected: {token} for chat {chat_id}")
        
        # Get token definition
        token_def = TOKEN_DEFINITIONS[token]
        networks = token_def['networks']
        
        # If only one network, auto-select it and proceed to acceptance
        if len(networks) == 1:
            network_id = list(networks.keys())[0]
            network_label = networks[network_id]['label']
            
            # Auto-select the network
            escrow_roles[chat_id]['selected_token'] = token
            escrow_roles[chat_id]['selected_network'] = network_id
            
            # Get buyer and seller info for acceptance screen
            if 'buyer' not in escrow_roles[chat_id] or 'seller' not in escrow_roles[chat_id]:
                await query.answer("⚠️ Error: Buyer and seller must be set first!", show_alert=True)
                return
            
            buyer_info = escrow_roles[chat_id]['buyer']
            seller_info = escrow_roles[chat_id]['seller']
            token_initiator = escrow_roles[chat_id].get('token_initiator')
            
            # Determine who needs to accept
            if token_initiator == buyer_info['user_id']:
                display_info = seller_info
                role_name = "Seller"
            else:
                display_info = buyer_info
                role_name = "Buyer"
            
            message_text = f"""📍 <b>ESCROW DECLARATION</b>

⚡️ <b>{role_name} {display_info['username']} | Userid: [{display_info['user_id']}]</b>

✅<b>{token} CRYPTO</b>
✅<b>{network_label.upper()} NETWORK</b>"""
            
            keyboard = [
                [InlineKeyboardButton("Accept ✅", callback_data="accept_escrow"),
                 InlineKeyboardButton("Reject ❌", callback_data="reject_escrow")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(message_text, parse_mode='HTML', reply_markup=reply_markup)
        
        else:
            # Multiple networks - show network selection
            keyboard = []
            network_buttons = []
            
            for network_id, network_data in networks.items():
                network_buttons.append(
                    InlineKeyboardButton(
                        network_data['label'].upper(),
                        callback_data=f"network_{token}|{network_id}"
                    )
                )
                # Add 2 buttons per row
                if len(network_buttons) == 2:
                    keyboard.append(network_buttons)
                    network_buttons = []
            
            # Add remaining buttons
            if network_buttons:
                keyboard.append(network_buttons)
            
            # Add back button
            keyboard.append([InlineKeyboardButton("⬅️ BACK", callback_data="back_to_token")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message_text = f"""📍<b>ESCROW-CRYPTO DECLARATION</b>

✅ <b>CRYPTO</b>
{token}

<b>Choose network from the list below for {token}</b>"""
            
            await query.edit_message_text(message_text, parse_mode='HTML', reply_markup=reply_markup)
    
    elif query.data.startswith("network_"):
        # Handle network selection using TOKEN_DEFINITIONS
        try:
            # Parse callback data: network_{token}|{network_id}
            data = query.data.replace("network_", "")
            token, network_id = data.split("|")
            chat_id = query.message.chat_id
            user_id = query.from_user.id
            
            print(f"Network selection: token={token}, network={network_id}, chat_id={chat_id}")
            
            # Validate token and network
            if token not in TOKEN_DEFINITIONS:
                await query.answer("⚠️ Invalid token!", show_alert=True)
                return
            
            if network_id not in TOKEN_DEFINITIONS[token]['networks']:
                await query.answer("⚠️ Invalid network!", show_alert=True)
                return
            
            # Get buyer and seller info
            if chat_id not in escrow_roles or 'buyer' not in escrow_roles[chat_id] or 'seller' not in escrow_roles[chat_id]:
                print(f"Error: Buyer or seller not set for chat {chat_id}")
                await query.answer("⚠️ Error: Buyer and seller must be set first! Use /buyer and /seller commands.", show_alert=True)
                return
            
            buyer_info = escrow_roles[chat_id]['buyer']
            seller_info = escrow_roles[chat_id]['seller']
            token_initiator = escrow_roles[chat_id].get('token_initiator')
            
            # Check if user is the initiator (only initiator can select token/network)
            if token_initiator:
                # Determine the other party
                if token_initiator == buyer_info['user_id']:
                    other_party_id = seller_info['user_id']
                else:
                    other_party_id = buyer_info['user_id']
                
                # If someone other than the initiator is clicking, restrict them
                if user_id == other_party_id:
                    await query.answer("⚠️ Only the person who initiated /token can select token and network. You can only accept or reject!", show_alert=True)
                    return
            
            # Answer the callback query after validation
            await query.answer()
            
            print(f"Buyer: {buyer_info['username']}, Seller: {seller_info['username']}, Initiator: {token_initiator}")
            
            # Store token and network for later use
            escrow_roles[chat_id]['selected_token'] = token
            escrow_roles[chat_id]['selected_network'] = network_id
            
            # Save to database
            save_deal(chat_id, {
                'selected_token': token,
                'selected_network': network_id
            })
            
            # Get network label from TOKEN_DEFINITIONS
            network_label = TOKEN_DEFINITIONS[token]['networks'][network_id]['label']
            
            # Determine who needs to accept/reject
            if token_initiator == buyer_info['user_id']:
                display_info = seller_info
                role_name = "Seller"
            else:
                display_info = buyer_info
                role_name = "Buyer"
            
            message_text = f"""📍 <b>ESCROW DECLARATION</b>

⚡️ <b>{role_name} {display_info['username']} | Userid: [{display_info['user_id']}]</b>

✅<b>{token} CRYPTO</b>
✅<b>{network_label.upper()} NETWORK</b>"""
            
            # Add Accept/Reject buttons
            keyboard = [
                [InlineKeyboardButton("Accept ✅", callback_data="accept_escrow"),
                 InlineKeyboardButton("Reject ❌", callback_data="reject_escrow")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(message_text, parse_mode='HTML', reply_markup=reply_markup)
        except Exception as e:
            print(f"Error in network selection: {e}")
            await query.answer(f"❌ Error: {str(e)}", show_alert=True)
    
    elif query.data == "accept_escrow":
        # Handle escrow acceptance
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles:
            await query.answer("Error: Escrow data not found!", show_alert=True)
            return
        
        buyer_info = escrow_roles[chat_id].get('buyer')
        seller_info = escrow_roles[chat_id].get('seller')
        token = escrow_roles[chat_id].get('selected_token')
        network = escrow_roles[chat_id].get('selected_network')
        token_initiator = escrow_roles[chat_id].get('token_initiator')
        
        if not all([buyer_info, seller_info, token, network, token_initiator]):
            await query.answer("Error: Missing escrow information!", show_alert=True)
            return
        
        # Determine who should accept/reject
        # If buyer initiated, only seller can accept/reject
        # If seller initiated, only buyer can accept/reject
        if token_initiator == buyer_info['user_id']:
            # Buyer initiated, only seller can accept
            allowed_user_id = seller_info['user_id']
        else:
            # Seller initiated, only buyer can accept
            allowed_user_id = buyer_info['user_id']
        
        # Check if the person clicking is authorized
        if user_id != allowed_user_id:
            await query.answer("⚠️ Only the other party can accept or reject this escrow!", show_alert=True)
            return
        
        # Format network name for display
        network_display = f"{network} NETWORK"
        
        # Show full escrow declaration with both buyer and seller
        final_message = f"""📍 <b>ESCROW DECLARATION</b>

⚡️ <b>Buyer {buyer_info['username']} | Userid:[{buyer_info['user_id']}]</b>
⚡️ <b>Seller {seller_info['username']} | Userid: [{seller_info['user_id']}]</b>

✅<b>{token} CRYPTO</b>
✅<b>{network_display}</b>"""
        
        await query.edit_message_text(final_message, parse_mode='HTML')
        await query.answer("✅ Escrow accepted!")
        
        # Update log: Token Selected
        escrow_roles[chat_id]['log_status'] = "Token Selected"
        await update_log_message(context, chat_id)
        
        # Use existing transaction ID (from group number) or generate new one
        transaction_id = escrow_roles[chat_id].get('transaction_id')
        if not transaction_id:
            # Generate transaction ID (8-digit number starting with 9)
            transaction_id = random.randint(90000000, 99999999)
            escrow_roles[chat_id]['transaction_id'] = transaction_id
        
        # Get current timestamp + 1 minute for trade start time (in IST)
        ist = pytz.timezone('Asia/Kolkata')
        trade_start_time = (datetime.now(ist) + timedelta(minutes=1)).strftime("%d/%m/%y %H:%M:%S")
        
        # Store trade start time for later use in /deposit
        escrow_roles[chat_id]['trade_start_time'] = trade_start_time
        
        # Send transaction information message independently (not as a reply)
        transaction_message = f"""📍 <b>TRANSACTION INFORMATION [{transaction_id}]</b>

⚡️ <b>SELLER</b>
<b>{seller_info['username']} | [{seller_info['user_id']}]</b>
{seller_info['address']} <b>[{token}] [{network}]</b>

⚡️ <b>BUYER</b>
<b>{buyer_info['username']} | [{buyer_info['user_id']}]</b>
{buyer_info['address']} <b>[{token}] [{network}]</b>

⏰ <b>Trade Start Time: {trade_start_time}</b>


⚠️ <b>IMPORTANT: Make sure to finalise and agree each-others terms before depositing.</b>

🗒 <b>Please use /deposit command to generate a deposit address for your trade.</b>

<b>Useful commands:</b>
🗒 <code>/release</code> = Will Release The Funds To Buyer.
🗒 <code>/refund</code> = Will Refund The Funds To Seller."""
        
        sent_transaction_msg = await context.bot.send_message(
            chat_id=chat_id, 
            text=transaction_message, 
            parse_mode='HTML'
        )
        
        # Pin the transaction information message
        try:
            await context.bot.pin_chat_message(chat_id=chat_id, message_id=sent_transaction_msg.message_id, disable_notification=True)
        except Exception as e:
            print(f"Error pinning message: {e}")
        
        # Send fee message
        fee_message = "<b>Note: The default fee is 1.0%, which is applied when funds are released. If you wish to customize the structure, use the command /setfee.</b>"
        await context.bot.send_message(chat_id=chat_id, text=fee_message, parse_mode='HTML')
        
        # Generate and set group photo with buyer and seller usernames
        try:
            photo_buffer = generate_group_photo(buyer_info['username'], seller_info['username'])
            if photo_buffer:
                await context.bot.set_chat_photo(chat_id=chat_id, photo=photo_buffer)
        except Exception as e:
            print(f"Error setting chat photo: {e}")
    
    elif query.data == "reject_escrow":
        # Handle escrow rejection - delete the message
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles:
            await query.answer("Error: Escrow data not found!", show_alert=True)
            return
        
        buyer_info = escrow_roles[chat_id].get('buyer')
        seller_info = escrow_roles[chat_id].get('seller')
        token_initiator = escrow_roles[chat_id].get('token_initiator')
        
        if not all([buyer_info, seller_info, token_initiator]):
            await query.answer("Error: Missing escrow information!", show_alert=True)
            return
        
        # Determine who should accept/reject
        if token_initiator == buyer_info['user_id']:
            allowed_user_id = seller_info['user_id']
        else:
            allowed_user_id = buyer_info['user_id']
        
        # Check if the person clicking is authorized
        if user_id != allowed_user_id:
            await query.answer("⚠️ Only the other party can accept or reject this escrow!", show_alert=True)
            return
        
        await query.message.delete()
        await query.answer("❌ Escrow rejected. Message deleted.")
    
    elif query.data == "confirm_buyer":
        # Handle buyer confirmation
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles or 'pending_buyer' not in escrow_roles[chat_id]:
            await query.answer("Error: No pending buyer data found!", show_alert=True)
            return
        
        pending_buyer = escrow_roles[chat_id]['pending_buyer']
        
        # Verify that the person confirming is the same person who initiated
        if pending_buyer['user_id'] != user_id:
            await query.answer("⚠️ Only the person who initiated can confirm!", show_alert=True)
            return
        
        # Check if user has @Easy_Escorw_Bot in their bio
        has_bot_in_bio = False
        try:
            user_chat = await context.bot.get_chat(user_id)
            if user_chat.bio and "@Easy_Escorw_Bot" in user_chat.bio:
                has_bot_in_bio = True
        except Exception as e:
            print(f"Could not fetch bio for user {user_id}: {e}")
        
        # Store buyer information
        escrow_roles[chat_id]['buyer'] = {
            'user_id': pending_buyer['user_id'],
            'username': pending_buyer['username'],
            'address': pending_buyer['address'],
            'has_bot_in_bio': has_bot_in_bio
        }
        
        # Clear pending data
        del escrow_roles[chat_id]['pending_buyer']
        
        # Edit confirmation message to show role declaration
        response_message = f"""📍<b>ESCROW-ROLE DECLARATION</b>

⚡️ <b>BUYER {pending_buyer['username']} | Userid: [{pending_buyer['user_id']}]</b>

✅ <b>BUYER WALLET</b>
<code>{pending_buyer['address']}</code>

<i>Note: If you don't see any address, then your address will used from saved addresses after selecting token and chain for the current escrow.</i>"""
        
        await query.edit_message_text(text=response_message, parse_mode='HTML')
        
        # Rename group with transaction ID if not already renamed
        try:
            if not escrow_roles[chat_id].get('group_renamed', False):
                transaction_id = escrow_roles[chat_id].get('transaction_id')
                if transaction_id:
                    chat = await context.bot.get_chat(chat_id)
                    current_title = chat.title
                    
                    if str(transaction_id) not in current_title:
                        if "P2P" in current_title:
                            new_title = f"P2P Escrow By PAGAL Bot ({transaction_id})"
                        elif "OTC" in current_title:
                            new_title = f"OTC Escrow By PAGAL Bot ({transaction_id})"
                        else:
                            new_title = f"Product Deal Escrow By PAGAL Bot ({transaction_id})"
                        
                        await context.bot.set_chat_title(chat_id=chat_id, title=new_title)
                        escrow_roles[chat_id]['group_renamed'] = True
                        print(f"✅ Group renamed to: {new_title}")
        except Exception as e:
            print(f"Error renaming group in buyer confirmation: {e}")
        
        # Save buyer to database
        save_deal(chat_id, {
            'transaction_id': escrow_roles[chat_id].get('transaction_id'),
            'buyer_user_id': escrow_roles[chat_id]['buyer'].get('user_id'),
            'buyer_username': escrow_roles[chat_id]['buyer'].get('username'),
            'buyer_address': escrow_roles[chat_id]['buyer'].get('address'),
            'seller_user_id': escrow_roles[chat_id].get('seller', {}).get('user_id'),
            'seller_username': escrow_roles[chat_id].get('seller', {}).get('username'),
            'seller_address': escrow_roles[chat_id].get('seller', {}).get('address'),
            'selected_token': escrow_roles[chat_id].get('selected_token'),
            'selected_network': escrow_roles[chat_id].get('selected_network'),
            'escrow_address': escrow_roles[chat_id].get('escrow_address'),
            'trade_start_time': escrow_roles[chat_id].get('trade_start_time'),
            'group_renamed': escrow_roles[chat_id].get('group_renamed')
        })
        
        await query.answer("✅ Buyer role confirmed!")
        
        # Update log message
        if 'seller' in escrow_roles[chat_id]:
            escrow_roles[chat_id]['log_status'] = "Buyer Address Set"
        else:
            escrow_roles[chat_id]['log_status'] = "Buyer Address Set — waiting for seller"
        await update_log_message(context, chat_id)
    
    elif query.data == "cancel_buyer":
        # Handle buyer cancellation
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles or 'pending_buyer' not in escrow_roles[chat_id]:
            await query.answer("Error: No pending buyer data found!", show_alert=True)
            return
        
        pending_buyer = escrow_roles[chat_id]['pending_buyer']
        
        # Verify that the person canceling is the same person who initiated
        if pending_buyer['user_id'] != user_id:
            await query.answer("⚠️ Only the person who initiated can cancel!", show_alert=True)
            return
        
        # Clear pending data
        del escrow_roles[chat_id]['pending_buyer']
        
        # Delete confirmation message
        await query.message.delete()
        await query.answer("❌ Buyer assignment cancelled.")
    
    elif query.data == "confirm_seller":
        # Handle seller confirmation
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles or 'pending_seller' not in escrow_roles[chat_id]:
            await query.answer("Error: No pending seller data found!", show_alert=True)
            return
        
        pending_seller = escrow_roles[chat_id]['pending_seller']
        
        # Verify that the person confirming is the same person who initiated
        if pending_seller['user_id'] != user_id:
            await query.answer("⚠️ Only the person who initiated can confirm!", show_alert=True)
            return
        
        # Check if user has @Easy_Escorw_Bot in their bio
        has_bot_in_bio = False
        try:
            user_chat = await context.bot.get_chat(user_id)
            if user_chat.bio and "@Easy_Escorw_Bot" in user_chat.bio:
                has_bot_in_bio = True
        except Exception as e:
            print(f"Could not fetch bio for user {user_id}: {e}")
        
        # Store seller information
        escrow_roles[chat_id]['seller'] = {
            'user_id': pending_seller['user_id'],
            'username': pending_seller['username'],
            'address': pending_seller['address'],
            'has_bot_in_bio': has_bot_in_bio
        }
        
        # Clear pending data
        del escrow_roles[chat_id]['pending_seller']
        
        # Edit confirmation message to show role declaration
        response_message = f"""📍<b>ESCROW-ROLE DECLARATION</b>

⚡️ <b>SELLER {pending_seller['username']} | Userid: [{pending_seller['user_id']}]</b>

✅ <b>SELLER WALLET</b>
<code>{pending_seller['address']}</code>

<i>Note: If you don't see any address, then your address will used from saved addresses after selecting token and chain for the current escrow.</i>"""
        
        await query.edit_message_text(text=response_message, parse_mode='HTML')
        
        # Rename group with transaction ID if not already renamed
        try:
            if not escrow_roles[chat_id].get('group_renamed', False):
                transaction_id = escrow_roles[chat_id].get('transaction_id')
                if transaction_id:
                    chat = await context.bot.get_chat(chat_id)
                    current_title = chat.title
                    
                    if str(transaction_id) not in current_title:
                        if "P2P" in current_title:
                            new_title = f"P2P Escrow By PAGAL Bot ({transaction_id})"
                        elif "OTC" in current_title:
                            new_title = f"OTC Escrow By PAGAL Bot ({transaction_id})"
                        else:
                            new_title = f"Product Deal Escrow By PAGAL Bot ({transaction_id})"
                        
                        await context.bot.set_chat_title(chat_id=chat_id, title=new_title)
                        escrow_roles[chat_id]['group_renamed'] = True
                        print(f"✅ Group renamed to: {new_title}")
        except Exception as e:
            print(f"Error renaming group in seller confirmation: {e}")
        
        # Save seller to database
        save_deal(chat_id, {
            'transaction_id': escrow_roles[chat_id].get('transaction_id'),
            'buyer_user_id': escrow_roles[chat_id].get('buyer', {}).get('user_id'),
            'buyer_username': escrow_roles[chat_id].get('buyer', {}).get('username'),
            'buyer_address': escrow_roles[chat_id].get('buyer', {}).get('address'),
            'seller_user_id': escrow_roles[chat_id]['seller'].get('user_id'),
            'seller_username': escrow_roles[chat_id]['seller'].get('username'),
            'seller_address': escrow_roles[chat_id]['seller'].get('address'),
            'selected_token': escrow_roles[chat_id].get('selected_token'),
            'selected_network': escrow_roles[chat_id].get('selected_network'),
            'escrow_address': escrow_roles[chat_id].get('escrow_address'),
            'trade_start_time': escrow_roles[chat_id].get('trade_start_time'),
            'group_renamed': escrow_roles[chat_id].get('group_renamed')
        })
        
        await query.answer("✅ Seller role confirmed!")
        
        # Update log message
        if 'buyer' in escrow_roles[chat_id]:
            escrow_roles[chat_id]['log_status'] = "Seller Address Set"
        else:
            escrow_roles[chat_id]['log_status'] = "Seller Address Set — waiting for buyer"
        await update_log_message(context, chat_id)
    
    elif query.data == "cancel_seller":
        # Handle seller cancellation
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles or 'pending_seller' not in escrow_roles[chat_id]:
            await query.answer("Error: No pending seller data found!", show_alert=True)
            return
        
        pending_seller = escrow_roles[chat_id]['pending_seller']
        
        # Verify that the person canceling is the same person who initiated
        if pending_seller['user_id'] != user_id:
            await query.answer("⚠️ Only the person who initiated can cancel!", show_alert=True)
            return
        
        # Clear pending data
        del escrow_roles[chat_id]['pending_seller']
        
        # Delete confirmation message
        await query.message.delete()
        await query.answer("❌ Seller assignment cancelled.")
    
    elif query.data == "update_trade_data_dummy":
        # Handle Update Trade Data button - refresh with current balance
        chat_id = query.message.chat_id
        
        if chat_id not in escrow_roles:
            await query.answer("Error: Escrow data not found!", show_alert=True)
            return
        
        buyer_info = escrow_roles[chat_id].get('buyer')
        seller_info = escrow_roles[chat_id].get('seller')
        token = escrow_roles[chat_id].get('selected_token')
        network = escrow_roles[chat_id].get('selected_network')
        transaction_id = escrow_roles[chat_id].get('transaction_id')
        trade_start_time = escrow_roles[chat_id].get('trade_start_time')
        
        if not all([buyer_info, seller_info, token, network, transaction_id, trade_start_time]):
            await query.answer("Error: Missing transaction information!", show_alert=True)
            return
        
        # Get escrow address and network label from TOKEN_DEFINITIONS
        if token not in TOKEN_DEFINITIONS:
            await query.answer("⚠️ Invalid token!", show_alert=True)
            return
        
        if network not in TOKEN_DEFINITIONS[token]['networks']:
            await query.answer("⚠️ Invalid network!", show_alert=True)
            return
        
        escrow_address = escrow_roles[chat_id].get('escrow_address')
        if not escrow_address:
            await query.answer("⚠️ No deposit address found!", show_alert=True)
            return
        network_label = TOKEN_DEFINITIONS[token]['networks'][network]['label'].upper()
        
        # Get current balance from monitored addresses
        current_balance = 0
        if escrow_address in monitored_addresses:
            current_balance = monitored_addresses[escrow_address]['total_balance']
        
        # Calculate time elapsed since deposit request
        last_deposit_time = escrow_roles[chat_id].get('last_deposit_time')
        if last_deposit_time:
            time_elapsed = (datetime.now() - last_deposit_time).total_seconds() / 60
            remaining_time = max(0, 20 - time_elapsed)
        else:
            remaining_time = 20.00
        
        # Recreate the deposit message with updated balance
        deposit_message = f"""📍 <b>TRANSACTION INFORMATION [{transaction_id}]</b>

⚡️ <b>SELLER</b>
{seller_info['username']} | <b>[{seller_info['user_id']}]</b>

⚡️ <b>BUYER</b>
{buyer_info['username']} | <b>[{buyer_info['user_id']}]</b>

🟢 <b>ESCROW ADDRESS</b>
<code>{escrow_address}</code> <b>[{token}] [{network_label}]</b>

Amount Recieved: <code>{current_balance:.5f}</code> [{current_balance:.2f}$]

⏰ <b>Trade Start Time: {trade_start_time}</b>
⏰ <b>Address Reset In: {remaining_time:.2f} Min</b>

📄 <b>Note: Address will reset after the given time, so make sure to deposit in the bot before the address exprires.</b>
⚠️ <b>IMPORTANT: Make sure to finalise and agree each-others terms before depositing.</b>


<b>Useful commands:</b>
🗒 <code>/release</code> <b>= Always pays the buyer.</b>
🗒 <code>/refund</code> <b>= Always pays the seller.</b>

<b>Remember, once commands are used payment will be released, there is no revert!</b>"""
        
        # Recreate button: "Update Trade Data"
        keyboard = [[InlineKeyboardButton("Update Trade Data", callback_data="update_trade_data_dummy")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Edit the message to refresh it with current balance
        await query.edit_message_text(
            text=deposit_message,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        await query.answer("✅ Trade data updated with current balance!")
    
    elif query.data == "check_payment_deposit":
        # Handle Check Payment button on deposit message - refresh with current balance
        chat_id = query.message.chat_id
        
        if chat_id not in escrow_roles:
            await query.answer("Error: Escrow data not found!", show_alert=True)
            return
        
        buyer_info = escrow_roles[chat_id].get('buyer')
        seller_info = escrow_roles[chat_id].get('seller')
        token = escrow_roles[chat_id].get('selected_token')
        network = escrow_roles[chat_id].get('selected_network')
        transaction_id = escrow_roles[chat_id].get('transaction_id')
        trade_start_time = escrow_roles[chat_id].get('trade_start_time')
        
        if not all([buyer_info, seller_info, token, network, transaction_id, trade_start_time]):
            await query.answer("Error: Missing transaction information!", show_alert=True)
            return
        
        # Get escrow address and network label from TOKEN_DEFINITIONS
        if token not in TOKEN_DEFINITIONS:
            await query.answer("⚠️ Invalid token!", show_alert=True)
            return
        
        if network not in TOKEN_DEFINITIONS[token]['networks']:
            await query.answer("⚠️ Invalid network!", show_alert=True)
            return
        
        escrow_address = escrow_roles[chat_id].get('escrow_address')
        if not escrow_address:
            await query.answer("⚠️ No deposit address found!", show_alert=True)
            return
        network_label = TOKEN_DEFINITIONS[token]['networks'][network]['label'].upper()
        
        # Get current balance from monitored addresses
        current_balance = 0
        if escrow_address in monitored_addresses:
            current_balance = monitored_addresses[escrow_address]['total_balance']
        
        # Calculate time elapsed since deposit request
        last_deposit_time = escrow_roles[chat_id].get('last_deposit_time')
        if last_deposit_time:
            time_elapsed = (datetime.now() - last_deposit_time).total_seconds() / 60
            remaining_time = max(0, 20 - time_elapsed)
        else:
            remaining_time = 20.00
        
        # Recreate the deposit message with updated balance
        # Everything bold except buyer/seller username, user_id, and crypto addresses
        # Escrow address, /release, /refund in monospace (not bold)
        deposit_message = f"""📍 <b>TRANSACTION INFORMATION [{transaction_id}]</b>

⚡️ <b>SELLER</b>
{seller_info['username']} | {seller_info['user_id']}
{seller_info['address']} <b>[{token}] [{network_label}]</b>

⚡️ <b>BUYER</b>
{buyer_info['username']} | {buyer_info['user_id']}
{buyer_info['address']} <b>[{token}] [{network_label}]</b>

🟢 <b>ESCROW ADDRESS</b>
<code>{escrow_address}</code> <b>[{token}] [{network_label}]</b>

Amount Recieved: <code>{current_balance:.5f}</code> [{current_balance:.2f}$]

⏰ <b>Trade Start Time: {trade_start_time}</b>
⏰ <b>Address Reset In: {remaining_time:.2f} Min</b>

📄 <b>Note: Address will reset after the given time, so make sure to deposit in the bot before the address exprires.</b>
⚠️ <b>IMPORTANT: Make sure to finalise and agree each-others terms before depositing.</b>


<b>Useful commands:</b>
🗒 <code>/release</code> <b>= Always pays the buyer.</b>
🗒 <code>/refund</code> <b>= Always pays the seller.</b>

<b>Remember, once commands are used payment will be released, there is no revert!</b>"""
        
        # Recreate button: "Update Trade Data" (non-functional)
        keyboard = [[InlineKeyboardButton("Update Trade Data", callback_data="update_trade_data_dummy")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Edit the message to refresh it
        await query.edit_message_text(
            text=deposit_message,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        await query.answer("✅ Payment status refreshed!")
    
    elif query.data.startswith("release_buyer_confirm_"):
        # Handle buyer confirmation for release
        release_id = query.data.replace("release_buyer_confirm_", "")
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles or 'pending_releases' not in escrow_roles[chat_id]:
            await query.answer("❌ Release confirmation expired!", show_alert=True)
            return
        
        if release_id not in escrow_roles[chat_id]['pending_releases']:
            await query.answer("❌ Release confirmation not found!", show_alert=True)
            return
        
        # Check if user is buyer
        buyer_info = escrow_roles[chat_id].get('buyer')
        seller_info = escrow_roles[chat_id].get('seller')
        if not buyer_info or buyer_info['user_id'] != user_id:
            await query.answer("❌ Only the buyer can confirm!", show_alert=True)
            return
        
        # Mark buyer as confirmed
        release_data = escrow_roles[chat_id]['pending_releases'][release_id]
        release_data['buyer_confirmed'] = True
        
        # Get original message and add confirmation status
        original_text = query.message.text_html
        # Add blank line before first confirmation, none before subsequent
        if "Confirmed" not in original_text:
            updated_text = original_text + "\n\n✅ Buyer Confirmed"
        else:
            updated_text = original_text + "\n✅ Buyer Confirmed"
        
        # Update buttons - one per line
        buyer_button = InlineKeyboardButton("Buyer Confirmation ✅", callback_data=f"release_buyer_confirm_{release_id}")
        seller_button_text = "Seller Confirmation ✅" if release_data['seller_confirmed'] else "Seller Confirmation ❌"
        seller_button = InlineKeyboardButton(seller_button_text, callback_data=f"release_seller_confirm_{release_id}")
        
        keyboard = [
            [buyer_button],
            [seller_button],
            [InlineKeyboardButton("Reject ❌", callback_data=f"release_reject_{release_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Update message text and buttons
        await query.edit_message_text(text=updated_text, parse_mode='HTML', reply_markup=reply_markup)
        
        # If seller hasn't confirmed yet, send waiting message
        if not release_data['seller_confirmed']:
            waiting_message = f"<b><u>Buyer[{buyer_info['username']}]</u> have confirmed the Release withdrawl, waiting for <u>Seller[{seller_info['username']}]</u> confirmation.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=waiting_message,
                parse_mode='HTML'
            )
        else:
            # Both confirmed - remove buttons
            await query.edit_message_reply_markup(reply_markup=None)
            # Send completion message
            completion_message = f"<b>Both <u>Seller[{seller_info['username']}]</u> and <u>Buyer[{buyer_info['username']}]</u> have confirmed the Release withdrawl.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=completion_message,
                parse_mode='HTML'
            )
            # Send release progress message
            token_symbol = release_data['token'].lower()
            progress_message = f"<b>Release of payment {release_data['amount']:.5f} {token_symbol} is in progress.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=progress_message,
                parse_mode='HTML'
            )
            
            # Schedule release completion message after 10 seconds
            asyncio.create_task(send_release_completion_message(
                context, chat_id, release_data
            ))
        
        await query.answer("✅ Buyer confirmed the release!")
    
    elif query.data.startswith("release_seller_confirm_"):
        # Handle seller confirmation for release
        release_id = query.data.replace("release_seller_confirm_", "")
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles or 'pending_releases' not in escrow_roles[chat_id]:
            await query.answer("❌ Release confirmation expired!", show_alert=True)
            return
        
        if release_id not in escrow_roles[chat_id]['pending_releases']:
            await query.answer("❌ Release confirmation not found!", show_alert=True)
            return
        
        # Check if user is seller
        seller_info = escrow_roles[chat_id].get('seller')
        buyer_info = escrow_roles[chat_id].get('buyer')
        if not seller_info or seller_info['user_id'] != user_id:
            await query.answer("❌ Only the seller can confirm!", show_alert=True)
            return
        
        # Mark seller as confirmed
        release_data = escrow_roles[chat_id]['pending_releases'][release_id]
        release_data['seller_confirmed'] = True
        
        # Get original message and add confirmation status
        original_text = query.message.text_html
        # Add blank line before first confirmation, none before subsequent
        if "Confirmed" not in original_text:
            updated_text = original_text + "\n\n✅ Seller Confirmed"
        else:
            updated_text = original_text + "\n✅ Seller Confirmed"
        
        # Update buttons - one per line
        buyer_button_text = "Buyer Confirmation ✅" if release_data['buyer_confirmed'] else "Buyer Confirmation ❌"
        buyer_button = InlineKeyboardButton(buyer_button_text, callback_data=f"release_buyer_confirm_{release_id}")
        seller_button = InlineKeyboardButton("Seller Confirmation ✅", callback_data=f"release_seller_confirm_{release_id}")
        
        keyboard = [
            [buyer_button],
            [seller_button],
            [InlineKeyboardButton("Reject ❌", callback_data=f"release_reject_{release_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Update message text and buttons
        await query.edit_message_text(text=updated_text, parse_mode='HTML', reply_markup=reply_markup)
        
        # If buyer hasn't confirmed yet, send waiting message
        if not release_data['buyer_confirmed']:
            waiting_message = f"<b><u>Seller[{seller_info['username']}]</u> have confirmed the Release withdrawl, waiting for <u>Buyer[{buyer_info['username']}]</u> confirmation.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=waiting_message,
                parse_mode='HTML'
            )
        else:
            # Both confirmed - remove buttons
            await query.edit_message_reply_markup(reply_markup=None)
            # Send completion message
            completion_message = f"<b>Both <u>Seller[{seller_info['username']}]</u> and <u>Buyer[{buyer_info['username']}]</u> have confirmed the Release withdrawl.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=completion_message,
                parse_mode='HTML'
            )
            # Send release progress message
            token_symbol = release_data['token'].lower()
            progress_message = f"<b>Release of payment {release_data['amount']:.5f} {token_symbol} is in progress.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=progress_message,
                parse_mode='HTML'
            )
            
            # Schedule release completion message after 10 seconds
            asyncio.create_task(send_release_completion_message(
                context, chat_id, release_data
            ))
        
        await query.answer("✅ Seller confirmed the release!")
    
    elif query.data.startswith("release_reject_"):
        # Handle rejection of release
        release_id = query.data.replace("release_reject_", "")
        chat_id = query.message.chat_id
        
        if chat_id not in escrow_roles or 'pending_releases' not in escrow_roles[chat_id]:
            await query.answer("❌ Release confirmation already expired!", show_alert=True)
            return
        
        if release_id not in escrow_roles[chat_id]['pending_releases']:
            await query.answer("❌ Release confirmation not found!", show_alert=True)
            return
        
        # Remove the release from pending
        del escrow_roles[chat_id]['pending_releases'][release_id]
        
        # Delete the message
        await query.message.delete()
        await query.answer("❌ Release rejected!")
    
    elif query.data.startswith("refund_buyer_confirm_"):
        # Handle buyer confirmation for refund
        refund_id = query.data.replace("refund_buyer_confirm_", "")
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles or 'pending_refunds' not in escrow_roles[chat_id]:
            await query.answer("❌ Refund confirmation expired!", show_alert=True)
            return
        
        if refund_id not in escrow_roles[chat_id]['pending_refunds']:
            await query.answer("❌ Refund confirmation not found!", show_alert=True)
            return
        
        # Check if user is buyer
        buyer_info = escrow_roles[chat_id].get('buyer')
        seller_info = escrow_roles[chat_id].get('seller')
        if not buyer_info or buyer_info['user_id'] != user_id:
            await query.answer("❌ Only the buyer can confirm!", show_alert=True)
            return
        
        # Mark buyer as confirmed
        refund_data = escrow_roles[chat_id]['pending_refunds'][refund_id]
        refund_data['buyer_confirmed'] = True
        
        # Get original message and add confirmation status
        original_text = query.message.text_html
        if "Confirmed" not in original_text:
            updated_text = original_text + "\n\n✅ Buyer Confirmed"
        else:
            updated_text = original_text + "\n✅ Buyer Confirmed"
        
        # Update buttons
        buyer_button = InlineKeyboardButton("Buyer Confirmation ✅", callback_data=f"refund_buyer_confirm_{refund_id}")
        seller_button_text = "Seller Confirmation ✅" if refund_data['seller_confirmed'] else "Seller Confirmation ❌"
        seller_button = InlineKeyboardButton(seller_button_text, callback_data=f"refund_seller_confirm_{refund_id}")
        
        keyboard = [
            [buyer_button],
            [seller_button],
            [InlineKeyboardButton("Reject ❌", callback_data=f"refund_reject_{refund_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text=updated_text, parse_mode='HTML', reply_markup=reply_markup)
        
        if not refund_data['seller_confirmed']:
            waiting_message = f"<b><u>Buyer[{buyer_info['username']}]</u> have confirmed the Refund, waiting for <u>Seller[{seller_info['username']}]</u> confirmation.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=waiting_message,
                parse_mode='HTML'
            )
        else:
            await query.edit_message_reply_markup(reply_markup=None)
            completion_message = f"<b>Both <u>Seller[{seller_info['username']}]</u> and <u>Buyer[{buyer_info['username']}]</u> have confirmed the Refund.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=completion_message,
                parse_mode='HTML'
            )
            token_symbol = refund_data['token'].lower()
            progress_message = f"<b>Refund of payment {refund_data['amount']:.5f} {token_symbol} is in progress.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=progress_message,
                parse_mode='HTML'
            )
            asyncio.create_task(send_refund_completion_message(
                context, chat_id, refund_data
            ))
        
        await query.answer("✅ Buyer confirmed the refund!")
    
    elif query.data.startswith("refund_seller_confirm_"):
        # Handle seller confirmation for refund
        refund_id = query.data.replace("refund_seller_confirm_", "")
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        if chat_id not in escrow_roles or 'pending_refunds' not in escrow_roles[chat_id]:
            await query.answer("❌ Refund confirmation expired!", show_alert=True)
            return
        
        if refund_id not in escrow_roles[chat_id]['pending_refunds']:
            await query.answer("❌ Refund confirmation not found!", show_alert=True)
            return
        
        # Check if user is seller
        seller_info = escrow_roles[chat_id].get('seller')
        buyer_info = escrow_roles[chat_id].get('buyer')
        if not seller_info or seller_info['user_id'] != user_id:
            await query.answer("❌ Only the seller can confirm!", show_alert=True)
            return
        
        # Mark seller as confirmed
        refund_data = escrow_roles[chat_id]['pending_refunds'][refund_id]
        refund_data['seller_confirmed'] = True
        
        # Get original message and add confirmation status
        original_text = query.message.text_html
        if "Confirmed" not in original_text:
            updated_text = original_text + "\n\n✅ Seller Confirmed"
        else:
            updated_text = original_text + "\n✅ Seller Confirmed"
        
        # Update buttons
        buyer_button_text = "Buyer Confirmation ✅" if refund_data['buyer_confirmed'] else "Buyer Confirmation ❌"
        buyer_button = InlineKeyboardButton(buyer_button_text, callback_data=f"refund_buyer_confirm_{refund_id}")
        seller_button = InlineKeyboardButton("Seller Confirmation ✅", callback_data=f"refund_seller_confirm_{refund_id}")
        
        keyboard = [
            [buyer_button],
            [seller_button],
            [InlineKeyboardButton("Reject ❌", callback_data=f"refund_reject_{refund_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text=updated_text, parse_mode='HTML', reply_markup=reply_markup)
        
        if not refund_data['buyer_confirmed']:
            waiting_message = f"<b><u>Seller[{seller_info['username']}]</u> have confirmed the Refund, waiting for <u>Buyer[{buyer_info['username']}]</u> confirmation.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=waiting_message,
                parse_mode='HTML'
            )
        else:
            await query.edit_message_reply_markup(reply_markup=None)
            completion_message = f"<b>Both <u>Seller[{seller_info['username']}]</u> and <u>Buyer[{buyer_info['username']}]</u> have confirmed the Refund.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=completion_message,
                parse_mode='HTML'
            )
            token_symbol = refund_data['token'].lower()
            progress_message = f"<b>Refund of payment {refund_data['amount']:.5f} {token_symbol} is in progress.</b>"
            await context.bot.send_message(
                chat_id=chat_id,
                text=progress_message,
                parse_mode='HTML'
            )
            asyncio.create_task(send_refund_completion_message(
                context, chat_id, refund_data
            ))
        
        await query.answer("✅ Seller confirmed the refund!")
    
    elif query.data.startswith("refund_reject_"):
        # Handle rejection of refund
        refund_id = query.data.replace("refund_reject_", "")
        chat_id = query.message.chat_id
        
        if chat_id not in escrow_roles or 'pending_refunds' not in escrow_roles[chat_id]:
            await query.answer("❌ Refund confirmation already expired!", show_alert=True)
            return
        
        if refund_id not in escrow_roles[chat_id]['pending_refunds']:
            await query.answer("❌ Refund confirmation not found!", show_alert=True)
            return
        
        del escrow_roles[chat_id]['pending_refunds'][refund_id]
        await query.message.delete()
        await query.answer("❌ Refund rejected!")
    
    elif query.data == "back_to_token":
        # Go back to token selection - use same layout as /token command
        keyboard = [
            [InlineKeyboardButton("DOGE", callback_data="token_DOGE"),
             InlineKeyboardButton("TRX", callback_data="token_TRX"),
             InlineKeyboardButton("USDC", callback_data="token_USDC")],
            [InlineKeyboardButton("BUSD", callback_data="token_BUSD"),
             InlineKeyboardButton("LTC", callback_data="token_LTC"),
             InlineKeyboardButton("SOL", callback_data="token_SOL")],
            [InlineKeyboardButton("ETH", callback_data="token_ETH"),
             InlineKeyboardButton("BTC", callback_data="token_BTC"),
             InlineKeyboardButton("BNB", callback_data="token_BNB")],
            [InlineKeyboardButton("USDT", callback_data="token_USDT")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "<b>choose token from the list below</b>",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    elif query.data == "back_to_start":
        welcome_message = """💫 @Easy_Escorw_Bot 💫
Your Trustworthy Telegram Escrow Service

Welcome to @Easy_Escorw_Bot. This bot provides a reliable escrow service for your transactions on Telegram.
Avoid scams, your funds are safeguarded throughout your deals. If you run into any issues, simply type /dispute and an arbitrator will join the group chat within 24 hours.

🎟 ESCROW FEE:
1.0% Flat

🌐 [UPDATES](https://t.me/+tO0cDuOe3aRmNmY8) - [VOUCHES](https://t.me/+7mgZcxgqeDEyZjc0) ☑️

💬 Proceed with /escrow (to start with a new escrow)

⚠️ IMPORTANT - Make sure coin is same of Buyer and Seller else you may loose your coin.

💡 Type /menu to summon a menu with all bots features"""
        
        keyboard = [
            [InlineKeyboardButton("COMMANDS LIST 🤖", callback_data="commands_list")],
            [InlineKeyboardButton("☎️ CONTACT", callback_data="contact")],
            [InlineKeyboardButton("Updates 🔃", url="http://t.me/Escrow_PagaL"), 
             InlineKeyboardButton("Vouches ✔️", url="http://t.me/PagaL_Escrow_Vouches")],
            [InlineKeyboardButton("WHAT IS ESCROW ❔", callback_data="what_is_escrow"),
             InlineKeyboardButton("Instructions 🧑‍🏫", callback_data="instructions")],
            [InlineKeyboardButton("Terms 📝", callback_data="terms")],
            [InlineKeyboardButton("Invites 👤", callback_data="invites")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(welcome_message, parse_mode='Markdown', disable_web_page_preview=True, reply_markup=reply_markup)

async def buyer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /buyer command with crypto address - shows confirmation dialog"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Check if command has arguments (crypto address)
    if not context.args or len(context.args) == 0:
        # Send help message with image
        help_message = "<code>/buyer [Your Crypto Address]</code>\n\n⛓️ <b>Chains Supported:</b> doge, bsc, ltc, sol, eth, tron, btc"
        
        try:
            with open(os.path.join(os.path.dirname(__file__), "photo_6316666496414845910_y.jpg"), "rb") as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=help_message,
                    parse_mode='HTML'
                )
        except Exception as e:
            print(f"Error sending buyer help image: {e}")
            await update.message.reply_text(help_message, parse_mode='HTML')
        return
    
    # Get the crypto address from arguments
    crypto_address = " ".join(context.args)
    
    # Get username (or use first name if no username)
    username = f"@{user.username}" if user.username else user.first_name
    user_id = user.id
    
    # Initialize chat in escrow_roles if not exists
    if chat_id not in escrow_roles:
        escrow_roles[chat_id] = {}
    
    # Check if buyer role is already set by another user (ROLE LOCKING)
    if 'buyer' in escrow_roles[chat_id]:
        existing_buyer_id = escrow_roles[chat_id]['buyer']['user_id']
        if existing_buyer_id != user_id:
            existing_buyer_username = escrow_roles[chat_id]['buyer']['username']
            await update.message.reply_text(
                f"⚠️ <b>Buyer role is already set by {existing_buyer_username}!</b>\n\n"
                f"Only {existing_buyer_username} can update the buyer information.",
                parse_mode='HTML'
            )
            return
    
    # Store pending buyer data temporarily for confirmation
    if 'pending_buyer' not in escrow_roles[chat_id]:
        escrow_roles[chat_id]['pending_buyer'] = {}
    
    escrow_roles[chat_id]['pending_buyer'] = {
        'user_id': user_id,
        'username': username,
        'address': crypto_address
    }
    
    # Show confirmation dialog
    confirmation_message = """⚠️ <b>Confirmation Required</b>

Are you sure you want to assign yourself as the crypto <b><i>buyer</i></b>?

Once you set yourself as the <b><i>buyer</i></b>, using the /refund command will transfer the funds to <b><i>seller</i></b>.

Click the button below to confirm."""
    
    keyboard = [
        [InlineKeyboardButton("Confirm ✅", callback_data="confirm_buyer"),
         InlineKeyboardButton("Cancel ❌", callback_data="cancel_buyer")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(confirmation_message, parse_mode='HTML', reply_markup=reply_markup)
    
    # Immediately send the next prompt (don't wait for confirmation)
    if 'seller' not in escrow_roles[chat_id]:
        await update.message.reply_text(
            "<b>Please set seller using /seller [DEPOSIT ADDRESS]</b>",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            "<b>Use /token to Choose crypto.</b>",
            parse_mode='HTML'
        )

async def seller_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /seller command with crypto address - shows confirmation dialog"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Check if command has arguments (crypto address)
    if not context.args or len(context.args) == 0:
        # Send help message with image
        help_message = "<code>/seller [Your Crypto Address]</code>\n\n⛓️ <b>Chains Supported:</b> doge, bsc, ltc, sol, eth, tron, btc"
        
        try:
            with open(os.path.join(os.path.dirname(__file__), "photo_6314481552062090385_y.jpg"), "rb") as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=help_message,
                    parse_mode='HTML'
                )
        except Exception as e:
            print(f"Error sending seller help image: {e}")
            await update.message.reply_text(help_message, parse_mode='HTML')
        return
    
    # Get the crypto address from arguments
    crypto_address = " ".join(context.args)
    
    # Get username (or use first name if no username)
    username = f"@{user.username}" if user.username else user.first_name
    user_id = user.id
    
    # Initialize chat in escrow_roles if not exists
    if chat_id not in escrow_roles:
        escrow_roles[chat_id] = {}
    
    # Check if seller role is already set by another user (ROLE LOCKING)
    if 'seller' in escrow_roles[chat_id]:
        existing_seller_id = escrow_roles[chat_id]['seller']['user_id']
        if existing_seller_id != user_id:
            existing_seller_username = escrow_roles[chat_id]['seller']['username']
            await update.message.reply_text(
                f"⚠️ <b>Seller role is already set by {existing_seller_username}!</b>\n\n"
                f"Only {existing_seller_username} can update the seller information.",
                parse_mode='HTML'
            )
            return
    
    # Store pending seller data temporarily for confirmation
    if 'pending_seller' not in escrow_roles[chat_id]:
        escrow_roles[chat_id]['pending_seller'] = {}
    
    escrow_roles[chat_id]['pending_seller'] = {
        'user_id': user_id,
        'username': username,
        'address': crypto_address
    }
    
    # Show confirmation dialog
    confirmation_message = """⚠️ <b>Confirmation Required</b>

Are you sure you want to assign yourself as the crypto <b><i>seller</i></b>?

Once you set yourself as the <b><i>seller</i></b>, using the /release command will transfer the funds to <b><i>buyer</i></b>.

Click the button below to confirm."""
    
    keyboard = [
        [InlineKeyboardButton("Confirm ✅", callback_data="confirm_seller"),
         InlineKeyboardButton("Cancel ❌", callback_data="cancel_seller")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(confirmation_message, parse_mode='HTML', reply_markup=reply_markup)
    
    # Immediately send the next prompt (don't wait for confirmation)
    if 'buyer' not in escrow_roles[chat_id]:
        await update.message.reply_text(
            "<b>Please set buyer using /buyer [DEPOSIT ADDRESS]</b>",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            "<b>Use /token to Choose crypto.</b>",
            parse_mode='HTML'
        )

async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /token command to choose cryptocurrency"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Check if both buyer and seller are set
    if chat_id not in escrow_roles or 'buyer' not in escrow_roles[chat_id] or 'seller' not in escrow_roles[chat_id]:
        await update.message.reply_text(
            "⚠️ Please set both buyer and seller first using /buyer and /seller commands."
        )
        return
    
    # Store who initiated the /token command
    escrow_roles[chat_id]['token_initiator'] = user_id
    
    # Create token selection buttons - 3 columns layout matching the image
    keyboard = [
        [InlineKeyboardButton("DOGE", callback_data="token_DOGE"),
         InlineKeyboardButton("TRX", callback_data="token_TRX"),
         InlineKeyboardButton("USDC", callback_data="token_USDC")],
        [InlineKeyboardButton("BUSD", callback_data="token_BUSD"),
         InlineKeyboardButton("LTC", callback_data="token_LTC"),
         InlineKeyboardButton("SOL", callback_data="token_SOL")],
        [InlineKeyboardButton("ETH", callback_data="token_ETH"),
         InlineKeyboardButton("BTC", callback_data="token_BTC"),
         InlineKeyboardButton("BNB", callback_data="token_BNB")],
        [InlineKeyboardButton("USDT", callback_data="token_USDT")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "<b>choose token from the list below</b>",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deposit command to generate deposit address"""
    chat_id = update.effective_chat.id
    
    # Check if deal is completed
    if chat_id in escrow_roles and escrow_roles[chat_id].get('completed'):
        await update.message.reply_text(
            "<b>Sorry! please first use /dd first!</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if escrow data exists
    if chat_id not in escrow_roles:
        await update.message.reply_text(
            "<b>Sorry! please first use /dd first!</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if buyer and seller are set
    buyer_info = escrow_roles[chat_id].get('buyer')
    seller_info = escrow_roles[chat_id].get('seller')
    
    if not buyer_info or not seller_info:
        await update.message.reply_text(
            "<b>Sorry! please first use /dd first!</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if token and network are selected
    token = escrow_roles[chat_id].get('selected_token')
    network = escrow_roles[chat_id].get('selected_network')
    
    if not token or not network:
        await update.message.reply_text(
            "⚠️ Please select token and network first using /token command."
        )
        return
    
    # Check if deposit was used recently (20-minute cooldown)
    last_deposit_time = escrow_roles[chat_id].get('last_deposit_time')
    if last_deposit_time:
        time_elapsed = (datetime.now() - last_deposit_time).total_seconds() / 60  # in minutes
        if time_elapsed < 20:
            remaining_minutes = 20 - time_elapsed
            await update.message.reply_text(
                f"⏳ <b>Please wait {remaining_minutes:.1f} minutes before requesting a new deposit address.</b>\n\n"
                f"<b>Address will reset after 20 minutes from the last request.</b>",
                parse_mode='HTML'
            )
            return
    
    # Show initial waiting message
    waiting_msg = await update.message.reply_text("Requesting a deposit address for you please wait...")
    
    # Get transaction ID if exists, or generate new one
    transaction_id = escrow_roles[chat_id].get('transaction_id')
    if not transaction_id:
        transaction_id = random.randint(90000000, 99999999)
        escrow_roles[chat_id]['transaction_id'] = transaction_id
    
    # Get trade start time if exists, or use current time + 1 minute (in IST)
    trade_start_time = escrow_roles[chat_id].get('trade_start_time')
    if not trade_start_time:
        ist = pytz.timezone('Asia/Kolkata')
        trade_start_time = (datetime.now(ist) + timedelta(minutes=1)).strftime("%d/%m/%y %H:%M:%S")
        escrow_roles[chat_id]['trade_start_time'] = trade_start_time
    
    # Get escrow address and network label from TOKEN_DEFINITIONS
    if token not in TOKEN_DEFINITIONS:
        await update.message.reply_text("⚠️ Invalid token selected.")
        return
    
    if network not in TOKEN_DEFINITIONS[token]['networks']:
        await update.message.reply_text("⚠️ Invalid network selected.")
        return
    
    # Check if fake deposit address is set for this chat
    escrow_address = None
    if chat_id in fake_deposit_addresses:
        # Check if this token/network combination has a fake address
        fake_addresses = fake_deposit_addresses[chat_id]
        if network in fake_addresses:
            escrow_address = fake_addresses[network]
    
    # If no fake address, get rotated address from available pool
    if not escrow_address:
        escrow_address = get_rotated_address(token, network)
        if not escrow_address:
            await update.message.reply_text("⚠️ No address available for this token/network.")
            return
    
    # Store the selected address for this escrow transaction
    escrow_roles[chat_id]['escrow_address'] = escrow_address
    
    # Save escrow address and token info to database
    save_deal(chat_id, {
        'escrow_address': escrow_address,
        'selected_token': token,
        'selected_network': network,
        'trade_start_time': trade_start_time
    })
    
    network_label = TOKEN_DEFINITIONS[token]['networks'][network]['label'].upper()
    
    # Create deposit information message with proper formatting
    # Everything bold except buyer/seller username, user_id
    # Escrow address, /release, /refund in monospace (not bold)
    deposit_message = f"""📍 <b>TRANSACTION INFORMATION [{transaction_id}]</b>

⚡️ <b>SELLER</b>
{seller_info['username']} | <b>[{seller_info['user_id']}]</b>

⚡️ <b>BUYER</b>
{buyer_info['username']} | <b>[{buyer_info['user_id']}]</b>

🟢 <b>ESCROW ADDRESS</b>
<code>{escrow_address}</code> <b>[{token}] [{network_label}]</b>

Amount Recieved: <code>0.00000</code> [0.00$]

⏰ <b>Trade Start Time: {trade_start_time}</b>
⏰ <b>Address Reset In: 20.00 Min</b>

📄 <b>Note: Address will reset after the given time, so make sure to deposit in the bot before the address exprires.</b>
⚠️ <b>IMPORTANT: Make sure to finalise and agree each-others terms before depositing.</b>


<b>Useful commands:</b>
🗒 <code>/release</code> <b>= Always pays the buyer.</b>
🗒 <code>/refund</code> <b>= Always pays the seller.</b>

<b>Remember, once commands are used payment will be released, there is no revert!</b>"""
    
    # Create button: "Update Trade Data" (non-functional)
    keyboard = [[InlineKeyboardButton("Update Trade Data", callback_data="update_trade_data_dummy")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Delete waiting message
    await waiting_msg.delete()
    
    # Send deposit information as reply to /deposit command
    deposit_msg = await update.message.reply_text(deposit_message, parse_mode='HTML', reply_markup=reply_markup)
    
    # Pin the deposit message
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=deposit_msg.message_id)
    except Exception as e:
        print(f"⚠️ Could not pin deposit message: {e}")
    
    # Store the deposit message ID for later refreshing
    escrow_roles[chat_id]['deposit_message_id'] = deposit_msg.message_id
    
    # Update log: Deposit Address Sent
    last4 = escrow_address[-4:] if escrow_address else "????"
    escrow_roles[chat_id]['log_status'] = f"Deposit Address Sent [{last4}]"
    await update_log_message(context, chat_id)
    
    # Store the current time as last deposit time
    escrow_roles[chat_id]['last_deposit_time'] = datetime.now()
    
    # Start monitoring this address for deposits
    monitored_addresses[escrow_address] = {
        'chat_id': chat_id,
        'network': network,
        'token': token,
        'network_label': network_label,
        'total_balance': 0,
        'last_check': datetime.now()
    }
    
    print(f"Started monitoring {network} address {escrow_address} for chat {chat_id}")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /balance command to show current escrow balance"""
    chat_id = update.effective_chat.id
    
    # Check if deal is completed
    if chat_id in escrow_roles and escrow_roles[chat_id].get('completed'):
        await update.message.reply_text(
            "<b>Sorry! please first use /dd first!</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if escrow data exists
    if chat_id not in escrow_roles:
        await update.message.reply_text(
            "⚠️ No escrow found. Please set buyer and seller first using /buyer and /seller commands."
        )
        return
    
    # Check if token and network are selected
    token = escrow_roles[chat_id].get('selected_token')
    network = escrow_roles[chat_id].get('selected_network')
    
    if not token or not network:
        await update.message.reply_text(
            "⚠️ Please select token and network first using /token command."
        )
        return
    
    # Get escrow address from TOKEN_DEFINITIONS
    if token not in TOKEN_DEFINITIONS:
        await update.message.reply_text("⚠️ Invalid token selected.")
        return
    
    if network not in TOKEN_DEFINITIONS[token]['networks']:
        await update.message.reply_text("⚠️ Invalid network selected.")
        return
    
    escrow_address = escrow_roles[chat_id].get('escrow_address')
    if not escrow_address:
        await update.message.reply_text("⚠️ No deposit address found. Please use /deposit first.")
        return
    
    # Get current balance from monitored addresses or database
    current_balance = 0
    if escrow_address in monitored_addresses:
        current_balance = monitored_addresses[escrow_address]['total_balance']
    else:
        # Get balance from database as fallback
        deposits = get_deposits(chat_id)
        if deposits:
            # Get the latest balance from the most recent deposit
            current_balance = float(deposits[0].get('balance', 0))
    
    # Format message: everything bold except amount (monospace) and USD value (bold+underline)
    balance_message = f"<b>Current Escrow Balance is: <code>{current_balance:.5f}</code>usdt <u>{current_balance:.2f}$</u></b>"
    
    await update.message.reply_text(balance_message, parse_mode='HTML')

async def check_bsc_transactions(address):
    """Check BSC USDT transactions for an address"""
    if not BSCSCAN_API_KEY:
        return []
    
    url = f"https://api.bscscan.com/api"
    params = {
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': BSC_USDT_CONTRACT,
        'address': address,
        'startblock': 0,
        'endblock': 999999999,
        'sort': 'desc',
        'apikey': BSCSCAN_API_KEY
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                if data.get('status') == '1' and data.get('result'):
                    # Filter incoming transactions only (to this address)
                    incoming = [tx for tx in data['result'] if tx['to'].lower() == address.lower()]
                    return incoming
                return []
    except Exception as e:
        print(f"Error checking BSC transactions: {e}")
        return []

async def check_tron_transactions(address):
    """Check TRON USDT (TRC20) transactions for an address"""
    if not TRONGRID_API_KEY:
        return []
    
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    params = {
        'limit': 100,
        'contract_address': TRON_USDT_CONTRACT
    }
    headers = {
        'TRON-PRO-API-KEY': TRONGRID_API_KEY
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as response:
                data = await response.json()
                if data.get('success') and data.get('data'):
                    # Filter incoming transactions only (to this address)
                    incoming = [tx for tx in data['data'] if tx['to'] == address]
                    return incoming
                return []
    except Exception as e:
        print(f"Error checking TRON transactions: {e}")
        return []

def initialize_all_token_monitoring():
    """Initialize monitoring for all tokens and networks from TOKEN_DEFINITIONS"""
    tokens_initialized = 0
    addresses_initialized = 0
    
    for token, token_data in TOKEN_DEFINITIONS.items():
        networks = token_data.get('networks', {})
        for network, network_data in networks.items():
            addresses = network_data.get('addresses', [])
            
            for address in addresses:
                # Skip if already monitoring (e.g., from database)
                if address not in monitored_addresses:
                    monitored_addresses[address] = {
                        'chat_id': None,  # Generic monitoring, no specific chat
                        'network': network,
                        'token': token,
                        'network_label': network_data.get('label', network).upper(),
                        'total_balance': 0,
                        'last_check': datetime.now()
                    }
                    addresses_initialized += 1
            
            if addresses:
                tokens_initialized += 1
    
    print(f"✅ Initialized monitoring for {tokens_initialized} token/network pairs ({addresses_initialized} addresses)")

async def monitor_deposits(bot_app):
    """Background task to monitor escrow addresses for deposits"""
    while True:
        try:
            for address, info in list(monitored_addresses.items()):
                chat_id = info['chat_id']
                network = info['network']
                network_label = info['network_label']
                token = info['token']
                current_balance = info['total_balance']
                
                # Check transactions based on network
                transactions = []
                decimals = 18
                token_name = "UNKNOWN"
                
                if network == "BSC":
                    transactions = await check_bsc_transactions(address)
                    # BSC USDT has 18 decimals
                    decimals = 18
                    token_name = "BSC-USD"
                elif network == "TRON":
                    transactions = await check_tron_transactions(address)
                    # TRON USDT has 6 decimals
                    decimals = 6
                    token_name = "TRON-USDT"
                
                # Calculate total received
                total_received = 0
                for tx in transactions:
                    if network == "BSC":
                        total_received += int(tx['value']) / (10 ** decimals)
                    elif network == "TRON":
                        total_received += int(tx['value']) / (10 ** decimals)
                
                # If new deposit detected
                if total_received > current_balance:
                    new_amount = total_received - current_balance
                    monitored_addresses[address]['total_balance'] = total_received
                    
                    # Save deposit to database
                    save_deposit(chat_id, {
                        'escrow_address': address,
                        'token': token,
                        'network': network,
                        'amount': new_amount,
                        'balance': total_received,
                        'tx_hash': transactions[-1].get('hash') if transactions and network == "BSC" else transactions[-1].get('transaction_id') if transactions else None
                    })
                    
                    # Get the most recent transaction hash for the link
                    tx_hash = None
                    tx_url = None
                    if transactions:
                        if network == "BSC":
                            tx_hash = transactions[-1].get('hash', '')
                            if tx_hash:
                                tx_url = f"https://bscscan.com/tx/{tx_hash}"
                        elif network == "TRON":
                            tx_hash = transactions[-1].get('transaction_id', '')
                            if tx_hash:
                                tx_url = f"https://tronscan.org/#/transaction/{tx_hash}"
                    
                    # Format message with bold text (everything bold except token and amount values)
                    confirmation_message = f"""<b>Deposit 💵 has been confirmed

🪙 Token:</b> {token_name}
<b>💰 Amount:</b> {new_amount:.5f}[{new_amount:.2f}$]
<b>📊 Total Balance:</b> {total_received:.5f}[{total_received:.2f}$]

<b>Please click update button to show the updated data.</b>"""
                    
                    # Create keyboard with Transaction button only
                    keyboard = []
                    if tx_url:
                        keyboard.append([InlineKeyboardButton("Transaction ➡️", url=tx_url)])
                    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                    
                    try:
                        await bot_app.bot.send_message(
                            chat_id=chat_id,
                            text=confirmation_message,
                            parse_mode='HTML',
                            reply_markup=reply_markup
                        )
                        print(f"✅ Deposit detected: {new_amount} {token_name} on {network} for chat {chat_id}")
                        
                        # Update log: Deposit Detected with total deposit
                        if chat_id in escrow_roles:
                            escrow_roles[chat_id]['log_status'] = "Deposit Detected"
                            escrow_roles[chat_id]['log_total_deposit'] = f"{total_received:.5f}"
                            await update_log_message(bot_app.bot, chat_id)
                    except Exception as e:
                        print(f"Failed to send deposit notification: {e}")
        
        except Exception as e:
            print(f"Error in deposit monitoring: {e}")
        
        # Check every 30 seconds
        await asyncio.sleep(30)

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command - admin only, manually add deposit to a chat"""
    user = update.effective_user
    
    # Check if user is an admin
    if user.id not in ADMIN_IDS:
        await update.message.reply_text(
            "<b>⚠️ This command is only available for admins.</b>",
            parse_mode='HTML'
        )
        return
    
    # Check command format: /add [amount] [chat_id]
    if len(context.args) != 2:
        await update.message.reply_text(
            "<b>⚠️ Usage: /add [amount] [chat_id]</b>\n\n"
            "<b>Example:</b> <code>/add 500 -1001234567890</code>",
            parse_mode='HTML'
        )
        return
    
    try:
        amount = float(context.args[0])
        chat_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            "<b>⚠️ Invalid amount or chat ID!</b>\n\n"
            "<b>Amount must be a number and chat_id must be an integer.</b>",
            parse_mode='HTML'
        )
        return
    
    # Get escrow information from the target chat if available
    token_name = "MANUAL"
    wallet_address = None
    selected_network = None
    selected_token = None
    
    if chat_id in escrow_roles:
        selected_token = escrow_roles[chat_id].get('selected_token')
        selected_network = escrow_roles[chat_id].get('selected_network')
        
        if selected_token and selected_network:
            if selected_token in TOKEN_DEFINITIONS:
                if selected_network in TOKEN_DEFINITIONS[selected_token]['networks']:
                    # Get the actual address used for this escrow transaction
                    wallet_address = escrow_roles[chat_id].get('escrow_address')
                    network_label = TOKEN_DEFINITIONS[selected_token]['networks'][selected_network]['label'].upper()
                    token_name = f"{selected_token}-{network_label}"
    
    # Calculate new balance BEFORE using it in the message
    new_balance = amount
    if wallet_address:
        # Get current balance if address is being monitored
        if wallet_address in monitored_addresses:
            new_balance = monitored_addresses[wallet_address]['total_balance'] + amount
            monitored_addresses[wallet_address]['total_balance'] = new_balance
        else:
            # Add the address to monitored_addresses if not already there
            monitored_addresses[wallet_address] = {
                'chat_id': chat_id,
                'network': selected_network or 'MANUAL',
                'token': selected_token or token_name,
                'network_label': selected_network or 'MANUAL',
                'total_balance': new_balance,
                'last_check': datetime.now()
            }
    
    # Format message with bold text (everything bold except token and amount values)
    confirmation_message = f"""<b>Deposit 💵 has been confirmed

🪙 Token:</b> {token_name}
<b>💰 Amount:</b> {amount:.5f}[{amount:.2f}$]

<b>Please click update button to show the updated data.</b>"""
    
    # Create keyboard with Transaction button linking to escrow address
    keyboard = []
    if wallet_address and selected_network:
        # Link to address explorer based on network type
        address_url = None
        if selected_network in ["BSC", "BEP20"]:
            address_url = f"https://bscscan.com/address/{wallet_address}"
        elif selected_network in ["TRON", "TRC20"]:
            address_url = f"https://tronscan.org/#/address/{wallet_address}"
        elif selected_network == "BTC":
            address_url = f"https://blockchain.info/address/{wallet_address}"
        elif selected_network == "ETH":
            address_url = f"https://etherscan.io/address/{wallet_address}"
        elif selected_network == "LTC":
            address_url = f"https://blockchair.com/litecoin/address/{wallet_address}"
        
        if address_url:
            keyboard.append([InlineKeyboardButton("Transaction ➡️", url=address_url)])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    # Save deposit to database
    save_deposit(chat_id, {
        'escrow_address': wallet_address,
        'token': selected_token or token_name,
        'network': selected_network or 'MANUAL',
        'amount': amount,
        'balance': new_balance,
        'tx_hash': None  # Manual deposits don't have tx_hash
    })
    print(f"✅ Updated balance for {wallet_address}: {new_balance}")
    
    # Send deposit confirmation to the specified chat
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=confirmation_message,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
        # Confirm to admin
        await update.message.reply_text(
            f"<b>✅ Manual deposit added successfully!</b>\n\n"
            f"<b>Amount:</b> {amount:.5f}\n"
            f"<b>Chat ID:</b> <code>{chat_id}</code>",
            parse_mode='HTML'
        )
        print(f"✅ Admin {user.id} manually added deposit of {amount} to chat {chat_id}")
        
        # Update log: Deposit Detected
        if chat_id in escrow_roles:
            escrow_roles[chat_id]['log_status'] = "Deposit Detected"
            escrow_roles[chat_id]['log_total_deposit'] = f"{new_balance:.5f}"
            await update_log_message(context, chat_id)
    except Exception as e:
        await update.message.reply_text(
            f"<b>❌ Failed to send deposit notification:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode='HTML'
        )
        print(f"Failed to send manual deposit notification: {e}")

async def blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /blacklist command - admin only, ban replied user"""
    user = update.effective_user
    chat = update.effective_chat
    
    # Check if user is an admin
    if user.id not in ADMIN_IDS:
        await update.message.reply_text(
            "<b>⚠️ This command is only available for admins.</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if command is used in a group
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text(
            "<b>⚠️ This command can only be used in groups.</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if this is a reply to another message
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "<b>⚠️ Please reply to a user's message to blacklist them.</b>",
            parse_mode='HTML'
        )
        return
    
    # Get the user to be banned
    target_user = update.message.reply_to_message.from_user
    
    # Don't ban other admins
    if target_user.id in ADMIN_IDS:
        await update.message.reply_text(
            "<b>⚠️ Cannot blacklist other admins.</b>",
            parse_mode='HTML'
        )
        return
    
    # Ban the user
    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=target_user.id)
        
        target_username = f"@{target_user.username}" if target_user.username else target_user.first_name
        await update.message.reply_text(
            f"<b>✅ User {target_username} has been blacklisted and banned from this group.</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        await update.message.reply_text(
            f"<b>❌ Failed to ban user: {str(e)}</b>",
            parse_mode='HTML'
        )

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /verify command - verify if group is created by bot or verify escrow address"""
    chat = update.effective_chat
    
    # If no arguments provided
    if not context.args:
        # If used in a group, verify the current group
        if chat.type in ['group', 'supergroup']:
            chat_id = chat.id
            if chat_id in escrow_roles:
                await update.message.reply_text(
                    "<b>The provided chat is valid and created by bot only.</b>",
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    "<b>The provided chat is not valid and not created by bot only.</b>",
                    parse_mode='HTML'
                )
            return
        else:
            # If used in DM, show help
            await update.message.reply_text(
                "<b>Please use the proper format.\n\nEx: </b><code>/verify</code> <b>[address]</b>",
                parse_mode='HTML'
            )
            return
    
    input_text = ' '.join(context.args)
    
    # It's an escrow address - check if it belongs to the bot
    address_to_check = input_text.strip()
    is_bot_address = False
    
    # Check against all addresses in TOKEN_DEFINITIONS
    for token, token_data in TOKEN_DEFINITIONS.items():
        for network, network_data in token_data.get("networks", {}).items():
            addresses = network_data.get("addresses", [])
            # Case-insensitive comparison for addresses
            if any(addr.lower() == address_to_check.lower() for addr in addresses):
                is_bot_address = True
                break
        if is_bot_address:
            break
    
    if is_bot_address:
        provided_address = address_to_check
        
        # Find which deal this address is assigned to
        deal_chat_id = None
        
        # Strategy 1: Check if the user who sent the command is buyer/seller in any deal
        user_id = update.effective_user.id
        for cid, roles in escrow_roles.items():
            buyer = roles.get('buyer')
            seller = roles.get('seller')
            if buyer and buyer.get('user_id') == user_id:
                if roles.get('escrow_address', '').lower() == provided_address.lower():
                    deal_chat_id = cid
                    break
            if seller and seller.get('user_id') == user_id:
                if roles.get('escrow_address', '').lower() == provided_address.lower():
                    deal_chat_id = cid
                    break
        
        # Strategy 2: Search all escrow_roles for matching escrow_address
        if not deal_chat_id:
            for cid, roles in escrow_roles.items():
                if roles.get('escrow_address', '').lower() == provided_address.lower():
                    deal_chat_id = cid
                    break
        
        # Strategy 3: Check monitored_addresses
        if not deal_chat_id:
            for addr, info in monitored_addresses.items():
                if addr.lower() == provided_address.lower():
                    deal_chat_id = info.get('chat_id')
                    break
        
        if deal_chat_id and deal_chat_id in escrow_roles:
            roles = escrow_roles[deal_chat_id]
            buyer_info = roles.get('buyer')
            seller_info = roles.get('seller')
            transaction_id = roles.get('transaction_id', 'N/A')
            
            # Check if command was sent in the deal's escrow group
            current_chat_id = update.effective_chat.id
            belongs_to_this_chat = "Yes ✅" if current_chat_id == deal_chat_id else "No ❌"
            
            # Get buyer details and check availability
            buyer_name = "N/A"
            buyer_userid = "N/A"
            buyer_available = "No"
            if buyer_info:
                buyer_userid = buyer_info['user_id']
                try:
                    member = await context.bot.get_chat_member(chat_id=deal_chat_id, user_id=buyer_userid)
                    buyer_first_name = member.user.first_name or "Unknown"
                    buyer_name = f'<a href="tg://user?id={buyer_userid}">{buyer_first_name}</a>'
                    if member.status in ['member', 'administrator', 'creator']:
                        buyer_available = "Yes"
                    else:
                        buyer_available = "No"
                except Exception:
                    buyer_name = buyer_info.get('username', 'Unknown')
                    buyer_available = "No"
            
            # Get seller details and check availability
            seller_name = "N/A"
            seller_userid = "N/A"
            seller_available = "No"
            if seller_info:
                seller_userid = seller_info['user_id']
                try:
                    member = await context.bot.get_chat_member(chat_id=deal_chat_id, user_id=seller_userid)
                    seller_first_name = member.user.first_name or "Unknown"
                    seller_name = f'<a href="tg://user?id={seller_userid}">{seller_first_name}</a>'
                    if member.status in ['member', 'administrator', 'creator']:
                        seller_available = "Yes"
                    else:
                        seller_available = "No"
                except Exception:
                    seller_name = seller_info.get('username', 'Unknown')
                    seller_available = "No"
            
            verify_message = (
                f"<b>Address belongs to this chat's deal: {belongs_to_this_chat}</b>\n\n"
                f"<b>The provided address is valid and belong to bot.</b>\n\n"
                f"<b>Currently Assigned Deal: {transaction_id}</b>\n"
                f"<b>Buyer: {buyer_name}({buyer_userid})</b>\n"
                f"<b>Seller: {seller_name}({seller_userid})</b>\n"
                f"<b>Buyer Available in Deal Chat: {buyer_available}</b>\n"
                f"<b>Seller Available in Deal Chat: {seller_available}</b>"
            )
            
            await update.message.reply_text(verify_message, parse_mode='HTML')
        else:
            # Address belongs to bot but no active deal found for it
            await update.message.reply_text(
                "<b>The provided address is valid and belong to bot.</b>\n\n"
                "<b>No active deal is currently assigned to this address.</b>",
                parse_mode='HTML'
            )
    else:
        await update.message.reply_text(
            "<b>The provided adress is invalid and doesn't belongs to bot.</b>",
            parse_mode='HTML'
        )

async def refund_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /refund command - only buyer can use this"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id
    
    # Check if deal is completed
    if chat_id in escrow_roles and escrow_roles[chat_id].get('completed'):
        await update.message.reply_text(
            "<b>Sorry! please first use /dd first!</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if this chat has escrow roles set
    if chat_id not in escrow_roles:
        await update.message.reply_text(
            "<b>⚠️ This command can only be used in an escrow group.</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if user is buyer
    is_buyer = 'buyer' in escrow_roles[chat_id] and escrow_roles[chat_id]['buyer']['user_id'] == user_id
    
    if not is_buyer:
        # User is not buyer (either seller or someone else)
        await update.message.reply_text(
            "<b>Sorry! you are not allowed to use this command!</b>",
            parse_mode='HTML'
        )
        return
    
    # Buyer is allowed - check if amount is provided
    if not context.args or len(context.args) == 0:
        await update.message.reply_text(
            "<b>Please enter the amount you wish to refund.</b>\n\n"
            "Ex: <code>/refund 200</code>, <code>/refund all</code>",
            parse_mode='HTML'
        )
        return
    
    # Get escrow data
    buyer_info = escrow_roles[chat_id].get('buyer')
    seller_info = escrow_roles[chat_id].get('seller')
    token = escrow_roles[chat_id].get('selected_token')
    network = escrow_roles[chat_id].get('selected_network')
    escrow_address = escrow_roles[chat_id].get('escrow_address')
    
    if not all([buyer_info, seller_info, token, network, escrow_address]):
        await update.message.reply_text(
            "<b>⚠️ Missing escrow information. Please complete setup with /buyer, /seller, and /token commands.</b>",
            parse_mode='HTML'
        )
        return
    
    # Parse amount
    amount_str = ' '.join(context.args)
    current_balance = 0
    if escrow_address in monitored_addresses:
        current_balance = monitored_addresses[escrow_address]['total_balance']
    
    # Handle "all" or specific amount
    if amount_str.lower() == "all":
        refund_amount = current_balance
    else:
        try:
            refund_amount = float(amount_str)
        except ValueError:
            await update.message.reply_text(
                "<b>⚠️ Invalid amount! Use a number or 'all'.</b>",
                parse_mode='HTML'
            )
            return
    
    if refund_amount <= 0:
        await update.message.reply_text(
            "<b>⚠️ Amount must be greater than 0!</b>",
            parse_mode='HTML'
        )
        return
    
    if refund_amount > current_balance:
        await update.message.reply_text(
            f"<b>⚠️ Amount exceeds available balance!</b>\n\n"
            f"<b>Available:</b> {current_balance:.5f}",
            parse_mode='HTML'
        )
        return
    
    # Calculate fees (1% escrow fee)
    escrow_fee = refund_amount * 0.01  # 1% flat fee
    network_fee = 0.10  # Fixed network fee of $0.10
    ambassador_discount = 0.0  # Always 0
    ticket_discount = 0.0  # Always 0
    
    # Get network label
    network_label = TOKEN_DEFINITIONS[token]['networks'][network]['label'].upper()
    
    # Create confirmation message (paying to seller instead of buyer)
    confirmation_message = f"""<b>‼️ Refund Confirmation ‼️

🔒 Paying To: Seller[{seller_info['username']}]</b>
<b>💰 Amount:</b> {refund_amount:.5f} [{refund_amount:.2f}$]
<b>🌐 Network Fee:</b> {network_fee:.2f}$
<b>💷 Escrow Fee:</b> {escrow_fee:.5f} [{escrow_fee:.2f}$]

<b>📬 Address:</b> {seller_info['address']}
<b>🪙 Token:</b> {token}
<b>🌐 Network:</b> {network_label}

<b><u>(Network fee will be deducted from amount)</u></b>
<b><u>(Escrow fee will be deducted from total balance)</u></b>

<b>Are you ready to proceed with this refund?</b>
<b>Both the parties kindly confirm the same and note the action is irreversible.

For help: Hit /dispute to call an Administrator.</b>"""
    
    # Store refund data for button handlers
    refund_id = f"refund_{chat_id}_{int(datetime.now().timestamp())}"
    if 'pending_refunds' not in escrow_roles[chat_id]:
        escrow_roles[chat_id]['pending_refunds'] = {}
    
    escrow_roles[chat_id]['pending_refunds'][refund_id] = {
        'amount': refund_amount,
        'escrow_fee': escrow_fee,
        'network_fee': network_fee,
        'ambassador_discount': ambassador_discount,
        'ticket_discount': ticket_discount,
        'buyer_confirmed': False,
        'seller_confirmed': False,
        'message_id': None,
        'token': token,
        'network': network,
        'buyer_username': buyer_info['username'],
        'seller_username': seller_info['username'],
        'seller_userid': seller_info['user_id'],
        'seller_address': seller_info['address']
    }
    
    # Create buttons
    keyboard = [
        [InlineKeyboardButton("Buyer Confirmation ❌", callback_data=f"refund_buyer_confirm_{refund_id}")],
        [InlineKeyboardButton("Seller Confirmation ❌", callback_data=f"refund_seller_confirm_{refund_id}")],
        [InlineKeyboardButton("Reject ❌", callback_data=f"refund_reject_{refund_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send confirmation message
    confirmation_msg = await update.message.reply_text(
        confirmation_message,
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    
    # Store the message ID for later editing
    escrow_roles[chat_id]['pending_refunds'][refund_id]['message_id'] = confirmation_msg.message_id
    
    # Update log: Refund Stage
    escrow_roles[chat_id]['log_status'] = "Refund Stage"
    await update_log_message(context, chat_id)

async def send_refund_completion_message(context, chat_id, refund_data):
    """Send refund completion message after 10 seconds (payment to seller)"""
    await asyncio.sleep(10)
    
    try:
        # Deduct amount from escrow balance
        escrow_address = None
        if chat_id in escrow_roles:
            escrow_address = escrow_roles[chat_id].get('escrow_address')
        
        if escrow_address and escrow_address in monitored_addresses:
            # Deduct the amount from balance
            monitored_addresses[escrow_address]['total_balance'] -= refund_data['amount']
            
            # Check if balance reached 0 - mark deal as completed
            if monitored_addresses[escrow_address]['total_balance'] <= 0:
                monitored_addresses[escrow_address]['total_balance'] = 0
                if chat_id in escrow_roles:
                    escrow_roles[chat_id]['completed'] = True
        
        # Calculate final amount after fees
        final_amount = refund_data['amount'] - refund_data['escrow_fee'] - refund_data['network_fee']
        
        # Get network explorer link
        token = refund_data['token']
        network = refund_data['network']
        seller_address = refund_data['seller_address']
        
        # Determine explorer URL based on network
        if network == "BEP20":
            explorer_link = f"https://bscscan.com/address/{seller_address}"
        elif network == "TRC20":
            explorer_link = f"https://tronscan.org/address/{seller_address}"
        else:
            explorer_link = None
        
        # Format the completion message
        buyer_username = refund_data['buyer_username']
        seller_username = refund_data['seller_username']
        seller_userid = refund_data['seller_userid']
        token_symbol = token.upper()
        
        completion_text = f"""<b><u>{final_amount:.5f} {token_symbol} [{final_amount:.2f}$]</u></b> 💸 <b>+ NETWORK FEE has been released to the <u>Seller's</u> address! 🚀

Approved By:</b> {seller_username} <b>|</b> [{seller_userid}] <b>

Thank you for using @Easy_Escrow_Bot 🙌

{buyer_username} and {seller_username}, if you liked the bot please leave a good review about the bot and use command /vouch in reply to the review, and please also mention @Easy_Escrow_Bot in your vouch.</b>"""
        
        # Create keyboard with explorer link button if available
        if explorer_link:
            keyboard = [
                [InlineKeyboardButton("Link", url=explorer_link)]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            reply_markup = None
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=completion_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
        # Update log: Deal Refunded
        if chat_id in escrow_roles:
            escrow_roles[chat_id]['log_status'] = "Deal Refunded"
            await update_log_message(context, chat_id)
    except Exception as e:
        print(f"❌ Error sending refund completion message: {e}")

async def send_release_completion_message(context, chat_id, release_data):
    """Send release completion message after 10 seconds"""
    await asyncio.sleep(10)
    
    try:
        # Deduct amount from escrow balance
        escrow_address = None
        if chat_id in escrow_roles:
            escrow_address = escrow_roles[chat_id].get('escrow_address')
        
        if escrow_address and escrow_address in monitored_addresses:
            # Deduct the amount from balance
            monitored_addresses[escrow_address]['total_balance'] -= release_data['amount']
            
            # Check if balance reached 0 - mark deal as completed
            if monitored_addresses[escrow_address]['total_balance'] <= 0:
                monitored_addresses[escrow_address]['total_balance'] = 0
                if chat_id in escrow_roles:
                    escrow_roles[chat_id]['completed'] = True
        
        # Calculate final amount after fees
        final_amount = release_data['amount'] - release_data['escrow_fee'] - release_data['network_fee']
        
        # Get network explorer link
        token = release_data['token']
        network = release_data['network']
        buyer_address = release_data['buyer_address']
        
        # Determine explorer URL based on network
        if network == "BEP20":
            explorer_link = f"https://bscscan.com/address/{buyer_address}"
        elif network == "TRC20":
            explorer_link = f"https://tronscan.org/address/{buyer_address}"
        else:
            explorer_link = None
        
        # Format the completion message
        buyer_username = release_data['buyer_username']
        buyer_userid = release_data['buyer_userid']
        seller_username = release_data['seller_username']
        token_symbol = token.lower()
        
        token_upper = token.upper()
        completion_text = f"""<b><u>{final_amount:.5f} {token_upper} [{final_amount:.2f}$]</u></b> 💸 <b>+ NETWORK FEE has been released to the <u>Buyer's</u> address! 🚀

Approved By:</b> {buyer_username} <b>|</b> [{buyer_userid}] <b>

Thank you for using @Easy_Escrow_Bot 🙌

{buyer_username} and {seller_username}, if you liked the bot please leave a good review about the bot and use command /vouch in reply to the review, and please also mention @Easy_Escrow_Bot in your vouch.</b>"""
        
        # Create keyboard with explorer link button if available
        if explorer_link:
            keyboard = [
                [InlineKeyboardButton("Link", url=explorer_link)]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            reply_markup = None
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=completion_text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
        # Update log: Deal Completed
        if chat_id in escrow_roles:
            escrow_roles[chat_id]['log_status'] = "Deal Completed"
            await update_log_message(context, chat_id)
    except Exception as e:
        print(f"❌ Error sending release completion message: {e}")

async def release_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /release command - only seller can use this"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id
    
    # Check if deal is completed
    if chat_id in escrow_roles and escrow_roles[chat_id].get('completed'):
        await update.message.reply_text(
            "<b>Sorry! please first use /dd first!</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if this chat has escrow roles set
    if chat_id not in escrow_roles:
        await update.message.reply_text(
            "<b>⚠️ This command can only be used in an escrow group.</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if user is seller
    is_seller = 'seller' in escrow_roles[chat_id] and escrow_roles[chat_id]['seller']['user_id'] == user_id
    
    if not is_seller:
        # User is not seller (either buyer or someone else)
        await update.message.reply_text(
            "<b>Sorry! you are not allowed to use this command!</b>",
            parse_mode='HTML'
        )
        return
    
    # Seller is allowed - check if amount is provided
    if not context.args or len(context.args) == 0:
        await update.message.reply_text(
            "<b>Please enter the amount you wish to release.</b>\n\n"
            "Ex: <code>/release 200</code>, <code>/release all</code>",
            parse_mode='HTML'
        )
        return
    
    # Get escrow data
    buyer_info = escrow_roles[chat_id].get('buyer')
    seller_info = escrow_roles[chat_id].get('seller')
    token = escrow_roles[chat_id].get('selected_token')
    network = escrow_roles[chat_id].get('selected_network')
    escrow_address = escrow_roles[chat_id].get('escrow_address')
    
    if not all([buyer_info, seller_info, token, network, escrow_address]):
        await update.message.reply_text(
            "<b>⚠️ Missing escrow information. Please complete setup with /buyer, /seller, and /token commands.</b>",
            parse_mode='HTML'
        )
        return
    
    # Parse amount
    amount_str = ' '.join(context.args)
    current_balance = 0
    if escrow_address in monitored_addresses:
        current_balance = monitored_addresses[escrow_address]['total_balance']
    
    # Handle "all" or specific amount
    if amount_str.lower() == "all":
        release_amount = current_balance
    else:
        try:
            release_amount = float(amount_str)
        except ValueError:
            await update.message.reply_text(
                "<b>⚠️ Invalid amount! Use a number or 'all'.</b>",
                parse_mode='HTML'
            )
            return
    
    if release_amount <= 0:
        await update.message.reply_text(
            "<b>⚠️ Amount must be greater than 0!</b>",
            parse_mode='HTML'
        )
        return
    
    if release_amount > current_balance:
        await update.message.reply_text(
            f"<b>⚠️ Amount exceeds available balance!</b>\n\n"
            f"<b>Available:</b> {current_balance:.5f}",
            parse_mode='HTML'
        )
        return
    
    # Calculate fees (1% escrow fee)
    escrow_fee = release_amount * 0.01  # 1% flat fee
    network_fee = 0.10  # Fixed network fee of $0.10
    ambassador_discount = 0.0  # Always 0
    ticket_discount = 0.0  # Always 0
    
    # Get network label
    network_label = TOKEN_DEFINITIONS[token]['networks'][network]['label'].upper()
    
    # Create confirmation message
    confirmation_message = f"""<b>‼️ Release Confirmation ‼️

🔒 Paying To: Buyer[{buyer_info['username']}]</b>
<b>💰 Amount:</b> {release_amount:.5f} [{release_amount:.2f}$]
<b>🌐 Network Fee:</b> {network_fee:.2f}$
<b>💷 Escrow Fee:</b> {escrow_fee:.5f} [{escrow_fee:.2f}$]

<b>📬 Address:</b> {buyer_info['address']}
<b>🪙 Token:</b> {token}
<b>🌐 Network:</b> {network_label}

<b><u>(Network fee will be deducted from amount)</u></b>
<b><u>(Escrow fee will be deducted from total balance)</u></b>

<b>Are you ready to proceed with this withdrawal?</b>
<b>Both the parties kindly confirm the same and note the action is irreversible.

For help: Hit /dispute to call an Administrator.</b>"""
    
    # Store release data for button handlers
    release_id = f"release_{chat_id}_{int(datetime.now().timestamp())}"
    if 'pending_releases' not in escrow_roles[chat_id]:
        escrow_roles[chat_id]['pending_releases'] = {}
    
    escrow_roles[chat_id]['pending_releases'][release_id] = {
        'amount': release_amount,
        'escrow_fee': escrow_fee,
        'network_fee': network_fee,
        'ambassador_discount': ambassador_discount,
        'ticket_discount': ticket_discount,
        'buyer_confirmed': False,
        'seller_confirmed': False,
        'message_id': None,
        'token': token,
        'network': network,
        'buyer_username': buyer_info['username'],
        'buyer_userid': buyer_info['user_id'],
        'seller_username': seller_info['username'],
        'buyer_address': buyer_info['address']
    }
    
    # Create buttons - one by one (3 separate lines)
    keyboard = [
        [InlineKeyboardButton("Buyer Confirmation ❌", callback_data=f"release_buyer_confirm_{release_id}")],
        [InlineKeyboardButton("Seller Confirmation ❌", callback_data=f"release_seller_confirm_{release_id}")],
        [InlineKeyboardButton("Reject ❌", callback_data=f"release_reject_{release_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send confirmation message
    confirmation_msg = await update.message.reply_text(
        confirmation_message,
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    
    # Store the message ID for later editing
    escrow_roles[chat_id]['pending_releases'][release_id]['message_id'] = confirmation_msg.message_id
    
    # Update log: Release Stage
    escrow_roles[chat_id]['log_status'] = "Release Stage"
    await update_log_message(context, chat_id)

async def fakedepo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /fakedepo command - admin only, sets fixed addresses for a chat"""
    user_id = update.effective_user.id
    chat = update.effective_chat
    
    # Check if user is admin
    if user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "<b>⚠️ This command is only available to admins.</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if used in DM
    if chat.type != 'private':
        await update.message.reply_text(
            "<b>⚠️ This command can only be used in bot's DM.</b>",
            parse_mode='HTML'
        )
        return
    
    # Check if chat_id argument is provided
    if not context.args or len(context.args) == 0:
        await update.message.reply_text(
            "<b>Please provide a chat ID.</b>\n\n"
            "Ex: <code>/fakedepo -1001234567890</code>",
            parse_mode='HTML'
        )
        return
    
    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "<b>❌ Invalid chat ID. Please provide a valid numeric chat ID.</b>",
            parse_mode='HTML'
        )
        return
    
    # Set fixed addresses for this chat
    fake_deposit_addresses[target_chat_id] = {
        'BEP20': '0xf282e789e835ed379aea84ece204d2d643e6774f',
        'TRC20': 'THb2Do8gmwEBocTGaduh73q6EwxfcX9Vx4'
    }
    
    await update.message.reply_text(
        f"<b>✅ Fake deposit addresses set for chat {target_chat_id}</b>\n\n"
        f"<b>BEP20:</b> <code>0xf282e789e835ed379aea84ece204d2d643e6774f</code>\n"
        f"<b>TRC20:</b> <code>THb2Do8gmwEBocTGaduh73q6EwxfcX9Vx4</code>",
        parse_mode='HTML'
    )

async def track_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track when members join and auto-promote admins"""
    result = update.chat_member
    
    # Check if this is a new member joining (status changed from non-member to member)
    was_member = result.old_chat_member.status in ['member', 'administrator', 'creator']
    is_member = result.new_chat_member.status in ['member', 'administrator', 'creator']
    
    # Only process if someone just joined
    if not was_member and is_member:
        user_id = result.new_chat_member.user.id
        chat_id = result.chat.id
        
        # Check if the user is in the admin list
        if user_id in ADMIN_IDS:
            try:
                # Promote the admin with full permissions
                await context.bot.promote_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    can_manage_chat=True,
                    can_delete_messages=True,
                    can_manage_video_chats=True,
                    can_restrict_members=True,
                    can_promote_members=True,
                    can_change_info=True,
                    can_invite_users=True,
                    can_pin_messages=True,
                    can_post_messages=True
                )
                print(f"✅ Auto-promoted admin {user_id} in chat {chat_id}")
            except Exception as e:
                print(f"Failed to promote admin {user_id}: {e}")

def main():
    if not BOT_TOKEN:
        print("❌ Error: ESCROW_BOT_TOKEN environment variable not set!")
        print("Please set your Telegram bot token in Secrets.")
        return
    
    if not API_ID or not API_HASH or not PHONE:
        print("⚠️  Warning: Telegram user account credentials not configured!")
        print("   Group creation will not work without:")
        print("   - TELEGRAM_API_ID")
        print("   - TELEGRAM_API_HASH")
        print("   - TELEGRAM_PHONE")
        print("   Get credentials from https://my.telegram.org/apps")
        print("")
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("dispute", dispute_command))
    app.add_handler(CommandHandler("dd", dd_command))
    app.add_handler(CommandHandler("buyer", buyer_command))
    app.add_handler(CommandHandler("seller", seller_command))
    app.add_handler(CommandHandler("token", token_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("verify", verify_command))
    app.add_handler(CommandHandler("blacklist", blacklist_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("release", release_command))
    app.add_handler(CommandHandler("refund", refund_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(ChatMemberHandler(track_chat_members, ChatMemberHandler.CHAT_MEMBER))
    
    # Start deposit monitoring and Pyrogram client in background
    async def post_init(application):
        global user_client_started
        # Initialize monitoring for all tokens from TOKEN_DEFINITIONS
        initialize_all_token_monitoring()
        # Start Pyrogram user client if configured
        if user_client:
            try:
                await user_client.start()
                user_client_started = True
                print("✅ Pyrogram user client started successfully")
            except Exception as e:
                user_client_started = False
                print(f"⚠️  Pyrogram user client failed to start: {e}")
                print("   Group creation (/escrow) will not be available")
        # Start deposit monitoring
        asyncio.create_task(monitor_deposits(application))
    
    # Shutdown hook to properly stop Pyrogram client
    async def post_shutdown(application):
        # Stop user client only if it successfully started
        if user_client_started and user_client and user_client.is_connected:
            try:
                await user_client.stop()
                print("✅ Pyrogram user client stopped")
            except Exception as e:
                print(f"⚠️  Error stopping Pyrogram client: {e}")
    
    app.post_init = post_init
    app.post_shutdown = post_shutdown
    
    print("✅ @Easy_Escorw_Bot is running...")
    if BSCSCAN_API_KEY and TRONGRID_API_KEY:
        print("✅ Blockchain monitoring enabled (BSC & TRON)")
    else:
        print("⚠️  Blockchain monitoring disabled (API keys not configured)")
    
    app.run_polling()

if __name__ == "__main__":
    main()
