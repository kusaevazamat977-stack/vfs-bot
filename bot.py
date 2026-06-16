"""ItalyVMS Slot Monitor Bot v5"""
import asyncio, logging, os, json, httpx, re, base64
from datetime import datetime
from playwright.async_api import async_playwright
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN       = "8623727460:AAGia4P5xYIPXqz5HR5ZyDTd6K5Qc8syvvs"
CHANNEL_ID      = "@SamSebeTur1"
ADMIN_ID        = 1020509234
CHECK_INTERVAL  = 1800
CAPTCHA_API_KEY = "59a9f897c7b64793c2ac84d4ffec4b34"
PROXY_USER      = "user409265"
PROXY_PASS      = "y41xol"
PROXY_HOST      = "138.249.26.253"
PROXY_PORT      = "6085"
PROXY_URL       = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
SESSION_TOKEN   = "twr6exwe8n-5259223-i7qcmgc169ttlro3kyu77hqhg6oc7g335ndgbwsxf0dbd"

TARGETS = [
    ("1",  "Moskva (Tolmachevsky)", "13", "Turizm"),
    ("1",  "Moskva (Tolmachevsky)", "1",  "Biznes"),
    ("1",  "Moskva (Tolmachevsky)", "4",  "Priglashenie"),
    ("11", "Sankt-Peterburg",       "13", "Turizm"),
    ("11", "Sankt-Peterburg",       "1",  "Biznes"),
    ("11", "Sankt-Peterburg",       "4",  "Priglashenie"),
]

SLOTS_FILE      = "C:\\vfs_bot\\last_slots.json"
SUBS_FILE       = "C:\\vfs_bot\\subscribers.json"
last_known      = {}
subscribers     = set()
token_expired   = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://italyvms.com/",
    "Accept": "application/json, text/javascript, */*",
}

def load_state():
    global last_known, subscribers, SESSION_TOKEN
    try:
        if os.path.exists(SLOTS_FILE):
            with open(SLOTS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    last_known = data.get("slots", {})
                    saved = data.get("token", "")
                    if saved:
                        SESSION_TOKEN = saved
    except Exception as e:
        logger.warning(f"Load state error: {e}")
    try:
        if os.path.exists(SUBS_FILE):
            with open(SUBS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    subscribers = set(json.loads(content))
    except Exception as e:
        logger.warning(f"Load subs error: {e}")

def save_state():
    try:
        with open(SLOTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"slots": last_known, "token": SESSION_TOKEN}, f, ensure_ascii=True, indent=2)
    except Exception as e:
        logger.error(f"Save state error: {e}")
    try:
        with open(SUBS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(subscribers), f, ensure_ascii=True)
    except Exception as e:
        logger.error(f"Save subs error: {e}")

async def get_token_via_playwright() -> str:
    global SESSION_TOKEN, token_expired
    logger.info("Getting new token via Playwright...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()
            # Load page and wait for it
            await page.goto("https://italyvms.com/autoform/?lang=ru", timeout=60000)
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Try filling the form with JS directly
            try:
                await page.evaluate("""
                    document.querySelector("select[name='center']").value = '1';
                    document.querySelector("select[name='center']").dispatchEvent(new Event('change'));
                """)
                await asyncio.sleep(2)
                await page.evaluate("""
                    document.querySelector("select[name='vtype']").value = '13';
                    document.querySelector("select[name='vtype']").dispatchEvent(new Event('change'));
                """)
                await asyncio.sleep(1)
                await page.evaluate("""
                    document.querySelector("input[name='num_of_person']").value = '1';
                    document.querySelector("input[name='email']").value = '201007azik@mail.ru';
                    document.querySelector("input[name='emailcheck']").value = '201007azik@mail.ru';
                    document.querySelectorAll("input[type='checkbox']").forEach(cb => cb.checked = true);
                """)
                await asyncio.sleep(1)
                await page.click("input[type='button']")
                await asyncio.sleep(4)
            except Exception as e:
                logger.warning(f"Form fill error: {e}")

            for _ in range(5):
                current_url = page.url
                logger.info(f"Current URL: {current_url}")
                token_match = re.search(r'[?&]t=([\w\-]+)', current_url)
                if token_match:
                    new_token = token_match.group(1)
                    logger.info(f"Got token: {new_token[:20]}...")
                    SESSION_TOKEN = new_token
                    token_expired = False
                    save_state()
                    await browser.close()
                    return new_token
                try:
                    await page.click("input[type='button']")
                    await asyncio.sleep(2)
                except Exception:
                    break
            await browser.close()
    except Exception as e:
        logger.error(f"Playwright error: {e}")
    return ""

async def check_slots_api(center: str, vtype: str) -> list:
    global token_expired
    url = "https://italyvms.com/vcs/get_nearest.htm"
    params = {"center": center, "persons": "1", "urgent": "0", "token": SESSION_TOKEN, "lang": "ru", "vtype": vtype}
    try:
        proxies = {"http://": PROXY_URL, "https://": PROXY_URL}
        async with httpx.AsyncClient(headers=HEADERS, proxies=proxies, timeout=30) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                text = r.text.strip()
                logger.info(f"API center={center} vtype={vtype}: '{text[:60]}'")
                if text and text not in ("", "null", "false"):
                    if "kapch" in text.lower() or "captcha" in text.lower() or "vvedite" in text.lower() or "\u043a\u0430\u043f\u0447" in text.lower() or "\u0432\u0432\u0435\u0434\u0438\u0442\u0435" in text.lower():
                        token_expired = True
                        return []
                    if re.match(r'\d{2}\.\d{2}\.\d{4}', text):
                        token_expired = False
                        return [text]
            elif r.status_code == 403:
                token_expired = True
    except Exception as e:
        logger.error(f"API error: {e}")
    return []

def make_link(center: str, vtype: str) -> str:
    return "https://italyvms.com/autoform/?t=" + SESSION_TOKEN + "&lang=ru&center=" + center + "&vtype=" + vtype

def format_message(results: dict) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = ["<b>Italyvms.com - Dostupnye okna zapisi</b>", "Obnovleno: " + now + " MSK\n"]
    for (center, city_name, vtype, visa_name), dates in results.items():
        if dates:
            link = make_link(center, vtype)
            lines.append("<b>" + city_name + " / " + visa_name + "</b>")
            lines.append("Data: " + " * ".join(dates))
            lines.append('<a href="' + link + '">Zapisatsya</a>\n')
    return "\n".join(lines)

async def monitor_loop(bot: Bot):
    global last_known, token_expired
    auto_retry = 0
    while True:
        if token_expired:
            logger.info("Token expired, auto-renewing...")
            new_token = await get_token_via_playwright()
            if new_token:
                auto_retry = 0
                logger.info("Token renewed!")
            else:
                auto_retry += 1
                if auto_retry <= 3:
                    await asyncio.sleep(300)
                    continue
                else:
                    try:
                        await bot.send_message(chat_id=ADMIN_ID, text=(
                            "Ne udalos obnovit token!\n\n"
                            "1. Zajdi na italyvms.com/autoform/?lang=ru\n"
                            "2. Zapol\' formu do stranicy s datami\n"
                            "3. Otprav\': /token NOVYJ_TOKEN"
                        ))
                    except Exception:
                        pass
                    auto_retry = 0
                    await asyncio.sleep(600)
                    continue

        logger.info("Starting slot check...")
        results = {}
        changed = False

        for i, (center, city_name, vtype, visa_name) in enumerate(TARGETS):
            key = city_name + "/" + visa_name
            logger.info(f"Checking {i+1}/{len(TARGETS)}: {key}")
            dates = await check_slots_api(center, vtype)
            results[(center, city_name, vtype, visa_name)] = dates
            prev = last_known.get(key, [])
            new_dates = [d for d in dates if d not in prev]
            if new_dates:
                changed = True
                try:
                    await bot.send_message(chat_id=ADMIN_ID, text="Novye okna!\n" + key + ": " + ", ".join(new_dates))
                except Exception:
                    pass
            last_known[key] = dates
            await asyncio.sleep(10)

        if token_expired:
            continue

        has_slots = any(dates for dates in results.values())
        if has_slots:
            try:
                msg = format_message(results)
                await bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                logger.info("Published to channel")
            except Exception as e:
                logger.error(f"Channel error: {e}")

            if subscribers:
                for chat_id in list(subscribers):
                    try:
                        await bot.send_message(chat_id=chat_id, text="Poyavilis novye okna! Smotri kanal.")
                    except Exception:
                        pass
        else:
            logger.info("No slots, skipping channel post")

        save_state()
        logger.info("Next check in 30 min...")
        await asyncio.sleep(CHECK_INTERVAL)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Tekushchie sloty", callback_data="slots"),
        InlineKeyboardButton("Podpisatsya", callback_data="subscribe"),
    ]])
    await update.message.reply_text("Bot monitrit italyvms.com\nGoroda: Moskva, Sankt-Peterburg\nTipy: Turizm, Biznes, Priglashenie", reply_markup=kb)

