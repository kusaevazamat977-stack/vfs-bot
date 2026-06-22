import asyncio
import json
import logging
import os
import re
import random
import httpx
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from playwright.async_api import async_playwright

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = "8572069793:AAHQ42H2b6D9QD5e-HaBGs3EM0DmEAFXlOo"
CHANNEL_ID     = "@SamSebeTur1"
ADMIN_ID       = 1020509234
CHECK_INTERVAL = 300   # секунды между проверками
STATE_FILE     = "C:\\vfs_bot\\last_slots.json"
FORM_EMAIL     = "info@italyvms.ru"   # email для формы (можно свой)

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
    {"center": "1",  "vtype": "13", "label": "Москва / Туризм"},
    {"center": "1",  "vtype": "1",  "label": "Москва / Бизнес"},
    {"center": "1",  "vtype": "4",  "label": "Москва / Приглашение"},
    {"center": "11", "vtype": "13", "label": "СПб / Туризм"},
    {"center": "11", "vtype": "1",  "label": "СПб / Бизнес"},
    {"center": "11", "vtype": "4",  "label": "СПб / Приглашение"},
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://italyvms.com/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
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

# ─── PLAYWRIGHT: ПОЛУАВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ ТОКЕНА ────────────────────────
def extract_token_from_url(url):
    m = re.search(r"[?&]t=([^&]{10,})", url)
    return m.group(1) if m else None

