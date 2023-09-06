#! !/usr/bin/env python3

import requests
import json
import time
import os
import sys
from web3 import Web3


def get_secret(key, default):
    value = os.getenv(key, default)
    if os.path.isfile(value):
        with open(value) as f:
            file_contents = f.readlines()
            file_contents = [line.rstrip() for line in file_contents]
            file_contents = "".join(file_contents)
            return file_contents
    return value


ETHERSCAN_API_KEY = get_secret("ETHERSCAN_API_KEY", "undefined")
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "undefined")
TELEGRAM_CHAT_ID = get_secret("TELEGRAM_CHAT_ID", "undefined")

USDT_CONTRACT_ADDRESS = "0xdac17f958d2ee523a2206206994597c13d831ec7"

WALLETS = os.environ.get("WALLETS", "watched_wallets.txt")
TMP = os.environ.get("TMP", "temp.txt")
HASHES_PATH = os.environ.get("HASHES_PATH", "latest_tx_hashes.json")
LAST_RUN_TIME_PATH = os.environ.get("LAST_RUN_TIME_PATH", "last_run_time.txt")


# Define some helper functions
def get_wallet_transactions(wallet_address, blockchain="eth"):
    url = f"https://api.etherscan.io/api?module=account&contractaddress={USDT_CONTRACT_ADDRESS}&action=txlist&address={wallet_address}&sort=desc&apikey={ETHERSCAN_API_KEY}"
    response = requests.get(url)
    data = json.loads(response.text)

    result = data.get("result", [])
    if not isinstance(result, list):
        print(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error fetching transactions for {wallet_address} on {blockchain.upper()} blockchain: {data}"
        )
        return []

    return result


def send_telegram_notification(message, value, usd_value, tx_hash, blockchain="eth"):
    etherscan_link = f'<a href="https://etherscan.io/tx/{tx_hash}">Etherscan</a>'

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": f"{TELEGRAM_CHAT_ID}",
        "text": f"{message}: {etherscan_link}\nValue: {value:.6f} {blockchain.upper()} (${usd_value:.2f})",
        "parse_mode": "HTML",
    }
    response = requests.post(url, data=payload)
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Telegram notification sent with message: {message}, value: {value} {blockchain.upper()} (${usd_value:.2f})"
    )
    return response


def monitor_wallets():
    watched_wallets = set()
    file_path = WALLETS

    if not os.path.exists(file_path):
        open(file_path, "w").close()

    latest_tx_hashes = {}
    latest_tx_hashes_path = HASHES_PATH
    if os.path.exists(latest_tx_hashes_path):
        with open(latest_tx_hashes_path, "r") as f:
            latest_tx_hashes = json.load(f)

    last_run_time = 0
    last_run_time_path = LAST_RUN_TIME_PATH
    if os.path.exists(last_run_time_path):
        with open(last_run_time_path, "r") as f:
            last_run_time = int(f.read())

    while True:
        try:
            # Fetch current ETH prices in USD from CoinGecko API
            eth_usd_price_url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum%2Cbinancecoin&vs_currencies=usd"
            response = requests.get(eth_usd_price_url)
            data = json.loads(response.text)
            eth_usd_price = data["ethereum"]["usd"]

            # Read from file
            with open(file_path, "r") as f:
                watched_wallets = set(f.read().splitlines())

            for wallet in watched_wallets:
                blockchain, wallet_address = wallet.split(":")
                transactions = get_wallet_transactions(wallet_address, blockchain)
                for tx in transactions:
                    tx_hash = tx["hash"]
                    tx_time = int(tx["timeStamp"])

                    if tx_hash not in latest_tx_hashes and tx_time > last_run_time:
                        if tx["to"].lower() == wallet_address.lower():
                            value = (
                                float(tx["value"]) / 10**18
                            )  # Convert from wei to ETH
                            usd_value = value * eth_usd_price  # Calculate value in USD
                            message = (
                                f"🚨 Incoming transaction detected on {wallet_address}"
                            )
                            send_telegram_notification(
                                message, value, usd_value, tx["hash"]
                            )
                        elif tx["from"].lower() == wallet_address.lower():
                            value = (
                                float(tx["value"]) / 10**18
                            )  # Convert from wei to ETH
                            usd_value = value * eth_usd_price  # Calculate value in USD
                            message = (
                                f"🚨 Outgoing transaction detected on {wallet_address}"
                            )
                            send_telegram_notification(
                                message, value, usd_value, tx["hash"]
                            )

                        latest_tx_hashes[tx_hash] = int(tx["blockNumber"])

            # Save latest_tx_hashes to file
            with open(latest_tx_hashes_path, "w") as f:
                json.dump(latest_tx_hashes, f)

            # Update last_run_time
            last_run_time = int(time.time())
            with open(last_run_time_path, "w") as f:
                f.write(str(last_run_time))

            # Sleep for 1 minute
            time.sleep(60)
        except Exception as e:
            print(f"An error occurred: {e}")
            # Sleep for 10 seconds before trying again
            time.sleep(10)


