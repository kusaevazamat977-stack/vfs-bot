import asyncio
import json
import logging
import os
import re
import base64
import time
import httpx
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from playwright.async_api import async_playwright

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = "8572069793:AAHQ42H2b6D9QD5e-HaBGs3EM0DmEAFXlOo"
CHANNEL_ID     = "@SamSebeTur1"
ADMIN_ID       = 1020509234
CAPTCHA_KEY    = "59a9f897c7b64793c2ac84d4ffec4b34"
PROXIES = [
    "http://user409265:y41xol@138.249.26.253:6085",
    "http://user409265:y41xol@193.33.67.76:3390",
    "http://user409265:y41xol@45.85.67.188:3390",
]
proxy_index = 0

def get_proxy() -> str:
    global proxy_index
    proxy = PROXIES[proxy_index % len(PROXIES)]
    proxy_index += 1
    return proxy
CHECK_INTERVAL = 1800   # 30 минут
STATE_FILE     = "C:\\vfs_bot\\last_slots.json"

# ─── TARGETS ──────────────────────────────────────────────────────────────────
TARGETS = [
    {"center": 1,  "vtype": 13, "label": "Москва / Туризм",        "vtype_id": "13"},
    {"center": 1,  "vtype": 1,  "label": "Москва / Бизнес",        "vtype_id": "1"},
    {"center": 1,  "vtype": 4,  "label": "Москва / Приглашение",   "vtype_id": "4"},
    {"center": 11, "vtype": 13, "label": "СПб / Туризм",           "vtype_id": "13"},
    {"center": 11, "vtype": 1,  "label": "СПб / Бизнес",           "vtype_id": "1"},
    {"center": 11, "vtype": 4,  "label": "СПб / Приглашение",      "vtype_id": "4"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://italyvms.com/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s,%(msecs)03d [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
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

# ─── 2CAPTCHA: решаем image-капчу ────────────────────────────────────────────
async def solve_image_captcha(image_base64: str) -> str | None:
    """Отправляем скриншот капчи в 2captcha, получаем текстовый ответ."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Отправляем капчу
            resp = await client.post("http://2captcha.com/in.php", data={
                "key": CAPTCHA_KEY,
                "method": "base64",
                "body": image_base64,
                "json": 1,
            })
            data = resp.json()
            if data.get("status") != 1:
                log.warning(f"2captcha submit error: {data}")
                return None
            captcha_id = data["request"]
            log.info(f"2captcha: captcha sent, id={captcha_id}")

            # Ждём решения (до 120 сек)
            for _ in range(24):
                await asyncio.sleep(5)
                res = await client.get(f"http://2captcha.com/res.php?key={CAPTCHA_KEY}&action=get&id={captcha_id}&json=1")
                rdata = res.json()
                if rdata.get("status") == 1:
                    answer = rdata["request"]
                    log.info(f"2captcha: solved -> {answer}")
                    return answer
                if rdata.get("request") != "CAPCHA_NOT_READY":
                    log.warning(f"2captcha error: {rdata}")
                    return None
            log.warning("2captcha: timeout")
            return None
    except Exception as e:
        log.error(f"2captcha exception: {e}")
        return None

# ─── PLAYWRIGHT: получаем токен автоматически ────────────────────────────────
async def get_token_via_playwright() -> str | None:
    """
    Открывает italyvms.com, заполняет форму, решает капчу через 2captcha,
    возвращает токен из URL.
    """
    log.info("Playwright: starting browser to get new token...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            # 1. Открываем форму
            await page.goto("https://italyvms.com/autoform/?lang=ru", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)

            log.info(f"Playwright: page loaded, URL={page.url}")

            # 2. Проверяем — может уже есть токен в URL (редирект)
            token = extract_token_from_url(page.url)
            if token:
                log.info(f"Playwright: got token from redirect: {token}")
                await browser.close()
                return token

            # 3. Заполняем форму если она есть
            try:
                await page.wait_for_selector("select[name='center']", timeout=10000)
                await page.select_option("select[name='center']", "1")   # Москва
                await asyncio.sleep(1)
                await page.select_option("select[name='vtype']", "13")   # Туризм
                await page.fill("input[name='num_of_person']", "1")
                await page.fill("input[name='email']", "test@italyvms.ru")
                await page.fill("input[name='emailcheck']", "test@italyvms.ru")

                # Чекбоксы
                for cb in await page.query_selector_all("input[type='checkbox']"):
                    if not await cb.is_checked():
                        await cb.check()

                log.info("Playwright: form filled, clicking Далее...")
                await page.click("input[type='button'][value*='Далее'], input[type='submit']")
                await asyncio.sleep(3)
            except Exception as e:
                log.info(f"Playwright: form step skipped ({e})")

            # 4. Проверяем URL после нажатия
            token = extract_token_from_url(page.url)
            if token:
                log.info(f"Playwright: got token after form: {token}")
                await browser.close()
                return token

            # 5. Ищем капчу на странице
            log.info("Playwright: looking for captcha...")
            captcha_solved = False

            for attempt in range(3):
                # Ищем картинку капчи
                captcha_img = None
                for selector in ["img.captcha", "img[src*='captcha']", ".captcha img", "img[id*='captcha']"]:
                    try:
                        captcha_img = await page.query_selector(selector)
                        if captcha_img:
                            log.info(f"Playwright: found captcha with selector: {selector}")
                            break
                    except Exception:
                        pass

                if not captcha_img:
                    # Делаем скриншот всей страницы и ищем поле ввода капчи
                    log.info("Playwright: no captcha image found, checking for captcha input...")
                    captcha_input = await page.query_selector("input[name='captcha'], input[id*='captcha'], input[placeholder*='апча']")
                    if captcha_input:
                        # Делаем скриншот страницы
                        screenshot = await page.screenshot()
                        img_b64 = base64.b64encode(screenshot).decode()
                        log.info("Playwright: sending full page screenshot to 2captcha...")
                        answer = await solve_image_captcha(img_b64)
                        if answer:
                            await captcha_input.fill(answer)
                            await asyncio.sleep(1)
                            # Ищем кнопку подтверждения
                            for btn_sel in ["input[type='button']", "input[type='submit']", "button[type='submit']"]:
                                btn = await page.query_selector(btn_sel)
                                if btn:
                                    await btn.click()
                                    break
                            await asyncio.sleep(3)
                            captcha_solved = True
                    else:
                        log.info("Playwright: no captcha found on page")
                        break
                else:
                    # Скриншот только картинки капчи
                    img_bytes = await captcha_img.screenshot()
                    img_b64 = base64.b64encode(img_bytes).decode()
                    log.info("Playwright: sending captcha image to 2captcha...")
                    answer = await solve_image_captcha(img_b64)
                    if answer:
                        captcha_input = await page.query_selector("input[name='captcha'], input[id*='captcha'], input[type='text']")
                        if captcha_input:
                            await captcha_input.fill(answer)
                        await asyncio.sleep(1)
                        for btn_sel in ["input[type='button']", "input[type='submit']", "button"]:
                            btn = await page.query_selector(btn_sel)
                            if btn:
                                await btn.click()
                                break
                        await asyncio.sleep(3)
                        captcha_solved = True

                # Проверяем URL после решения капчи
                token = extract_token_from_url(page.url)
                if token:
                    log.info(f"Playwright: got token after captcha: {token}")
                    await browser.close()
                    return token

                if not captcha_solved:
                    break

            # 6. Последняя попытка — перехват сетевых запросов
            log.info("Playwright: trying network interception...")
            found_token = None

            async def handle_request(request):
                nonlocal found_token
                t = extract_token_from_url(request.url)
                if t:
                    found_token = t

            page.on("request", handle_request)
            await page.reload(wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)

            if found_token:
                log.info(f"Playwright: got token from network: {found_token}")
                await browser.close()
                return found_token

            await browser.close()
            log.warning("Playwright: failed to get token")
            return None

    except Exception as e:
        log.error(f"Playwright error: {e}")
        return None

def extract_token_from_url(url: str) -> str | None:
    m = re.search(r"[?&]t=([^&]+)", url)
    if m:
        t = m.group(1)
        if len(t) > 10:
            return t
    return None

# ─── API: проверяем слоты ─────────────────────────────────────────────────────
async def check_slots(token: str) -> dict:
    results = {}
    for t in TARGETS:
        proxy = get_proxy()
        log.info(f"Using proxy: {proxy.split('@')[1]}")
        async with httpx.AsyncClient(
            proxies={"http://": proxy, "https://": proxy},
            headers=HEADERS,
            timeout=30,
            verify=False,
        ) as client:
            url = (f"https://italyvms.com/vcs/get_nearest.htm"
                   f"?center={t['center']}&persons=1&urgent=0"
                   f"&token={token}&lang=ru&vtype={t['vtype']}")
            try:
                resp = await client.get(url)
                text = resp.text.strip()
                log.info(f"API center={t['center']} vtype={t['vtype']}: {text[:80]!r}")

                if "введите капчу" in text or "captcha" in text.lower():
                    results[t["label"]] = "CAPTCHA"
                elif "записи нет" in text or not text or text == "[]":
                    results[t["label"]] = []
                else:
                    dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", text)
                    results[t["label"]] = dates if dates else []
            except Exception as e:
                log.warning(f"API error for {t['label']}: {e}")
                results[t["label"]] = []
            await asyncio.sleep(5)
    return results

# ─── TELEGRAM: публикация ─────────────────────────────────────────────────────
def booking_url(center: int, vtype: int, token: str) -> str:
    return f"https://italyvms.com/autoform/?t={token}&lang=ru"

async def publish_slots(bot: Bot, state: dict, new_slots: dict):
    token = state.get("token", "")
    lines = []
    for t in TARGETS:
        label = t["label"]
        dates = new_slots.get(label, [])
        if isinstance(dates, list) and dates:
            url = booking_url(t["center"], t["vtype"], token)
            lines.append(f"🟢 *{label}*\nДата: {', '.join(dates)}\n[Записаться]({url})")

    if lines:
        msg = "🇮🇹 *Доступные окна на визу Италии:*\n\n" + "\n\n".join(lines)
        try:
            await bot.send_message(CHANNEL_ID, msg, parse_mode="Markdown", disable_web_page_preview=True)
            log.info("Published to channel ✅")
        except Exception as e:
            log.error(f"Publish error: {e}")

# ─── ГЛАВНЫЙ ЦИКЛ ─────────────────────────────────────────────────────────────
async def monitor_loop(app):
    state = load_state()
    bot = app.bot
    captcha_fails = 0

    while True:
        log.info("Starting slot check...")
        token = state.get("token", "")

        # Проверяем слоты
        results = await check_slots(token)

        # Считаем сколько ответов CAPTCHA
        captcha_count = sum(1 for v in results.values() if v == "CAPTCHA")

        if captcha_count > 0:
            log.info(f"Captcha required on {captcha_count} endpoints. Auto-renewing token...")

            # Пробуем получить новый токен через Playwright + 2captcha
            new_token = await get_token_via_playwright()

            if new_token:
                log.info(f"Token renewed: {new_token}")
                state["token"] = new_token
                save_state(state)
                captcha_fails = 0

                # Перепроверяем с новым токеном
                results = await check_slots(new_token)
            else:
                captcha_fails += 1
                log.warning(f"Token renewal failed ({captcha_fails}/3)")
                if captcha_fails >= 3:
                    # Уведомляем админа
                    try:
                        await bot.send_message(
                            ADMIN_ID,
                            "⚠️ *Не удалось обновить токен автоматически!*\n\n"
                            "1. Зайди на https://italyvms.com/autoform/?lang=ru\n"
                            "2. Заполни форму до страницы с датами\n"
                            "3. Отправь мне: /token НОВЫЙ\\_ТОКЕН\n\n"
                            "Или пришли полный URL со страницы с датами.",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
                    captcha_fails = 0

        # Обновляем и публикуем только новые слоты
        old_slots = state.get("slots", {})
        new_found = {}

        for label, dates in results.items():
            if dates == "CAPTCHA":
                continue
            old = old_slots.get(label, [])
            if isinstance(dates, list) and dates and dates != old:
                new_found[label] = dates

        if new_found:
            await publish_slots(bot, state, new_found)
            # Обновляем состояние
            for label, dates in new_found.items():
                state["slots"][label] = dates
            save_state(state)
        else:
            log.info("No new slots found.")

        log.info(f"Next check in {CHECK_INTERVAL//60} minutes...")
        await asyncio.sleep(CHECK_INTERVAL)

# ─── КОМАНДЫ ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton("📅 Текущие слоты", callback_data="slots"),
        InlineKeyboardButton("🔔 Подписаться", callback_data="subscribe"),
    ]]
    await update.message.reply_text(
        "🇮🇹 *VFS Italy Monitor*\n\nМониторинг свободных окон на запись.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Использование: /token ТОКЕН_ИЛИ_ПОЛНЫЙ_URL")
        return
    raw = ctx.args[0]
    token = extract_token_from_url(raw) or raw
    state = load_state()
    state["token"] = token
    save_state(state)
    log.info(f"Token updated manually: {token}")
    await update.message.reply_text(f"✅ Токен обновлён:\n`{token}`", parse_mode="Markdown")

async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("🔄 Запускаю проверку...")
    state = load_state()
    results = await check_slots(state.get("token", ""))
    lines = []
    for t in TARGETS:
        label = t["label"]
        v = results.get(label, [])
        if v == "CAPTCHA":
            lines.append(f"⚠️ {label}: Капча")
        elif isinstance(v, list) and v:
            lines.append(f"🟢 {label}: {', '.join(v)}")
        else:
            lines.append(f"🔴 {label}: Нет мест")
    await update.message.reply_text("\n".join(lines))

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    state = load_state()

    if q.data == "slots":
        results = await check_slots(state.get("token", ""))
        lines = []
        for t in TARGETS:
            label = t["label"]
            v = results.get(label, [])
            if isinstance(v, list) and v:
                url = booking_url(t["center"], t["vtype"], state.get("token", ""))
                lines.append(f"🟢 [{label}]({url}): {', '.join(v)}")
        msg = "\n".join(lines) if lines else "🔴 Свободных мест нет."
        await q.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

    elif q.data == "subscribe":
        subs_file = "C:\\vfs_bot\\subscribers.json"
        try:
            subs = json.load(open(subs_file, "r", encoding="utf-8")) if os.path.exists(subs_file) else []
        except Exception:
            subs = []
        uid = q.from_user.id
        if uid not in subs:
            subs.append(uid)
            json.dump(subs, open(subs_file, "w", encoding="utf-8"))
            await q.edit_message_text("✅ Вы подписались на уведомления!")
        else:
            await q.edit_message_text("Вы уже подписаны.")

# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    log.info("Bot v6 started! Playwright + 2captcha + Proxy enabled.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("token", cmd_token))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Запускаем monitor_loop как job через job_queue
    async def monitor_job(context):
        await monitor_loop(context.application)

    # Запуск мониторинга через post_init чтобы не конфликтовать с polling
    async def post_init(application):
        # Задержка 5 секунд чтобы polling успел стартовать
        await asyncio.sleep(5)
        asyncio.create_task(monitor_loop(application))

    app.post_init = post_init

    # Запускаем polling — он сам управляет event loop
    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