async def get_token_via_playwright(bot=None):
    """
    Полуавтоматическое обновление токена:
    - Бот открывает браузер и заполняет форму сам
    - Тебе остаётся только пройти капчу и нажать Далее
    - Бот поймает новый токен из URL автоматически
    """
    log.info("Playwright: открываю браузер для обновления токена...")

    # Уведомляем администратора что открываем браузер
    if bot:
        try:
            await bot.send_message(
                ADMIN_ID,
                "🔄 *Токен истёк — открываю браузер...*\n\n"
                "Я заполню форму автоматически.\n"
                "Тебе останется только *пройти капчу* и нажать *Далее*.\n\n"
                "⏳ Жди следующего сообщения...",
                parse_mode="Markdown"
            )
        except Exception as e:
            log.warning(f"Notify error: {e}")

    try:
        async with async_playwright() as p:
            # headless=False — открываем ВИДИМЫЙ браузер
            browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()

            # Переходим на форму
            await page.goto("https://italyvms.com/autoform/?lang=ru",
                            wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)

            # Проверяем — может токен уже в URL
            token = extract_token_from_url(page.url)
            if token:
                log.info(f"Playwright: токен сразу в URL!")
                await browser.close()
                return token

            # ── Автоматически заполняем форму ──
            try:
                await page.wait_for_selector("select[name='center']", timeout=15000)

                await page.select_option("select[name='center']", "1")   # Москва
                await asyncio.sleep(0.5)
                await page.select_option("select[name='vtype']", "13")   # Туризм
                await asyncio.sleep(0.5)

                await page.fill("input[name='num_of_person']", "1")
                await page.fill("input[name='email']", FORM_EMAIL)
                await page.fill("input[name='emailcheck']", FORM_EMAIL)

                # Ставим галочки (согласие с условиями)
                for cb in await page.query_selector_all("input[type='checkbox']"):
                    if not await cb.is_checked():
                        await cb.check()
                        await asyncio.sleep(0.3)

                # Нажимаем кнопку Далее
                for sel in ["input[type='button']", "input[type='submit']", "button[type='submit']"]:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        break

                await asyncio.sleep(2)
                log.info(f"Playwright: форма заполнена, URL={page.url[:80]}")

            except Exception as e:
                log.warning(f"Playwright: ошибка заполнения формы: {e}")

            # Проверяем токен после заполнения
            token = extract_token_from_url(page.url)
            if token:
                log.info(f"Playwright: токен получен после формы!")
                await browser.close()
                return token

            # ── Ждём пока человек пройдёт капчу ──
            log.info("Playwright: форма открыта, жду прохождения капчи человеком...")

            if bot:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        "✅ *Форма заполнена автоматически!*\n\n"
                        "👀 *Посмотри на экран — браузер уже открыт.*\n\n"
                        "Тебе нужно:\n"
                        "1. Пройти капчу (выбери картинки)\n"
                        "2. Нажать *Далее*\n\n"
                        "⏳ Жду 5 минут — токен подхвачу автоматически.",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    log.warning(f"Notify error: {e}")

            # Polling URL каждые 2 секунды до 5 минут
            for _ in range(150):  # 150 × 2 сек = 5 минут
                await asyncio.sleep(2)
                current_url = page.url
                token = extract_token_from_url(current_url)
                if token:
                    log.info(f"Playwright: поймал токен после капчи!")
                    await browser.close()
                    return token

            # Timeout — человек не прошёл капчу за 5 минут
            log.warning("Playwright: таймаут 5 минут, токен не получен")
            if bot:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        "⏰ *Таймаут!* Капча не была пройдена за 5 минут.\n\n"
                        "Зайди вручную:\n"
                        "1. Открой https://italyvms.com/autoform/?lang=ru\n"
                        "2. Заполни форму, пройди капчу\n"
                        "3. Скопируй URL и отправь: `/token ТВОЙ_URL`",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            await browser.close()
            return None

    except Exception as e:
        log.error(f"Playwright error: {e}")
        if bot:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"❌ Ошибка браузера: `{e}`\n\n"
                    "Обнови токен вручную:\n"
                    "1. Открой https://italyvms.com/autoform/?lang=ru\n"
                    "2. Заполни форму, пройди капчу\n"
                    "3. Отправь: `/token ТВОЙ_URL_ИЛИ_ТОКЕН`",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
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
                headers=get_headers(), timeout=30, verify=False,
            ) as client:
                url = (f"https://italyvms.com/vcs/get_nearest.htm"
                       f"?center={t['center']}&persons=1&urgent=0"
                       f"&token={token}&lang=ru&vtype={t['vtype']}")
                resp = await client.get(url)
                text = resp.text.strip()
                log.info(f"  -> {text[:80]!r}")

                if "капчу" in text or "captcha" in text.lower():
                    results[t["label"]] = "CAPTCHA"
                elif not text or text == "[]" or "нет" in text.lower():
                    results[t["label"]] = []
                else:
                    dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", text)
                    results[t["label"]] = dates if dates else []

        except Exception as e:
            log.warning(f"  -> error: {e}")
            results[t["label"]] = []

        await asyncio.sleep(random.uniform(5, 12))
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
            lines.append(f"🟢 *{label}*\nДаты: {', '.join(dates)}\n[➡️ Записаться]({url})")

    if lines:
        msg = "🇮🇹 *Свободные окна на визу Италии:*\n\n" + "\n\n".join(lines)
        try:
            await bot.send_message(
                CHANNEL_ID, msg,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            log.info("Published to channel ✅")
        except Exception as e:
            log.error(f"Publish error: {e}")

# ─── JOB: мониторинг ──────────────────────────────────────────────────────────
token_renewal_in_progress = False

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    global token_renewal_in_progress
    log.info("=== Slot check ===")
    state = load_state()
    bot = context.bot
    token = state.get("token", "")

    if not token:
        log.warning("Нет токена! Запускаю обновление...")
        if not token_renewal_in_progress:
            token_renewal_in_progress = True
            new_token = await get_token_via_playwright(bot)
            token_renewal_in_progress = False
            if new_token:
                state["token"] = new_token
                save_state(state)
                token = new_token
            else:
                return

    results = await check_slots(token)

    captcha_count = sum(1 for v in results.values() if v == "CAPTCHA")
    if captcha_count >= 3:
        log.info(f"Капча на {captcha_count} направлениях — обновляю токен...")
        if not token_renewal_in_progress:
            token_renewal_in_progress = True
            new_token = await get_token_via_playwright(bot)
            token_renewal_in_progress = False
            if new_token:
                state["token"] = new_token
                save_state(state)
                results = await check_slots(new_token)

    old_slots = state.get("slots", {})
    new_found = {}
    for label, dates in results.items():
        if dates == "CAPTCHA":
            continue
        if isinstance(dates, list) and dates and set(dates) != set(old_slots.get(label, [])):
            new_found[label] = dates

    if new_found:
        await publish_slots(bot, state, new_found)
        for label, dates in new_found.items():
            state["slots"][label] = dates
        save_state(state)
        log.info(f"Опубликованы новые слоты: {list(new_found.keys())}")
    else:
        log.info("Новых слотов нет.")

    log.info(f"Следующая проверка через {CHECK_INTERVAL//60} мин...")

# ─── COMMANDS ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton("📅 Текущие слоты", callback_data="slots"),
        InlineKeyboardButton("🔄 Обновить токен", callback_data="renew_token"),
    ]]
    await update.message.reply_text(
        "🇮🇹 *VFS Italy Monitor v9*\n\n"
        "Мониторинг свободных окон на запись в визовый центр Италии.\n\n"
        "Команды:\n"
        "/check — проверить прямо сейчас\n"
        "/token URL\_ИЛИ\_ТОКЕН — обновить токен вручную\n"
        "/status — статус бота",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text(
            "Использование:\n"
            "`/token ТОКЕН` — вставить токен\n"
            "`/token https://italyvms.com/autoform/?t=...` — вставить URL целиком",
            parse_mode="Markdown"
        )
        return
    raw = ctx.args[0]
    token = extract_token_from_url(raw) or raw
    state = load_state()
    state["token"] = token
    save_state(state)
    await update.message.reply_text(
        f"✅ Токен обновлён!\n\n`{token[:40]}...`",
        parse_mode="Markdown"
    )

async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg = await update.message.reply_text("🔄 Проверяю слоты...")
    state = load_state()
    results = await check_slots(state.get("token", ""))
    lines = []
    for t in TARGETS:
        v = results.get(t["label"], [])
        if v == "CAPTCHA":
            lines.append(f"⚠️ {t['label']}: нужна капча")
        elif isinstance(v, list) and v:
            lines.append(f"🟢 {t['label']}: {', '.join(v)}")
        else:
            lines.append(f"🔴 {t['label']}: нет мест")
    await msg.edit_text("\n".join(lines))

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    state = load_state()
    token = state.get("token", "")
    token_preview = f"`{token[:30]}...`" if token else "❌ Нет токена"
    slots_count = sum(1 for v in state.get("slots", {}).values() if v)
    await update.message.reply_text(
        f"📊 *Статус бота v9*\n\n"
        f"Токен: {token_preview}\n"
        f"Направлений со слотами: {slots_count}/{len(TARGETS)}\n"
        f"Интервал проверки: {CHECK_INTERVAL//60} мин",
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global token_renewal_in_progress
    q = update.callback_query
    await q.answer()

    if q.data == "slots":
        state = load_state()
        await q.edit_message_text("🔄 Проверяю...")
        results = await check_slots(state.get("token", ""))
        lines = []
        for t in TARGETS:
            v = results.get(t["label"], [])
            if isinstance(v, list) and v:
                url = f"https://italyvms.com/autoform/?t={state.get('token', '')}&lang=ru"
                lines.append(f"🟢 [{t['label']}]({url}): {', '.join(v)}")
        await q.edit_message_text(
            "\n".join(lines) if lines else "🔴 Свободных мест нет.",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

    elif q.data == "renew_token":
        if q.from_user.id != ADMIN_ID:
            await q.answer("Только для администратора", show_alert=True)
            return
        await q.edit_message_text(
            "🔄 Открываю браузер...\n"
            "Следи за сообщениями — скажу когда нужно пройти капчу."
        )
        if not token_renewal_in_progress:
            token_renewal_in_progress = True
            new_token = await get_token_via_playwright(ctx.bot)
            token_renewal_in_progress = False
            if new_token:
                state = load_state()
                state["token"] = new_token
                save_state(state)
                await ctx.bot.send_message(
                    ADMIN_ID,
                    f"✅ *Токен успешно обновлён!*\n`{new_token[:40]}...`",
                    parse_mode="Markdown"
                )

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    log.info("Bot v9 started! Полуавтоматическое обновление токена через Playwright")
    app = (Application.builder()
           .token(BOT_TOKEN)
           .build())

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("token",  cmd_token))
    app.add_handler(CommandHandler("check",  cmd_check))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.job_queue.run_repeating(monitor_job, interval=CHECK_INTERVAL, first=15)

    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
