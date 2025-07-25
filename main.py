#!/usr/bin/env python
# pylint: disable=unused-argument, import-error, logging-fstring-interpolation, global-statement, fixme

"""
Ethereum Addresses Tracker Bot

This bot monitors Ethereum wallet addresses for USDT transactions and sends
notifications via Telegram when new transactions are detected.

Enhanced Error Reporting:
The bot implements comprehensive error reporting to improve debugging and monitoring:

1. Admin Notification System:
   - Critical errors are sent to the admin chat via Telegram
   - Includes contextual information and stack traces when helpful
   - Gracefully handles notification failures to avoid infinite recursion

2. Error Handling Strategy:
   - API failures: Logged with context, admin notified, exceptions re-raised
   - Missing secrets: Detailed error messages, startup failure with admin notification
   - Database errors: Logged and reported, fail-safe behavior (duplicate notifications vs missed transactions)
   - Telegram API errors: Isolated handling to prevent notification loop failures

3. Error Categories:
   - Startup errors: Configuration and credential issues
   - Runtime errors: API failures, network issues, data processing errors
   - Critical errors: Database corruption, unexpected exceptions

4. Fail-Safe Approach:
   - Prefer duplicate notifications over missed transactions
   - Continue operation when possible to maintain service availability
   - Clear error messages with actionable information for maintainers

All error handling blocks include contextual information, appropriate logging levels,
and admin notifications for issues requiring human intervention.
"""

import os
import sys
import json
import requests
import html
import traceback
from requests.exceptions import HTTPError, RequestException

import shelve

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

USDT_CONTRACT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
ETHERSCAN_API_KEY = None
WALLET_ADDRESS = None
TG_CHAT_ID = None
TG_BOT_TOKEN = None

HTTP_TIMEOUT = 5


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends explanation on how to use the bot."""
    await update.message.reply_text("Hi! Let's get started!")


async def send_admin_alert(message: str, include_traceback: bool = False) -> None:
    """
    Send critical error alert to admin chat.

    This function provides a centralized way to notify administrators of critical
    failures that require attention. It handles errors gracefully to avoid
    infinite recursion in error reporting.

    Args:
        message: The error message to send to admin
        include_traceback: Whether to include the current stack trace
    """
    try:
        if not TG_BOT_TOKEN or not TG_CHAT_ID:
            logger.warning("Cannot send admin alert: TG_BOT_TOKEN or TG_CHAT_ID not configured")
            return

        alert_text = f"🚨 CRYPTO TRACKER ALERT 🚨\n\n{message}"

        if include_traceback:
            tb_str = traceback.format_exc()
            # Limit traceback length to avoid Telegram message limits
            if len(tb_str) > 1000:
                tb_str = tb_str[:1000] + "... (truncated)"
            alert_text += f"\n\nStacktrace:\n```\n{tb_str}\n```"

        # Use direct HTTP request to avoid dependency on Application instance
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TG_CHAT_ID,
            "text": alert_text,
            "parse_mode": "Markdown"
        }

        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Admin alert sent successfully")

    except Exception as e:
        # Avoid infinite recursion by logging instead of sending another alert
        logger.error(f"Failed to send admin alert: {e}")


def send_admin_alert_sync(message: str, include_traceback: bool = False) -> None:
    """
    Synchronous wrapper for send_admin_alert.

    Used in contexts where async/await is not available.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're already in an async context, create a task
            loop.create_task(send_admin_alert(message, include_traceback))
        else:
            # If no loop is running, run the coroutine
            loop.run_until_complete(send_admin_alert(message, include_traceback))
    except RuntimeError:
        # If no event loop exists, create a new one
        asyncio.run(send_admin_alert(message, include_traceback))


def get_latest_tx(token: str, contract: str, address: str) -> dict:
    """
    Get the latest transaction for a given address on ETH blockchain.

    Enhanced error reporting: Critical failures are reported to admin chat
    and include contextual information for debugging.
    """

    url = "https://api.etherscan.io/api"
    params = {
        "module": "account",
        "action": "tokentx",
        "contractaddress": contract,
        "address": address,
        "page": 1,
        "offset": 10,
        "startblock": 0,
        "endblock": 99999999,
        "sort": "desc",
        "apikey": token,
    }

    try:
        response = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()

        response_json = response.json()
        if "message" in response_json and response_json["message"] == "NOTOK":
            error_msg = f"Etherscan API returned NOTOK for address {address}: {response_json}"
            logger.error(error_msg)
            send_admin_alert_sync(f"Etherscan API Error: {error_msg}")
            raise RuntimeError(response.json())
        data = response.json()
        result = data.get("result", [])

        if not result:
            logger.warning(f"No transactions found for address {address}")
            return None

        return result[0]

    except HTTPError as http_e:
        error_msg = f"HTTP error fetching transactions for {address}: {http_e} (Status: {response.status_code if 'response' in locals() else 'Unknown'})"
        logger.error(error_msg)
        send_admin_alert_sync(f"HTTP Error in get_latest_tx: {error_msg}")
        raise http_e  # Re-raise for caller to handle

    except (RequestException, RuntimeError) as e:
        error_msg = f"Request/Runtime error fetching transactions for {address}: {e}"
        logger.error(error_msg)
        send_admin_alert_sync(f"Request Error in get_latest_tx: {error_msg}")
        raise e  # Re-raise for caller to handle

    except json.JSONDecodeError as json_e:
        error_msg = f"JSON decode error for address {address}: {json_e}"
        logger.error(error_msg)
        send_admin_alert_sync(f"JSON Decode Error in get_latest_tx: {error_msg}")
        raise json_e  # Re-raise for caller to handle

    except Exception as e:
        error_msg = f"Unexpected error in get_latest_tx for address {address}: {e}"
        logger.error(error_msg, exc_info=True)
        send_admin_alert_sync(f"Unexpected Error in get_latest_tx: {error_msg}", include_traceback=True)
        raise e  # Re-raise for caller to handle


