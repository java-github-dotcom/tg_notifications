import asyncio
import secrets
import time
import re
import random
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UsernameInvalidError, UsernamePurchaseAvailableError
from telethon.tl.functions.account import CheckUsernameRequest

# Optional: GetUsernameInfoRequest
try:
    from telethon.tl.functions.account import GetUsernameInfoRequest
    HAS_USERNAME_INFO = True
except Exception:
    HAS_USERNAME_INFO = False

# =========================
# CONFIG from ENV
# =========================
API_ID = int(os.environ.get("TG_API_ID", "0"))
API_HASH = os.environ.get("TG_API_HASH", "")
ATTEMPTS = int(os.environ.get("ATTEMPTS", 100))  # limit in CI
DELAY_MIN = 1.0  # lower for GitHub Actions
DELAY_MAX = 2.0
FREE_FILE = "free.txt"
BUYABLE_FILE = "buyable.txt"

USE_LENGTHS = [5, 6]

PATTERNS_5 = ["CVCVC", "VCVCV", "CVCCV", "VCCVC", "CVVCV"]
PATTERNS_6 = ["CVCVCV", "VCVCVC", "CVCCVC"]

GEN_MODE = "pattern"
MIX_DOMAIN_PROB = 0.2

VOWELS = "aeiou"
CONSONANTS = "bcdfghklmnprstvwy"

VOWEL_WEIGHTS: Dict[str, float] = {"a": 1.15, "e": 1.35, "i": 1.10, "o": 1.00, "u": 0.55}
CONS_WEIGHTS: Dict[str, float] = {
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

DOMAIN_SYLLABLES = ["CV", "CV", "CV", "CVC", "VC"]

# =========================
# UTILITIES
# =========================
def weighted_choice(chars: str, weights_map: Dict[str, float]) -> str:
    weights = [weights_map.get(ch, 1.0) for ch in chars]
    return random.choices(list(chars), weights=weights, k=1)[0]

def pick_pattern() -> str:
    length = secrets.choice(USE_LENGTHS)
    if length == 5:
        return secrets.choice(PATTERNS_5)
    return secrets.choice(PATTERNS_6)

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

def gen_domain_style(target_len: int) -> Tuple[str, str]:
    parts: List[str] = []
    recipe: List[str] = []
    s = ""
    while len(s) < target_len:
        chunk = random.choice(DOMAIN_SYLLABLES)
        if len(s) + len(chunk) > target_len:
            remaining = target_len - len(s)
            chunk = "C" if remaining == 1 else "CV"
            chunk = chunk[:remaining]
        piece = []
        for ch in chunk:
            piece.append(weighted_choice(VOWELS, VOWEL_WEIGHTS) if ch == "V" else weighted_choice(CONSONANTS, CONS_WEIGHTS))
        piece_s = "".join(piece)
        parts.append(piece_s)
        recipe.append(chunk)
        s = "".join(parts)
    return s, "domain:" + "-".join(recipe)

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
    if (len(u)==5 or len(u)==6) and vcount < 2:
        return False
    if has_bad_bigrams(u) or has_bad_trigrams(u):
        return False
    return True

def generate_username() -> Tuple[str, str]:
    if GEN_MODE=="domain" or (MIX_DOMAIN_PROB>0 and random.random()<MIX_DOMAIN_PROB):
        length = secrets.choice(USE_LENGTHS)
        return gen_domain_style(length)
    pattern = pick_pattern()
    return gen_by_pattern(pattern), pattern

def append_line(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line+"\n")

# =========================
# TELEGRAM CHECK
# =========================
async def classify_username(client, u: str) -> str:
    try:
        ok = await client(CheckUsernameRequest(username=u))
        if not ok: return "TAKEN"
        if HAS_USERNAME_INFO:
            info = await client(GetUsernameInfoRequest(username=u))
            txt = str(info).lower()
            if "purchase" in txt and ("available" in txt or "true" in txt):
                return "BUYABLE"
        return "FREE"
    except UsernamePurchaseAvailableError: return "BUYABLE"
    except UsernameInvalidError: return "INVALID"

async def main():
    os.makedirs("./sessions", exist_ok=True)
    checked = free = buyable = taken = invalid = 0
    seen = set()
    t0 = time.time()
    session_file = "./sessions/username_checker.session"

    async with TelegramClient(session_file, API_ID, API_HASH) as client:
        if not HAS_USERNAME_INFO:
            print("[warn] Telethon without GetUsernameInfoRequest, BUYABLE may misclassify.")

        while checked < ATTEMPTS:
            u, tag = generate_username()
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
                append_line(FREE_FILE, u)
                print(f"[FREE] @{u} ({tag})")
            elif status=="BUYABLE":
                buyable += 1
                append_line(BUYABLE_FILE, u)
            elif status=="TAKEN": taken += 1
            else: invalid += 1

            if checked % 50 == 0:
                elapsed = time.time()-t0
                print(f"[progress] checked={checked} free={free} buyable={buyable} taken={taken} invalid={invalid} rate={checked/elapsed:.2f}/s")

            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    print(f"DONE: checked={checked} free={free} buyable={buyable} taken={taken} invalid={invalid}")

if __name__=="__main__":
    asyncio.run(main())
