import discord
from discord.ext import commands
import aiohttp
import asyncio
import logging
import sqlite3
import argparse

# Constants
CATEGORY_ID = 1270450616988340244
CATEGORY_TITLE = "XELIS STATS"
GUILD_ID = 985624643576672256

CHANNEL_IDS = {
    "Network:": None,
    "Block Time:": None,
    "Block Reward:": None,
    "Max Supply:": None,
    "Circ Supply:": None,
    "Coins Mined:": None,
    "Price:": None,
    "Mcap:": None,
    "Net Hash:": None
}

async def background_task(bot, conn, c):
    await set_category_name(bot)
    await update_channels(bot, conn, c)

async def set_category_name(bot):
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    if guild:
        category = discord.utils.get(guild.categories, id=CATEGORY_ID)
        if category:
            await category.edit(name=CATEGORY_TITLE)
            logging.info(f"Category name set to {CATEGORY_TITLE}")

async def update_channels(bot, conn, c):
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    if guild:
        while True:
            logging.info("update channels")
            try:
                await update_channel(conn, c, guild, "Network:", "get_info", key="network")
                await update_channel(conn, c, guild, "Block Time:", "get_info", key="average_block_time", convert_to_seconds=True, format_seconds=True)
                await update_channel(conn, c, guild, "Block Reward:", "get_info", key="block_reward", is_block_reward=True)
                await update_channel(conn, c, guild, "Max Supply:", "get_info", key="maximum_supply", convert_units=True)
                await update_channel(conn, c, guild, "Circ Supply:", "get_info", key="circulating_supply", convert_units=True, is_circulating_supply=True)
                await update_channel(conn, c, guild, "Net Hash:", "get_difficulty", key="hashrate_formatted")
                await update_channel(conn, c, guild, "Coins Mined:", "get_info", key="circulating_supply", calculate_percentage=True)
                await update_channel(conn, c, guild, "Price:", "get_price", key="price")
                await update_channel(conn, c, guild, "Mcap:", "get_info", key="circulating_supply", calculate_market_cap=True)
            except Exception as e:
                logging.error(f"Error updating channels: {e}")
            await asyncio.sleep(400)

async def update_channel(conn, c, guild, channel_name, method, key, convert_units=False, convert_to_seconds=False, format_seconds=False, is_circulating_supply=False, is_block_reward=False, calculate_percentage=False, calculate_market_cap=False):
    data = await fetch_xelis_data(method)
    value = "N/A"
    formatted_value = "N/A"

    if method == "get_price":
        value = data
        formatted_value = f"${value:.4f}" if value != "N/A" else "N/A"
    else:
        value = data.get(key, "N/A")
    
        if calculate_percentage and "maximum_supply" in data and isinstance(value, (int, float)):
            max_supply = data["maximum_supply"]
            if max_supply:
                percentage = (value / max_supply) * 100
                formatted_value = f"{percentage:.2f}%"
        
        if calculate_market_cap and isinstance(value, (int, float)):
            price = await fetch_price()
            if price != "N/A":
                # Assuming value is in atomic units, convert to whole XEL
                circulating_supply = value / 1e8
                market_cap = circulating_supply * price
                formatted_value = f"${market_cap:,.0f}"

        if convert_units and isinstance(value, (int, float)):
            value = value / 1e8  # Convert to units of 1 XELIS
            if is_circulating_supply:
                formatted_value = f"{value:.0f} XEL"
            else:
                formatted_value = f"{value / 1e6:.1f}M XEL"
    
        if convert_to_seconds and isinstance(value, (int, float)):
            value = value / 1000  # Convert from milliseconds to seconds
            formatted_value = f"{value:.0f}s avg"
    
        if is_block_reward and isinstance(value, (int, float)):
            value = value / 1e8  # Convert to units of 1 XELIS
            formatted_value = f"{value:.4f} XEL"
    
        if formatted_value == "N/A":
            formatted_value = str(value)
    
    new_name = f"{channel_name} {formatted_value}"
    channel_id = CHANNEL_IDS.get(channel_name)
    await update_or_create_channel(conn, c, guild, channel_id, channel_name, new_name)

async def update_or_create_channel(conn, c, guild, channel_id, channel_name, new_name):
    try:
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                await channel.edit(name=new_name)
                logging.info(f"Updated channel {channel_name} to {new_name}")
            else:
                new_channel = await guild.create_voice_channel(new_name, category=discord.utils.get(guild.categories, id=CATEGORY_ID))
                CHANNEL_IDS[channel_name] = new_channel.id
                c.execute("INSERT OR REPLACE INTO channels (name, id) VALUES (?, ?)", (channel_name, new_channel.id))
                conn.commit()
                logging.info(f"Created channel {new_name} with ID {new_channel.id}")
        else:
            new_channel = await guild.create_voice_channel(new_name, category=discord.utils.get(guild.categories, id=CATEGORY_ID))
            CHANNEL_IDS[channel_name] = new_channel.id
            c.execute("INSERT OR REPLACE INTO channels (name, id) VALUES (?, ?)", (channel_name, new_channel.id))
            conn.commit()
            logging.info(f"Created channel {new_name} with ID {new_channel.id}")
    except discord.errors.HTTPException as e:
        if e.status == 429:
            retry_after = int(e.response.headers.get("Retry-After", 60))
            logging.warning(f"Rate limited. Retrying in {retry_after} seconds.")
            await asyncio.sleep(retry_after)
            await update_or_create_channel(guild, channel_id, channel_name, new_name)
        else:
            logging.error(f"HTTP Error creating/updating channel {channel_name}: {e}")
    except Exception as e:
        logging.error(f"Error creating/updating channel {channel_name}: {e}")

async def fetch_xelis_data(method):
    if method == "get_price":
        return await fetch_price()
    url = "https://node.xelis.io/json_rpc"
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "id": 1
    }
    headers = {"Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                return result.get("result", {})
            else:
                logging.error(f"Failed to fetch Xelis data for method {method}. Status code: {response.status}")
                return {}

async def fetch_price():
    url = "https://api.coinpaprika.com/v1/tickers/xel-xelis"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                result = await response.json()
                # Correct path to access the price in USD
                return result.get("quotes", {}).get("USD", {}).get("price", "N/A")
            else:
                logging.error(f"Failed to fetch price data. Status code: {response.status}")
                return "N/A"

def setup_db():
    # Database setup
    conn = sqlite3.connect("channels.db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        name TEXT PRIMARY KEY,
        id INTEGER
    )
    """)
    conn.commit()

    # Load existing channels from database
    c.execute("SELECT name, id FROM channels")
    rows = c.fetchall()
    for row in rows:
        CHANNEL_IDS[row[0]] = row[1]

    return conn, c

def main():
    # Set up logging
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Read token from arguments
    parser = argparse.ArgumentParser(description='Discord Bot')
    parser.add_argument('--token', type=str, required=True, help='Token for the Discord bot')
    args = parser.parse_args()

    # Setup database
    conn, c = setup_db()

    # Initialize bot
    bot = commands.Bot(command_prefix=".", intents=discord.Intents.default())

    @bot.event
    async def on_ready():
        logging.info(f"Logged in as {bot.user}")
        bot.loop.create_task(background_task(bot, conn, c))

    token = args.token
    bot.run(token)

if __name__ == "__main__":
    main()