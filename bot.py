import asyncio
import json
import logging
import os
import re
import base64
import httpx
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from playwright.async_api import async_playwright

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = "8572069793:AAHQ42H2b6D9QD5e-HaBGs3EM0DmEAFXlOo"
CHANNEL_ID     = "@SamSebeTur1"
ADMIN_ID       = 1020509234
CAPTCHA_KEY    = "59a9f897c7b64793c2ac84d4ffec4b34"
CHECK_INTERVAL = 1800
STATE_FILE     = "C:\\vfs_bot\\last_slots.json"

PROXIES = [
    "http://user409265:y41xol@138.249.26.253:6085",
    "http://user409265:y41xol@193.33.67.76:3390",
    "http://user409265:y41xol@45.85.67.188:3390",
]
proxy_index = 0

def get_proxy():
    global proxy_index
    proxy = PROXIES[proxy_index % len(PROXIES)]
    proxy_index += 1
    return proxy

TARGETS = [
    {"center": 1,  "vtype": 13, "label": "Москва / Туризм"},
    {"center": 1,  "vtype": 1,  "label": "Москва / Бизнес"},
    {"center": 1,  "vtype": 4,  "label": "Москва / Приглашение"},
    {"center": 11, "vtype": 13, "label": "СПб / Туризм"},
    {"center": 11, "vtype": 1,  "label": "СПб / Бизнес"},
    {"center": 11, "vtype": 4,  "label": "СПб / Приглашение"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://italyvms.com/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── STATE ────────────────────────────────────────────────────────────────────
def load_state():
    try:
        if os.path.exists(STATE_FILE):
            raw = open(STATE_FILE, "r", encoding="utf-8").read().strip()
            if raw:
                return json.loads(raw)
    except Exception:
        pass
    return {"slots": {t["label"]: [] for t in TARGETS}, "token": ""}

def save_state(state):
    try:
        json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=True, indent=2)
    except Exception as e:
        log.warning(f"Save state error: {e}")

# ─── 2CAPTCHA ─────────────────────────────────────────────────────────────────
async def solve_image_captcha(image_base64):
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post("http://2captcha.com/in.php", data={
                "key": CAPTCHA_KEY, "method": "base64",
                "body": image_base64, "json": 1,
            })
            data = resp.json()
            if data.get("status") != 1:
                return None
            captcha_id = data["request"]
            log.info(f"2captcha: sent id={captcha_id}")
            for _ in range(24):
                await asyncio.sleep(5)
                res = await client.get(f"http://2captcha.com/res.php?key={CAPTCHA_KEY}&action=get&id={captcha_id}&json=1")
                rdata = res.json()
                if rdata.get("status") == 1:
                    log.info(f"2captcha: solved!")
                    return rdata["request"]
                if rdata.get("request") != "CAPCHA_NOT_READY":
                    return None
    except Exception as e:
        log.error(f"2captcha error: {e}")
    return None

# ─── PLAYWRIGHT ───────────────────────────────────────────────────────────────
def extract_token_from_url(url):
    m = re.search(r"[?&]t=([^&]{10,})", url)
    return m.group(1) if m else None

async def get_token_via_playwright():
    log.info("Playwright: getting new token...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            )
            await page.goto("https://italyvms.com/autoform/?lang=ru", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)

            token = extract_token_from_url(page.url)
            if token:
                await browser.close()
                return token

            try:
                await page.wait_for_selector("select[name='center']", timeout=10000)
                await page.select_option("select[name='center']", "1")
                await asyncio.sleep(1)
                await page.select_option("select[name='vtype']", "13")
                await page.fill("input[name='num_of_person']", "1")
                await page.fill("input[name='email']", "test@italyvms.ru")
                await page.fill("input[name='emailcheck']", "test@italyvms.ru")
                for cb in await page.query_selector_all("input[type='checkbox']"):
                    if not await cb.is_checked():
                        await cb.check()
                btn = await page.query_selector("input[type='button']")
                if btn:
                    await btn.click()
                await asyncio.sleep(3)
            except Exception as e:
                log.info(f"Form fill skipped: {e}")

            token = extract_token_from_url(page.url)
            if token:
                await browser.close()
                return token

            for selector in ["img.captcha", "img[src*='captcha']", ".captcha img"]:
                img = await page.query_selector(selector)
                if img:
                    img_bytes = await img.screenshot()
                    answer = await solve_image_captcha(base64.b64encode(img_bytes).decode())
                    if answer:
                        inp = await page.query_selector("input[name='captcha'],input[id*='captcha']")
                        if inp:
                            await inp.fill(answer)
                        btn = await page.query_selector("input[type='button'],input[type='submit']")
                        if btn:
                            await btn.click()
                        await asyncio.sleep(3)
                        token = extract_token_from_url(page.url)
                        if token:
                            await browser.close()
                            return token
                    break

            await browser.close()
            return None
    except Exception as e:
        log.error(f"Playwright error: {e}")
        return None

# ─── SLOTS ────────────────────────────────────────────────────────────────────
async def check_slots(token):
    results = {}
    for t in TARGETS:
        proxy = get_proxy()
        log.info(f"Checking {t['label']} via {proxy.split('@')[1]}")
        try:
            async with httpx.AsyncClient(
                proxies={"http://": proxy, "https://": proxy},
                headers=HEADERS, timeout=30, verify=False,
            ) as client:
                url = (f"https://italyvms.com/vcs/get_nearest.htm"
                       f"?center={t['center']}&persons=1&urgent=0"
                       f"&token={token}&lang=ru&vtype={t['vtype']}")
                resp = await client.get(url)
                text = resp.text.strip()
                log.info(f"  -> {text[:60]!r}")
                if "капчу" in text or "captcha" in text.lower():
                    results[t["label"]] = "CAPTCHA"
                elif "нет" in text.lower() or not text or text == "[]":
                    results[t["label"]] = []
                else:
                    dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", text)
                    results[t["label"]] = dates if dates else []
        except Exception as e:
            log.warning(f"  -> error: {e}")
            results[t["label"]] = []
        await asyncio.sleep(5)
    return results

# ─── PUBLISH ──────────────────────────────────────────────────────────────────
async def publish_slots(bot, state, new_slots):
    token = state.get("token", "")
    lines = []
    for t in TARGETS:
        label = t["label"]
        dates = new_slots.get(label, [])
        if isinstance(dates, list) and dates:
            url = f"https://italyvms.com/autoform/?t={token}&lang=ru"
            lines.append(f"🟢 *{label}*\nДата: {', '.join(dates)}\n[Записаться]({url})")
    if lines:
        msg = "🇮🇹 *Доступные окна на визу Италии:*\n\n" + "\n\n".join(lines)
        try:
            await bot.send_message(CHANNEL_ID, msg, parse_mode="Markdown", disable_web_page_preview=True)
            log.info("Published to channel ✅")
        except Exception as e:
            log.error(f"Publish error: {e}")

# ─── JOB: мониторинг через JobQueue ──────────────────────────────────────────
captcha_fails = 0

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    global captcha_fails
    log.info("=== Slot check ===")
    state = load_state()
    bot = context.bot
    token = state.get("token", "")

    results = await check_slots(token)

    captcha_count = sum(1 for v in results.values() if v == "CAPTCHA")
    if captcha_count > 0:
        log.info(f"Captcha on {captcha_count} endpoints, auto-renewing...")
        new_token = await get_token_via_playwright()
        if new_token:
            state["token"] = new_token
            save_state(state)
            captcha_fails = 0
            log.info(f"Token renewed!")
            results = await check_slots(new_token)
        else:
            captcha_fails += 1
            log.warning(f"Token renewal failed ({captcha_fails}/3)")
            if captcha_fails >= 3:
                try:
                    await bot.send_message(ADMIN_ID,
                        "⚠️ Не удалось обновить токен!\n\n"
                        "1. Зайди на https://italyvms.com/autoform/?lang=ru\n"
                        "2. Заполни форму до дат\n"
                        "3. Отправь: /token НОВЫЙ\\_ТОКЕН",
                        parse_mode="Markdown")
                except Exception:
                    pass
                captcha_fails = 0

    old_slots = state.get("slots", {})
    new_found = {}
    for label, dates in results.items():
        if dates == "CAPTCHA":
            continue
        if isinstance(dates, list) and dates and dates != old_slots.get(label, []):
            new_found[label] = dates

    if new_found:
        await publish_slots(bot, state, new_found)
        for label, dates in new_found.items():
            state["slots"][label] = dates
        save_state(state)
    else:
        log.info("No new slots.")

    log.info(f"Next check in {CHECK_INTERVAL//60} min...")

# ─── COMMANDS ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton("📅 Текущие слоты", callback_data="slots"),
        InlineKeyboardButton("🔔 Подписаться", callback_data="subscribe"),
    ]]
    await update.message.reply_text(
        "🇮🇹 *VFS Italy Monitor*\n\nМониторинг свободных окон на запись.",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def cmd_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Использование: /token ТОКЕН_ИЛИ_URL")
        return
    raw = ctx.args[0]
    token = extract_token_from_url(raw) or raw
    state = load_state()
    state["token"] = token
    save_state(state)
    await update.message.reply_text(f"✅ Токен обновлён:\n`{token}`", parse_mode="Markdown")

async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("🔄 Проверяю...")
    state = load_state()
    results = await check_slots(state.get("token", ""))
    lines = []
    for t in TARGETS:
        v = results.get(t["label"], [])
        if v == "CAPTCHA":
            lines.append(f"⚠️ {t['label']}: Капча")
        elif isinstance(v, list) and v:
            lines.append(f"🟢 {t['label']}: {', '.join(v)}")
        else:
            lines.append(f"🔴 {t['label']}: Нет мест")
    await update.message.reply_text("\n".join(lines))

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    state = load_state()
    if q.data == "slots":
        results = await check_slots(state.get("token", ""))
        lines = []
        for t in TARGETS:
            v = results.get(t["label"], [])
            if isinstance(v, list) and v:
                url = f"https://italyvms.com/autoform/?t={state.get('token','')}&lang=ru"
                lines.append(f"🟢 [{t['label']}]({url}): {', '.join(v)}")
        await q.edit_message_text(
            "\n".join(lines) if lines else "🔴 Свободных мест нет.",
            parse_mode="Markdown", disable_web_page_preview=True)
    elif q.data == "subscribe":
        subs_file = "C:\\vfs_bot\\subscribers.json"
        try:
            subs = json.load(open(subs_file, encoding="utf-8")) if os.path.exists(subs_file) else []
        except Exception:
            subs = []
        uid = q.from_user.id
        if uid not in subs:
            subs.append(uid)
            json.dump(subs, open(subs_file, "w", encoding="utf-8"))
            await q.edit_message_text("✅ Вы подписались!")
        else:
            await q.edit_message_text("Вы уже подписаны.")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    log.info("Bot v8 started! JobQueue + Proxy rotation + 2captcha")
    app = (Application.builder()
           .token(BOT_TOKEN)
           .build())

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("token", cmd_token))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Запускаем мониторинг через JobQueue — без Conflict!
    app.job_queue.run_repeating(monitor_job, interval=CHECK_INTERVAL, first=10)

    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
