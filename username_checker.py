import asyncio
import secrets
import time
import re
import random
import os
from typing import Dict, List

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UsernameInvalidError, UsernamePurchaseAvailableError
from telethon.tl.functions.account import CheckUsernameRequest

# Telegram Bot
import aiohttp
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN001")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")  # must be your chat ID

# =========================
# CONFIGURATION
# =========================
VOWELS = "aeiou"
CONSONANTS = "bcdfghklmnprstvwy"

VOWEL_WEIGHTS = {"a": 1.15, "e": 1.35, "i": 1.10, "o": 1.00, "u": 0.55}
CONS_WEIGHTS = {
    "b": 0.55, "c": 0.80, "d": 0.85, "f": 0.55, "g": 0.65,
    "h": 0.55, "k": 0.60, "l": 1.05, "m": 0.95, "n": 1.20,
    "p": 0.80, "r": 1.15, "s": 1.10, "t": 1.20, "v": 0.45,
    "w": 0.35, "y": 0.40,
}

ALLOWED_DOUBLES = {"ll", "ss", "tt", "nn", "mm", "pp", "rr"}
HARD_BANS_SUBSTRINGS = ("q", "x", "z", "j")
BAD_BIGRAMS = {"lr","rl","pt","tp","wv","vw","yh","hy","nm","mn","dl","ld","dt","td","bp","pb","fk","kf","gp","pg","tv","vt","dw","wd","tg","gt","kc"}
BAD_BIGRAMS.discard("ck")
BAD_TRIGRAMS = {"str"}
ONLY_LETTERS_RE_5_6 = re.compile(r"^[a-z]{5,6}$")

PATTERNS_5 = ["CVCVC", "VCVCV", "CVCCV", "VCCVC", "CVVCV"]
PATTERNS_6 = ["CVCVCV", "VCVCVC", "CVCCVC"]
USE_LENGTHS = [5,6]

FREE_FILE = "free.txt"
BUYABLE_FILE = "buyable.txt"

DELAY_MIN = 1.0
DELAY_MAX = 2.0
ATTEMPTS = 100

# =========================
# TELEGRAM BOT HELPERS
# =========================
async def send_telegram_message(text: str):
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TG_CHAT_ID, "text": text}
        async with session.post(url, json=payload) as resp:
            return await resp.json()

# =========================
# USERNAME GENERATION
# =========================
def weighted_choice(chars: str, weights_map: Dict[str, float]) -> str:
    weights = [weights_map.get(ch, 1.0) for ch in chars]
    return random.choices(list(chars), weights=weights, k=1)[0]

def pick_pattern(patterns: List[str]) -> str:
    return secrets.choice(patterns)

def gen_by_pattern(pattern: str) -> str:
    out = []
    for ch in pattern:
        if ch == "V":
            out.append(weighted_choice(VOWELS, VOWEL_WEIGHTS))
        elif ch == "C":
            out.append(weighted_choice(CONSONANTS, CONS_WEIGHTS))
        else:
            raise ValueError(f"Unknown pattern symbol: {ch}")
    return "".join(out)

def has_bad_bigrams(u: str) -> bool:
    for i in range(len(u)-1):
        bg = u[i:i+2]
        if bg in BAD_BIGRAMS or (u[i]==u[i+1] and bg not in ALLOWED_DOUBLES):
            return True
    return False

def has_bad_trigrams(u: str) -> bool:
    return any(u[i:i+3] in BAD_TRIGRAMS for i in range(len(u)-2))

def passes_basic_rules(u: str) -> bool:
    if not ONLY_LETTERS_RE_5_6.fullmatch(u):
        return False
    if any(b in u for b in HARD_BANS_SUBSTRINGS):
        return False
    vcount = sum(1 for ch in u if ch in VOWELS)
    if len(u) in [5,6] and vcount < 2:
        return False
    if has_bad_bigrams(u) or has_bad_trigrams(u):
        return False
    return True

def generate_username(patterns: List[str]) -> str:
    return gen_by_pattern(pick_pattern(patterns))

def append_line(path: str, line: str):
    with open(path,"a", encoding="utf-8") as f:
        f.write(line+"\n")

# =========================
# TELEGRAM CHECK
# =========================
async def classify_username(client, u: str) -> str:
    try:
        ok = await client(CheckUsernameRequest(username=u))
        if not ok: return "TAKEN"
        return "FREE"
    except UsernamePurchaseAvailableError: return "BUYABLE"
    except UsernameInvalidError: return "INVALID"

# =========================
# MAIN
# =========================
async def main():
    await send_telegram_message("🚀 Username checker workflow started.")
    
    API_ID = int(os.environ.get("TG_API_ID") or input("Enter API_ID: "))
    API_HASH = os.environ.get("TG_API_HASH") or input("Enter API_HASH: ")

    patterns_input = input(f"Enter patterns separated by comma (default {PATTERNS_5 + PATTERNS_6}): ")
    patterns = [p.strip().upper() for p in patterns_input.split(",") if p.strip()] or PATTERNS_5 + PATTERNS_6

    lengths_input = input(f"Enter lengths separated by comma (default 5,6): ")
    global USE_LENGTHS
    USE_LENGTHS = [int(x.strip()) for x in lengths_input.split(",") if x.strip()] or [5,6]

    os.makedirs("./sessions", exist_ok=True)
    checked = free = buyable = taken = invalid = 0
    seen = set()
    t0 = time.time()
    session_file = "./sessions/username_checker.session"

    async with TelegramClient(session_file, API_ID, API_HASH) as client:
        while checked < ATTEMPTS:
            u = generate_username(patterns)
            if u in seen: continue
            seen.add(u)
            if not passes_basic_rules(u):
                invalid += 1
                continue
            try:
                status = await classify_username(client, u)
            except FloodWaitError as e:
                wait_s = int(getattr(e,"seconds",30))
                print(f"[FLOOD_WAIT] sleeping {wait_s}s")
                await asyncio.sleep(wait_s)
                continue

            checked += 1
            if status=="FREE":
                free += 1
                append_line(FREE_FILE,u)
            elif status=="BUYABLE":
                buyable += 1
                append_line(BUYABLE_FILE,u)
            elif status=="TAKEN": taken += 1
            else: invalid += 1

            if checked % 10 == 0:
                print(f"[progress] checked={checked} free={free} buyable={buyable} taken={taken} invalid={invalid}")
            
            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    msg = f"✅ Username checker finished.\nChecked={checked} Free={free} Buyable={buyable} Taken={taken} Invalid={invalid}"
    await send_telegram_message(msg)

    # Send list of available usernames
    if free > 0:
        with open(FREE_FILE,"r", encoding="utf-8") as f:
            lines = f.read()
            await send_telegram_message(f"Available usernames:\n{lines[:4000]}")  # Telegram limit

if __name__=="__main__":
    asyncio.run(main())
