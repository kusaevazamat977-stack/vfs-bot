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
BOT_TOKEN      = "8572069793:AAEPg0ij645PLXlWS0f8xpvwCsh_nfsIcMk"
CHANNEL_ID     = "@SamSebeTur1"
ADMIN_ID       = 1020509234
CHECK_INTERVAL = 300
STATE_FILE     = "C:\\vfs_bot\\last_slots.json"
YANDEX_EMAIL    = "kusyaevazilia@yandex.ru"
YANDEX_PASSWORD = "20708090aza!"   # <-- вставь сам в файле

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

# ─── YANDEX IMAP — чтение кода подтверждения ─────────────────────────────────
import imaplib
import email as emaillib
from email.header import decode_header

def imap_get_latest_code(timeout_sec: int = 120) -> str | None:
    """
    Читает последнее письмо с кодом подтверждения через IMAP.
    Опрашивает ящик каждые 5 секунд до timeout_sec.
    """
    import time
    deadline = time.time() + timeout_sec
    seen_ids = set()

    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL("imap.yandex.ru", 993)
            mail.login(YANDEX_EMAIL, YANDEX_PASSWORD)
            mail.select("INBOX")

            # Ищем непрочитанные письма от italyvms
            _, data = mail.search(None, 'UNSEEN')
            ids = data[0].split()

            for mid in reversed(ids):  # сначала свежие
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                _, msg_data = mail.fetch(mid, "(RFC822)")
                raw = msg_data[0][1]
                msg = emaillib.message_from_bytes(raw)

                # Собираем текст письма
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        ct = part.get_content_type()
                        if ct in ("text/plain", "text/html"):
                            try:
                                body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                            except Exception:
                                pass
                else:
                    try:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                    except Exception:
                        pass

                # Ищем код (4-8 цифр)
                codes = re.findall(r"\b(\d{4,8})\b", body)
                if codes:
                    log.info(f"Код из письма: {codes[0]}")
                    mail.logout()
                    return codes[0]

            mail.logout()
        except Exception as e:
            log.warning(f"IMAP error: {e}")

        time.sleep(5)

    log.warning("IMAP: код не найден за отведённое время")
    return None

