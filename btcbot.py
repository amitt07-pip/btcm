"""
Run Easy Escrow Bot (@Easy_Escorw_Bot)
"""
import asyncio
import sys
import types
import os

if sys.version_info >= (3, 13):
    sys.modules["imghdr"] = types.ModuleType("imghdr")

def main():
    escrow_token = os.getenv("ESCROW_BOT_TOKEN")
    if not escrow_token:
        print("❌ ERROR: ESCROW_BOT_TOKEN not set!")
        sys.exit(1)
    
    print("✅ Easy Escrow Bot (@Easy_Escorw_Bot) - Starting...")
    
    # Initialize database
    from database import init_db
    init_db()
    
    import escrowbot as escrow_bot
    
    # Load all deals from database into memory
    escrow_bot.load_deals_from_database()
    
    # Load escrow balances from database
    escrow_bot.load_balances_from_database()
    from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ChatMemberHandler, MessageHandler, filters
    
    app = ApplicationBuilder().token(escrow_token).build()
    
    app.add_handler(CommandHandler("start", escrow_bot.start_command))
    app.add_handler(CommandHandler("menu", escrow_bot.menu_command))
    app.add_handler(CommandHandler("escrow", escrow_bot.escrow_command))
    app.add_handler(CommandHandler("dispute", escrow_bot.dispute_command))
    app.add_handler(CommandHandler("dd", escrow_bot.dd_command))
    app.add_handler(CommandHandler("buyer", escrow_bot.buyer_command))
    app.add_handler(CommandHandler("seller", escrow_bot.seller_command))
    app.add_handler(CommandHandler("token", escrow_bot.token_command))
    app.add_handler(CommandHandler("deposit", escrow_bot.deposit_command))
    app.add_handler(CommandHandler("balance", escrow_bot.balance_command))
    app.add_handler(CommandHandler("verify", escrow_bot.verify_command))
    app.add_handler(CommandHandler("refund", escrow_bot.refund_command))
    app.add_handler(CommandHandler("release", escrow_bot.release_command))
    app.add_handler(CommandHandler("fakedepo", escrow_bot.fakedepo_command))
    app.add_handler(CommandHandler("add", escrow_bot.add_command))
    app.add_handler(CommandHandler("blacklist", escrow_bot.blacklist_command))
    app.add_handler(CommandHandler("changeaddy", escrow_bot.changeaddy_command))
    app.add_handler(CommandHandler("setaddy", escrow_bot.setaddy_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, escrow_bot.handle_text_message))
    app.add_handler(CallbackQueryHandler(escrow_bot.button_callback))
    app.add_handler(ChatMemberHandler(escrow_bot.track_chat_members, ChatMemberHandler.CHAT_MEMBER))
    
    async def post_init(application):
        # Start Telethon user client for group creation
        if escrow_bot.user_client:
            try:
                await escrow_bot.user_client.connect()
                if not await escrow_bot.user_client.is_user_authorized():
                    raise Exception("Session is not authorized")
                escrow_bot.user_client_started = True
                print("✅ Telethon client started - Group creation enabled")
            except Exception as e:
                error_msg = str(e).lower()
                if "unpack" in error_msg or "buffer" in error_msg or "incorrect padding" in error_msg:
                    print(f"❌ Failed to start Telethon: Invalid session string format")
                    print("⚠️ The TELEGRAM_SESSION_STRING is corrupted or incomplete.")
                elif "auth" in error_msg or "404" in error_msg or "unauthorized" in error_msg:
                    print(f"❌ Failed to start Telethon: Session is expired or invalid")
                    print("⚠️ The session doesn't match your API credentials or has been revoked.")
                else:
                    print(f"⚠️ Failed to start Telethon: {e}")
                
                print("⚠️ Group creation will NOT be available")
                print("⚠️ ")
                print("⚠️ To fix this:")
                print("⚠️ 1. Go to Secrets and DELETE 'TELEGRAM_SESSION_STRING'")
                print("⚠️ 2. Open Shell tab and run: python telethon_login.py")
                print("⚠️ 3. Copy the NEW session string (starts with '1' and is very long)")
                print("⚠️ 4. Add it back to Secrets as 'TELEGRAM_SESSION_STRING'")
                print("⚠️ 5. Restart this workflow")
                print("⚠️ ")
                
                # Clear the client so it doesn't keep retrying
                escrow_bot.user_client = None
        
        asyncio.create_task(escrow_bot.monitor_deposits(application))
    
    async def post_shutdown(application):
        # Stop Telethon user client on shutdown
        if escrow_bot.user_client and escrow_bot.user_client_started:
            try:
                await escrow_bot.user_client.disconnect()
                print("✅ Telethon client stopped")
            except Exception as e:
                print(f"⚠️ Error stopping Telethon client: {e}")
    
    app.post_init = post_init
    app.post_shutdown = post_shutdown
    
    print("✅ @Easy_Escorw_Bot is running...")
    print("✅ Bot is now polling for updates...")
    
    app.run_polling()

if __name__ == "__main__":
    main()