async def cmd_slots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not last_known:
        await update.message.reply_text("Dannyh eshche net.")
        return
    lines = ["<b>Tekushchie sloty:</b>\n"]
    kb_buttons = []
    has_slots = False
    for (center, city_name, vtype, visa_name) in TARGETS:
        key = city_name + "/" + visa_name
        dates = last_known.get(key, [])
        if dates:
            has_slots = True
            link = make_link(center, vtype)
            lines.append("<b>" + key + "</b>: " + ", ".join(dates))
            kb_buttons.append([InlineKeyboardButton("Zapisatsya: " + key, url=link)])
    if not has_slots:
        lines.append("Svobodnyh mest net.")
    kb = InlineKeyboardMarkup(kb_buttons) if kb_buttons else None
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)

async def cmd_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SESSION_TOKEN, token_expired
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Net dostupa.")
        return
    if not ctx.args:
        await update.message.reply_text("Ispolzovanie: /token NOVYJ_TOKEN")
        return
    new_token = ctx.args[0].strip()
    match = re.search(r'[?&]t=([\w\-]+)', new_token)
    if match:
        new_token = match.group(1)
    SESSION_TOKEN = new_token
    token_expired = False
    save_state()
    await update.message.reply_text("Token obnovlen! Bot prodolzhaet rabotu.")

async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    subscribers.add(update.effective_chat.id)
    save_state()
    await update.message.reply_text("Podpiska oformlena!")

async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    subscribers.discard(update.effective_chat.id)
    save_state()
    await update.message.reply_text("Podpiska otmenena.")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    total = sum(1 for v in last_known.values() if v)
    status = "TOKEN ISTEKL!" if token_expired else "Rabotaet"
    await update.message.reply_text(
        "Status: " + status + "\nNapravlenij s mestami: " + str(total) + "/" + str(len(TARGETS)) +
        "\nPodpischikov: " + str(len(subscribers)) + "\nInterval: 30 min\nProxy: " + PROXY_HOST
    )

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "slots":
        await cmd_slots(update, ctx)
    elif q.data == "subscribe":
        await cmd_subscribe(update, ctx)

async def main():
    load_state()
    logger.info("Starting Bot v5...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("slots", cmd_slots))
    app.add_handler(CommandHandler("token", cmd_token))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_callback))
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot v5 started!")
    await monitor_loop(app.bot)

if __name__ == "__main__":
    asyncio.run(main())
