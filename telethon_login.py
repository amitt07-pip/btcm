"""
Generate a Telethon session string for the escrow bot.

Usage:
    pip install telethon
    python telethon_login.py

You will be prompted for:
    1. API ID and API Hash (from https://my.telegram.org)
    2. Your phone number (with country code, e.g. +91XXXXXXXXXX)
    3. The OTP code Telegram sends you
    4. Your 2FA password (if enabled)

The script prints a session string that you should set as
the TELEGRAM_SESSION_STRING environment variable.
"""
import os
import sys

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    print("❌ Telethon is not installed. Run: pip install telethon")
    sys.exit(1)


def main():
    print("=" * 50)
    print("  Telethon Session String Generator")
    print("  For Easy Escrow Bot (@Easy_Escorw_Bot)")
    print("=" * 50)
    print()
    print("You need your API ID and API Hash from https://my.telegram.org")
    print()

    # Try environment variables first, fall back to interactive input
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    if api_id:
        print(f"Using TELEGRAM_API_ID from environment: {api_id}")
    else:
        api_id = input("Enter your API ID: ").strip()

    if api_hash:
        print("Using TELEGRAM_API_HASH from environment.")
    else:
        api_hash = input("Enter your API Hash: ").strip()

    if not api_id or not api_hash:
        print("❌ API ID and API Hash are required.")
        sys.exit(1)

    try:
        api_id = int(api_id)
    except ValueError:
        print("❌ API ID must be a number.")
        sys.exit(1)

    print()
    print("Connecting to Telegram...")
    print("You will be asked for your phone number and a verification code.")
    print()

    try:
        with TelegramClient(StringSession(), api_id, api_hash) as client:
            session_string = client.session.save()

            print()
            print("=" * 50)
            print("  SESSION STRING (copy the entire value below)")
            print("=" * 50)
            print()
            print(session_string)
            print()
            print("=" * 50)
            print()
            print("Set this as your TELEGRAM_SESSION_STRING environment variable.")
            print("Make sure you copy the ENTIRE string — it is very long.")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