def is_new_tx(tx_hash: str) -> bool:
    """
    Check if the transaction is new.

    Enhanced error reporting: Database errors are logged with context
    and reported to admin for persistent storage issues.
    """
    try:
        # TODO: Keeping only last X transactions
        with shelve.open("tx") as db:
            if tx_hash in db:
                logger.debug(f"Transaction {tx_hash} already processed")
                return False
            db[tx_hash] = True
            logger.debug(f"Marked transaction {tx_hash} as processed")
            return True
    except Exception as e:
        error_msg = f"Error accessing transaction database for {tx_hash}: {e}"
        logger.error(error_msg, exc_info=True)
        send_admin_alert_sync(f"Database Error: {error_msg}", include_traceback=True)
        # Return True to avoid missing transactions due to DB errors
        # This may cause duplicate notifications but is safer than missing transactions
        return True


def get_direction(transaction: dict, address: str) -> str:
    """Detect transaction direction."""

    if transaction["from"].lower() == address.lower():
        direction = "📤 Outgoing"
    elif transaction["to"].lower() == address.lower():
        direction = "📥 Incoming"
    else:
        direction = "Unknown"
    return direction


async def callback_minute(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Keep track of ETH transactions regularly.

    Enhanced error reporting: Handles API failures gracefully and provides
    detailed error information to admins when critical issues occur.
    """
    try:
        tx = get_latest_tx(
            token=ETHERSCAN_API_KEY, contract=USDT_CONTRACT, address=WALLET_ADDRESS
        )

        if tx is None:
            logger.debug("No transactions found or API returned empty result")
            return

        logger.debug(f"The latest transaction is {tx}")
        tx_hash = tx.get("hash")

        if not tx_hash:
            logger.warning("Transaction data missing hash field")
            return

        usdt = float(tx.get("value", 0)) / 10**6

        if is_new_tx(tx_hash):
            try:
                etherscan_link = f'<a href="https://etherscan.io/tx/{html.escape(tx_hash)}">Etherscan</a>'
                direction = get_direction(transaction=tx, address=WALLET_ADDRESS)

                await context.bot.send_message(
                    chat_id=context.job.chat_id,
                    text=f"{direction} ETH transaction detected {etherscan_link} {usdt:.2f} USDT",
                    parse_mode=ParseMode.HTML,
                )
                logger.info(f"Sent notification for transaction {tx_hash}")

            except Exception as telegram_e:
                error_msg = f"Failed to send transaction notification for {tx_hash}: {telegram_e}"
                logger.error(error_msg, exc_info=True)
                await send_admin_alert(f"Telegram Notification Error: {error_msg}", include_traceback=True)
        else:
            logger.debug(f"Transaction {tx_hash} already processed")

    except Exception as e:
        error_msg = f"Critical error in callback_minute: {e}"
        logger.error(error_msg, exc_info=True)
        await send_admin_alert(f"Callback Error: {error_msg}", include_traceback=True)
        # Continue running despite errors to maintain service availability


def read_docker_secret(secret_name: str) -> str:
    """
    Read secret from Docker secret file.

    Enhanced error reporting: Missing secrets are reported to admin if possible
    and include contextual information for debugging.
    """
    secret_path = f"/run/secrets/{secret_name}"
    try:
        with open(secret_path, "r", encoding="utf-8") as secret_file:
            secret_value = secret_file.read().strip()
            if not secret_value:
                error_msg = f"Secret '{secret_name}' is empty at {secret_path}"
                logger.error(error_msg)
                # Only send admin alert if we have bot credentials (avoid bootstrap problem)
                if secret_name not in ["tg_bot_token", "tg_chat_id"]:
                    send_admin_alert_sync(f"Empty Secret: {error_msg}")
                raise ValueError(error_msg)
            return secret_value
    except FileNotFoundError as e:
        error_msg = f"Secret '{secret_name}' not found at {secret_path}"
        logger.error(error_msg)
        # Only send admin alert if we have bot credentials (avoid bootstrap problem)
        if secret_name not in ["tg_bot_token", "tg_chat_id"]:
            send_admin_alert_sync(f"Missing Secret: {error_msg}")
        raise FileNotFoundError(error_msg) from e
    except PermissionError as e:
        error_msg = f"Permission denied reading secret '{secret_name}' at {secret_path}"
        logger.error(error_msg)
        # Only send admin alert if we have bot credentials
        if secret_name not in ["tg_bot_token", "tg_chat_id"]:
            send_admin_alert_sync(f"Permission Error: {error_msg}")
        raise PermissionError(error_msg) from e
    except Exception as e:
        error_msg = f"Unexpected error reading secret '{secret_name}': {e}"
        logger.error(error_msg, exc_info=True)
        # Only send admin alert if we have bot credentials
        if secret_name not in ["tg_bot_token", "tg_chat_id"]:
            send_admin_alert_sync(f"Secret Read Error: {error_msg}", include_traceback=True)
        raise e


def load_global_secrets() -> None:
    """
    Load secrets from docker secrets.

    Enhanced error reporting: Missing or invalid secrets cause startup failure
    with detailed error messages sent to admin (if possible).
    """
    global ETHERSCAN_API_KEY, WALLET_ADDRESS, TG_CHAT_ID, TG_BOT_TOKEN

    try:
        # Load Telegram credentials first to enable admin notifications
        TG_BOT_TOKEN = read_docker_secret("tg_bot_token")
        TG_CHAT_ID = read_docker_secret("tg_chat_id")

        # Now load other secrets with admin notification capability
        ETHERSCAN_API_KEY = read_docker_secret("etherscan_api_key")
        WALLET_ADDRESS = read_docker_secret("wallet_address")

        logger.info("All secrets loaded successfully")

    except Exception as e:
        error_msg = f"Failed to load required secrets: {e}"
        logger.error(error_msg, exc_info=True)
        raise RuntimeError(error_msg) from e


def main() -> None:
    """
    Run bot.

    Enhanced error reporting: Provides detailed startup error information
    and graceful shutdown handling.
    """
    try:
        logger.info("Starting Crypto Wallet Tracker Bot...")

        # Load all required secrets
        load_global_secrets()

        # Validate required secrets are not None
        if not all([ETHERSCAN_API_KEY, WALLET_ADDRESS, TG_CHAT_ID, TG_BOT_TOKEN]):
            missing_secrets = []
            if not ETHERSCAN_API_KEY:
                missing_secrets.append("etherscan_api_key")
            if not WALLET_ADDRESS:
                missing_secrets.append("wallet_address")
            if not TG_CHAT_ID:
                missing_secrets.append("tg_chat_id")
            if not TG_BOT_TOKEN:
                missing_secrets.append("tg_bot_token")

            error_msg = f"Missing required secrets: {', '.join(missing_secrets)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Create the Application and pass it your bot's token
        try:
            application = Application.builder().token(TG_BOT_TOKEN).build()
            job_queue = application.job_queue
        except Exception as app_e:
            error_msg = f"Failed to create Telegram application: {app_e}"
            logger.error(error_msg, exc_info=True)
            send_admin_alert_sync(f"App Creation Error: {error_msg}", include_traceback=True)
            raise RuntimeError(error_msg) from app_e

        # Register command handlers
        application.add_handler(CommandHandler(["start", "help"], start))

        # Schedule periodic transaction checking
        try:
            job_queue.run_repeating(
                callback_minute, interval=60, first=10, chat_id=TG_CHAT_ID
            )
            logger.info("Scheduled transaction monitoring job")
        except Exception as job_e:
            error_msg = f"Failed to schedule monitoring job: {job_e}"
            logger.error(error_msg, exc_info=True)
            send_admin_alert_sync(f"Job Schedule Error: {error_msg}", include_traceback=True)
            raise RuntimeError(error_msg) from job_e

        logger.info("Bot initialization completed successfully")
        send_admin_alert_sync("🚀 Crypto Wallet Tracker Bot started successfully!")

        # Run the bot until the user presses Ctrl-C
        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as polling_e:
            error_msg = f"Error during bot polling: {polling_e}"
            logger.error(error_msg, exc_info=True)
            send_admin_alert_sync(f"Polling Error: {error_msg}", include_traceback=True)
            raise RuntimeError(error_msg) from polling_e

    except (FileNotFoundError, RuntimeError, ValueError) as e:
        error_msg = f"Startup error: {e}"
        logger.error(error_msg, exc_info=True)
        # Try to send admin alert if possible (may fail if secrets not loaded)
        try:
            send_admin_alert_sync(f"🚨 Bot Startup Failed: {error_msg}", include_traceback=True)
        except Exception:
            logger.error("Could not send startup failure alert to admin")
        sys.exit(1)
    except Exception as e:
        error_msg = f"Unexpected startup error: {e}"
        logger.error(error_msg, exc_info=True)
        try:
            send_admin_alert_sync(f"🚨 Unexpected Bot Failure: {error_msg}", include_traceback=True)
        except Exception:
            logger.error("Could not send unexpected failure alert to admin")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted")
        try:
            sys.exit(130)
        except SystemExit:
            os._exit(130)