async def wait_for_code_imap(timeout: int = 120) -> str | None:
    """Асинхронная обёртка для IMAP чтения кода."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, imap_get_latest_code, timeout)

# ─── PLAYWRIGHT ───────────────────────────────────────────────────────────────
def extract_token_from_url(url):
    m = re.search(r"[?&]t=([^&]{10,})", url)
    return m.group(1) if m else None

async def get_token_via_playwright(bot=None):
    """
    Полуавтоматическое обновление токена:
    1. Создаёт временный email (mail.tm) автоматически
    2. Открывает браузер и заполняет форму
    3. Читает код подтверждения из почты автоматически
    4. Тебе остаётся только пройти капчу
    5. Бот ловит токен из URL сам
    """
    log.info("Playwright: создаю временный email...")

    temp_email = YANDEX_EMAIL

    if bot:
        try:
            await bot.send_message(ADMIN_ID,
                f"🔄 *Токен истёк — запускаю обновление...*\n\n"
                f"📧 Email: `{temp_email}`\n"
                f"Открываю браузер и заполняю форму автоматически.\n\n"
                f"⏳ Жди следующего сообщения...",
                parse_mode="Markdown")
        except Exception:
            pass

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()

            await page.goto("https://italyvms.com/autoform/?lang=ru",
                            wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)

            # Может токен уже в URL
            token = extract_token_from_url(page.url)
            if token:
                await browser.close()
                return token

            # ── Шаг 0: страница с предупреждением (галочки + Далее) ──
            try:
                await asyncio.sleep(2)
                # Ставим все галочки на текущей странице
                for cb in await page.query_selector_all("input[type='checkbox']"):
                    if not await cb.is_checked():
                        await cb.check()
                        await asyncio.sleep(0.3)
                        log.info("Поставил галочку на странице предупреждения")
                # Если есть кнопка Далее — жмём
                for sel in ["input[type='button'][value*='алее']", "input[type='submit']",
                            "input[type='button']", "button"]:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        await asyncio.sleep(2)
                        break
            except Exception as e:
                log.warning(f"Шаг 0 (галочки): {e}")

            # ── Шаг 1: основная форма ──
            try:
                await page.wait_for_selector("select[name='center']", timeout=15000)
                await page.select_option("select[name='center']", "1")
                await asyncio.sleep(0.5)
                await page.select_option("select[name='vtype']", "13")
                await asyncio.sleep(0.5)
                await page.fill("input[name='num_of_person']", "1")
                await page.fill("input[name='email']", temp_email)
                await page.fill("input[name='emailcheck']", temp_email)
                # Галочки согласия
                for cb in await page.query_selector_all("input[type='checkbox']"):
                    if not await cb.is_checked():
                        await cb.check()
                        await asyncio.sleep(0.2)
                for sel in ["input[type='button']", "input[type='submit']"]:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        break
                await asyncio.sleep(3)
                log.info(f"Форма заполнена, URL={page.url[:80]}")
            except Exception as e:
                log.warning(f"Ошибка заполнения формы: {e}")

            # ── Проверяем нужен ли код подтверждения ──
            page_text = await page.inner_text("body")
            needs_code = any(w in page_text.lower() for w in ["код", "code", "подтвер", "confirm"])

            if needs_code:
                log.info("Нужен код подтверждения — жду письмо на Яндекс...")
                if bot:
                    try:
                        await bot.send_message(ADMIN_ID,
                            f"📬 *Форма заполнена!*\n\n"
                            f"Жду код подтверждения на:\n`{temp_email}`\n\n"
                            f"Читаю письмо через IMAP автоматически... ⏳",
                            parse_mode="Markdown")
                    except Exception:
                        pass

                code = await wait_for_code_imap(timeout=120)
                if code:
                    # Вводим код в поле
                    for code_sel in ["input[name='code']", "input[name='confirm']",
                                     "input[type='number']", "input[type='text']"]:
                        try:
                            field = await page.query_selector(code_sel)
                            if field:
                                await field.fill(code)
                                await asyncio.sleep(0.5)
                                for btn_sel in ["input[type='button']", "input[type='submit']", "button"]:
                                    btn = await page.query_selector(btn_sel)
                                    if btn:
                                        await btn.click()
                                        break
                                await asyncio.sleep(2)
                                log.info(f"Код {code} введён")
                                break
                        except Exception:
                            pass

                    if bot:
                        try:
                            await bot.send_message(ADMIN_ID,
                                f"✅ *Код `{code}` введён автоматически!*\n\n"
                                f"👀 Посмотри на экран браузера.\n"
                                f"Пройди капчу и нажми *Далее* — токен подхвачу сам. ⏳",
                                parse_mode="Markdown")
                        except Exception:
                            pass
                else:
                    if bot:
                        try:
                            await bot.send_message(ADMIN_ID,
                                f"⚠️ Код не пришёл за 2 минуты.\n"
                                f"Введи вручную в браузере, потом пройди капчу.",
                                parse_mode="Markdown")
                        except Exception:
                            pass
            else:
                # Код не нужен — сразу капча
                if bot:
                    try:
                        await bot.send_message(ADMIN_ID,
                            "✅ *Форма заполнена автоматически!*\n\n"
                            "👀 Посмотри на экран — браузер открыт.\n\n"
                            "Тебе нужно:\n"
                            "1️⃣ Пройти капчу\n"
                            "2️⃣ Нажать *Далее*\n\n"
                            "⏳ Жду 5 минут — токен подхвачу сам.",
                            parse_mode="Markdown")
                    except Exception:
                        pass

            # ── Polling: ждём пока токен реально заработает в API ──
            log.info("Жду прохождения капчи (проверяю API каждые 5 сек)...")
            current_token = extract_token_from_url(page.url)
            for i in range(60):  # до 5 минут (60 × 5 сек)
                await asyncio.sleep(5)
                # Обновляем токен из URL если изменился
                new_url_token = extract_token_from_url(page.url)
                if new_url_token:
                    current_token = new_url_token
                if not current_token:
                    continue
                # Проверяем токен через реальный API
                try:
                    async with httpx.AsyncClient(timeout=10, verify=False) as client:
                        test_url = (f"https://italyvms.com/vcs/get_nearest.htm"
                                    f"?center=1&persons=1&urgent=0"
                                    f"&token={current_token}&lang=ru&vtype=13")
                        r = await client.get(test_url, headers=get_headers())
                        resp_text = r.text.strip()
                        log.info(f"  API test ({i+1}/60): {resp_text[:60]!r}")
                        # Токен рабочий если ответ НЕ содержит "капчу"
                        if "капчу" not in resp_text and "captcha" not in resp_text.lower():
                            log.info("Токен рабочий!")
                            if bot:
                                try:
                                    await bot.send_message(ADMIN_ID,
                                        "🎉 *Токен обновлён успешно!*\n"
                                        "Бот продолжает мониторинг. ✅",
                                        parse_mode="Markdown")
                                except Exception:
                                    pass
                            await browser.close()
                            return current_token
                except Exception as e:
                    log.warning(f"API test error: {e}")

            log.warning("Таймаут 5 минут")
            if bot:
                try:
                    await bot.send_message(ADMIN_ID,
                        "⏰ *Таймаут!* Обнови токен вручную:\n"
                        "1. https://italyvms.com/autoform/?lang=ru\n"
                        "2. Заполни форму, пройди капчу\n"
                        "3. Отправь: `/token ТВОЙ_URL`",
                        parse_mode="Markdown")
                except Exception:
                    pass

            await browser.close()
            return None

    except Exception as e:
        log.error(f"Playwright error: {e}")
        if bot:
            try:
                await bot.send_message(ADMIN_ID,
                    f"❌ Ошибка браузера: `{e}`\n"
                    "Обнови вручную: `/token ТВОЙ_URL`",
                    parse_mode="Markdown")
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
            await bot.send_message(CHANNEL_ID, msg, parse_mode="Markdown",
                                   disable_web_page_preview=True)
            log.info("Published ✅")
        except Exception as e:
            log.error(f"Publish error: {e}")

# ─── JOB ──────────────────────────────────────────────────────────────────────
token_renewal_in_progress = False

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    global token_renewal_in_progress
    log.info("=== Slot check ===")
    state = load_state()
    bot   = context.bot
    token = state.get("token", "")

    if not token:
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
    if captcha_count >= 3 and not token_renewal_in_progress:
        log.info(f"Капча на {captcha_count} направлениях — обновляю токен...")
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
    else:
        log.info("Новых слотов нет.")

    log.info(f"Следующая проверка через {CHECK_INTERVAL//60} мин...")

# ─── COMMANDS ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton("📅 Текущие слоты",  callback_data="slots"),
        InlineKeyboardButton("🔄 Обновить токен", callback_data="renew_token"),
    ]]
    await update.message.reply_text(
        "🇮🇹 *VFS Italy Monitor v9*\n\n"
        "Мониторинг свободных окон на запись в визовый центр.\n\n"
        "/check — проверить прямо сейчас\n"
        "/token URL\_ИЛИ\_ТОКЕН — обновить токен вручную\n"
        "/status — статус бота",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))

async def cmd_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text(
            "Использование:\n`/token ТОКЕН`\n`/token https://italyvms.com/autoform/?t=...`",
            parse_mode="Markdown")
        return
    raw   = ctx.args[0]
    token = extract_token_from_url(raw) or raw
    state = load_state()
    state["token"] = token
    save_state(state)
    await update.message.reply_text(f"✅ Токен обновлён!\n`{token[:50]}...`",
                                    parse_mode="Markdown")

async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg = await update.message.reply_text("🔄 Проверяю слоты...")
    state   = load_state()
    results = await check_slots(state.get("token", ""))
    lines   = []
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
    state         = load_state()
    token         = state.get("token", "")
    token_preview = f"`{token[:30]}...`" if token else "❌ Нет токена"
    slots_count   = sum(1 for v in state.get("slots", {}).values() if v)
    await update.message.reply_text(
        f"📊 *Статус бота v9*\n\n"
        f"Токен: {token_preview}\n"
        f"Направлений со слотами: {slots_count}/{len(TARGETS)}\n"
        f"Интервал: {CHECK_INTERVAL//60} мин",
        parse_mode="Markdown")

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global token_renewal_in_progress
    q = update.callback_query
    await q.answer()

    if q.data == "slots":
        state = load_state()
        await q.edit_message_text("🔄 Проверяю...")
        results = await check_slots(state.get("token", ""))
        lines   = []
        for t in TARGETS:
            v = results.get(t["label"], [])
            if isinstance(v, list) and v:
                url = f"https://italyvms.com/autoform/?t={state.get('token','')}&lang=ru"
                lines.append(f"🟢 [{t['label']}]({url}): {', '.join(v)}")
        await q.edit_message_text(
            "\n".join(lines) if lines else "🔴 Свободных мест нет.",
            parse_mode="Markdown", disable_web_page_preview=True)

    elif q.data == "renew_token":
        if q.from_user.id != ADMIN_ID:
            await q.answer("Только для администратора", show_alert=True)
            return
        await q.edit_message_text("🔄 Запускаю обновление токена...\nСледи за сообщениями.")
        if not token_renewal_in_progress:
            token_renewal_in_progress = True
            new_token = await get_token_via_playwright(ctx.bot)
            token_renewal_in_progress = False
            if new_token:
                state = load_state()
                state["token"] = new_token
                save_state(state)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    log.info("Bot v9 started! Временная почта mail.tm + полуавтоматическое обновление токена")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("token",  cmd_token))
    app.add_handler(CommandHandler("check",  cmd_check))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.job_queue.run_repeating(monitor_job, interval=CHECK_INTERVAL, first=15)
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