def add_wallet(wallet_address, blockchain="eth", file_path=WALLETS):
    with open(file_path, "a") as f:
        f.write(f"{blockchain}:{wallet_address}\n")


def remove_wallet(wallet_address, blockchain="eth", file_path=WALLETS):
    with open(file_path, "r") as f, open(TMP, "w") as temp_f:
        for line in f:
            if line.strip() != f"{blockchain}:{wallet_address}":
                temp_f.write(line)
    os.replace(TMP, file_path)


def remove_wallet_by_index(index, blockchain="eth", file_path=WALLETS):
    i = 0
    with open(file_path, "r") as f, open(TMP, "w") as temp_f:
        for line in f:
            if i != index:
                temp_f.write(line)
                i += 1
    os.replace(TMP, file_path)


# Define the command handlers for the Telegram bot
def start(update, context):
    message = """
👋 Welcome to the Ethereum and Binance Wallet Monitoring Bot!

Use /add <wallet_address> to add a new wallet to monitor.

Example: /add 0x123456789abcdef

Use /remove <wallet_address> to stop monitoring a wallet.

Example: /remove 0x123456789abcdef

Use /list to list all wallets being monitored for a specific blockchain.

Example: /list or just /list
    """
    context.bot.send_message(chat_id=update.message.chat_id, text=message)


def add(update, context):
    if len(context.args) < 1:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Please provide a wallet address to add.",
        )
        return

    wallet_address = context.args[0]

    # Check if the wallet address is in the correct format for the specified blockchain
    if not Web3.is_address(wallet_address):
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"{wallet_address} is not a valid Ethereum wallet address.",
        )
        return

    add_wallet(wallet_address)
    message = f"Added {wallet_address} to the list of watched eth wallets."
    context.bot.send_message(chat_id=update.message.chat_id, text=message)


def remove(update, context):
    if len(context.args) < 1:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Please provide a wallet address to remove or a number.\nUsage: /remove 0x123456789abcdef",
        )
        return
    address = context.args[0]
    if Web3.is_address(address):
        remove_wallet(address)
    elif address.isdigit:
        remove_wallet_by_index(int(address) - 1)
    else:
        context.bot.send_message(
            chat_id=update.message.chat_id,
            text="Please provide a wallet address to remove or a number.\nUsage: /remove 0x123456789abcdef",
        )
        return
    message = f"Removed {address} from the list of watched eth wallets."
    context.bot.send_message(chat_id=update.message.chat_id, text=message)


def list_wallets(update, context, blockchain="eth", file_path=WALLETS):
    with open(file_path, "r") as f:
        wallets = [line.strip() for line in f.readlines()]
    if wallets:
        eth_wallets = []
        for wallet in wallets:
            blockchain, wallet_address = wallet.split(":")
            eth_wallets.append(wallet_address)

        message = "The following wallets are currently being monitored\n"
        message += "\n"
        if eth_wallets:
            message += "Ethereum Wallets:\n"
            for i, wallet in enumerate(eth_wallets):
                message += f"{i+1}. {wallet}\n"
            message += "\n"
        context.bot.send_message(chat_id=update.message.chat_id, text=message)
    else:
        message = "There are no wallets currently being monitored."
        context.bot.send_message(chat_id=update.message.chat_id, text=message)


# Set up the Telegram bot
from telegram.ext import Updater, CommandHandler


def main():
    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # Define the command handlers
    start_handler = CommandHandler("start", start)
    add_handler = CommandHandler("add", add)
    remove_handler = CommandHandler("remove", remove)
    list_handler = CommandHandler("list", list_wallets)

    # Add the command handlers to the dispatcher
    dispatcher.add_handler(start_handler)
    dispatcher.add_handler(add_handler)
    dispatcher.add_handler(remove_handler)
    dispatcher.add_handler(list_handler)

    updater.start_polling()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Telegram bot started.")

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Monitoring wallets...")
    monitor_wallets()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted")
        try:
            sys.exit(130)
        except SystemExit:
            os._exit(130)
