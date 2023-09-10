#!/usr/bin/env python
# pylint: disable=unused-argument, import-error, logging-fstring-interpolation, global-statement, fixme

import os
import json
import requests
import shelve

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

USDT_CONTRACT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
ETHERSCAN_TOKEN = "undefined"
WALLET_ADDRESS = "undefined"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends explanation on how to use the bot."""
    await update.message.reply_text("Hi! Let's get started!")


def get_latest_tx(token: str, contract: str, address: str) -> dict:
    """Get the latest transaction for a given address on ETH blockchain."""

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
    response = requests.get(url, params=params, timeout=10)
    data = json.loads(response.text)

    result = data.get("result", [])
    logging.debug(f"Fetched {len(result)} transactions for {address} on ETH blockchain")

    if not isinstance(result, list):
        logging.error(
            f"Error fetching transactions for {address} on ETH blockchain: {data}"
        )
    return result[0]


def is_new_tx(tx_hash: str) -> bool:
    """Check if the transaction is new."""

    # TODO: Keeping only last X transactions
    with shelve.open("tx") as db:
        if tx_hash in db:
            logging.debug(f"Transaction {tx_hash} already processed")
            return False
        db[tx_hash] = True
        return True


def get_direction(transaction: dict, address: str) -> str:
    """Detect transaction direction."""

    if transaction["from"].lower() == address.lower():
        direction = "📤 Outgoing"
    elif transaction["to"].lower() == address.lower:
        direction = "📥 Incoming"
    else:
        direction = "Unknown"
    return direction


async def callback_minute(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep track of ETH transactions regularly."""

    tx = get_latest_tx(
        token=ETHERSCAN_TOKEN, contract=USDT_CONTRACT, address=WALLET_ADDRESS
    )
    if tx is not None:
        logger.debug(f"The latest transaction is {tx}")
        tx_hash = tx.get("hash")
        usdt = float(tx.get("value")) / 10**6
        if is_new_tx(tx_hash):
            etherscan_link = (
                f'<a href="https://etherscan.io/tx/{tx_hash}">Etherscan</a>'
            )
            direction = get_direction(transaction=tx, address=WALLET_ADDRESS)
            await context.bot.send_message(
                chat_id=context.job.chat_id,
                text=f"{direction} ETH transaction detected {etherscan_link} {usdt:.2f} USDT",
                parse_mode=ParseMode.HTML,
            )


def get_secret(key: str, default: str) -> str:
    """Get secret from environment variable or from file."""

    value = os.getenv(key, default)
    if os.path.isfile(value):
        with open(value, encoding="utf-8") as f:
            file_contents = f.readlines()
            file_contents = [line.rstrip() for line in file_contents]
            file_contents = "".join(file_contents)
            return file_contents
    return value


def main() -> None:
    """Run bot."""
    global ETHERSCAN_TOKEN
    global WALLET_ADDRESS

    ETHERSCAN_TOKEN = get_secret("ETHERSCAN_API_KEY_FILE", "undefined")
    WALLET_ADDRESS = get_secret("WALLET_ADDRESS_FILE", "undefined")

    tg_chat_id = get_secret("TELEGRAM_CHAT_ID_FILE", "undefined")
    tg_token = get_secret("TELEGRAM_BOT_TOKEN_FILE", "undefined")

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(tg_token).build()
    job_queue = application.job_queue

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler(["start", "help"], start))

    job_queue.run_repeating(callback_minute, interval=60, first=10, chat_id=tg_chat_id)

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
