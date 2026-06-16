"""
ItalyVMS Slot Monitor Bot v5
- Playwright для автоматического заполнения формы
- 2captcha image captcha для автоматического прохождения капчи
- Прокси для обхода блокировки IP
- /token команда как запасной вариант
"""

import asyncio
import logging
import os
import json
import httpx
import re
import base64
from datetime import datetime
from playwright.async_api import async_playwright, Page

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Конфигурация ─────────────────────────────────────────────────────────────
BOT_TOKEN        = "8623727460:AAGia4P5xYIPXqz5HR5ZyDTd6K5Qc8syvvs"
CHANNEL_ID       = "@SamSebeTur1"
ADMIN_ID         = 1020509234
CHECK_INTERVAL   = 1800  # 30 минут
CAPTCHA_API_KEY  = "59a9f897c7b64793c2ac84d4ffec4b34"
PROXY_USER       = "user409265"
PROXY_PASS       = "y41xol"
PROXY_HOST       = "138.249.26.253"
PROXY_PORT       = "6085"
PROXY_URL        = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"

SESSION_TOKEN    = "t76getfpgv-5258588-98g1pe2dtuzbx4220npbp53smas0iwq7kxegangl2nhep"

TARGETS = [
    ("1",  "Москва (Толмачевский)", "13", "Туризм"),
    ("1",  "Москва (Толмачевский)", "1",  "Бизнес"),
    ("1",  "Москва (Толмачевский)", "4",  "Приглашение"),
    ("11", "Санкт-Петербург",       "13", "Туризм"),
    ("11", "Санкт-Петербург",       "1",  "Бизнес"),
    ("11", "Санкт-Петербург",       "4",  "Приглашение"),
]

SLOTS_FILE       = "C:\\vfs_bot\\last_slots.json"
SUBSCRIBERS_FILE = "C:\\vfs_bot\\subscribers.json"
last_known: dict = {}
subscribers: set = set()
token_expired: bool = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://italyvms.com/",
    "Accept": "application/json, text/javascript, */*",
}

# ─── Хранилище ────────────────────────────────────────────────────────────────
def load_state():
    global last_known, subscribers, SESSION_TOKEN
    try:
        if os.path.exists(SLOTS_FILE):
            with open(SLOTS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        last_known = data.get("slots", {})
                        saved_token = data.get("token", "")
                        if saved_token:
                            SESSION_TOKEN = saved_token
    except Exception as e:
        logger.warning(f"Could not load state: {e}")
        last_known = {}
    try:
        if os.path.exists(SUBSCRIBERS_FILE):
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    subscribers = set(json.loads(content))
    except Exception as e:
        logger.warning(f"Could not load subscribers: {e}")
        subscribers = set()


def save_state():
    try:
        with open(SLOTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"slots": last_known, "token": SESSION_TOKEN}, f, ensure_ascii=True, indent=2)
    except Exception as e:
        logger.error(f"Could not save state: {e}")
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(subscribers), f, ensure_ascii=True)
    except Exception as e:
        logger.error(f"Could not save subscribers: {e}")


# ─── 2captcha: решение image captcha ─────────────────────────────────────────
async def solve_image_captcha(image_base64: str) -> str:
    logger.info("Sending captcha to 2captcha...")
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post("https://2captcha.com/in.php", data={
            "key": CAPTCHA_API_KEY,
            "method": "base64",
            "body": image_base64,
            "json": 1,
        })
        result = r.json()
        if result.get("status") != 1:
            logger.error(f"2captcha submit error: {result}")
            return ""
        task_id = result["request"]
        logger.info(f"Captcha task {task_id}, waiting...")
        for _ in range(30):
            await asyncio.sleep(5)
            r = await client.get(
                f"https://2captcha.com/res.php?key={CAPTCHA_API_KEY}&action=get&id={task_id}&json=1"
            )
            res = r.json()
            if res.get("status") == 1:
                logger.info(f"Captcha solved: {res['request']}")
                return res["request"]
            if "ERROR" in str(res.get("request", "")):
                logger.error(f"2captcha error: {res}")
                return ""
    logger.error("Captcha timeout")
    return ""


# ─── Playwright: получить новый токен автоматически ──────────────────────────
async def get_token_via_playwright() -> str:
    global SESSION_TOKEN, token_expired
    logger.info("Getting new token via Playwright + 2captcha...")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy={"server": f"http://{PROXY_HOST}:{PROXY_PORT}",
                       "username": PROXY_USER, "password": PROXY_PASS},
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()

            # Открываем форму
            await page.goto("https://italyvms.com/autoform/?lang=ru", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)

            # Заполняем форму
            try:
                await page.select_option("select[name='center']", "1")
                await asyncio.sleep(1)
                await page.select_option("select[name='vtype']", "13")
                await asyncio.sleep(1)
                await page.fill("input[name='num_of_person']", "1")
                await page.fill("input[name='email']", "201007azik@mail.ru")
                await page.fill("input[name='emailcheck']", "201007azik@mail.ru")
                await asyncio.sleep(0.5)

                # Чекбоксы
                for cb in await page.query_selector_all("input[type='checkbox']"):
                    await cb.check()
                await asyncio.sleep(0.5)

                await page.click("input[type='button']")
                await asyncio.sleep(3)
            except Exception as e:
                logger.warning(f"Form fill error: {e}")

            # Ищем капчу на странице
            for attempt in range(5):
                current_url = page.url
                logger.info(f"Current URL: {current_url}")

                # Проверяем есть ли токен в URL
                token_match = re.search(r'[?&]t=([\w\-]+)', current_url)
                if token_match:
                    new_token = token_match.group(1)
                    logger.info(f"Got token from URL: {new_token[:20]}...")
                    SESSION_TOKEN = new_token
                    token_expired = False
                    save_state()
                    await browser.close()
                    return new_token

                # Ищем капчу (изображение с вопросом)
                captcha_img = await page.query_selector("img.captcha, .captcha img, img[src*='captcha'], .tip-yellowsimple img")
                if captcha_img:
                    logger.info("Found captcha image, solving...")
                    img_bytes = await captcha_img.screenshot()
                    img_b64 = base64.b64encode(img_bytes).decode()
                    answer = await solve_image_captcha(img_b64)
                    if answer:
                        # Вводим ответ
                        captcha_input = await page.query_selector("input[name='captcha'], input.captcha-input, input[type='text'][name*='cap']")
                        if captcha_input:
                            await captcha_input.fill(answer)
                            await page.click("input[type='button'], button[type='submit']")
                            await asyncio.sleep(2)
                            continue

                # Продолжаем нажимать Далее
                next_btn = await page.query_selector("input[value*='Далее'], input[value*='далее']")
                if next_btn:
                    await next_btn.click()
                    await asyncio.sleep(2)
                else:
                    break

            await browser.close()
    except Exception as e:
        logger.error(f"Playwright error: {e}")

    logger.warning("Could not get token automatically")
    return ""


# ─── API запрос к italyvms ────────────────────────────────────────────────────
async def check_slots_api(center: str, vtype: str) -> list:
    global token_expired
    url = "https://italyvms.com/vcs/get_nearest.htm"
    params = {
        "center": center, "persons": "1", "urgent": "0",
        "token": SESSION_TOKEN, "lang": "ru", "vtype": vtype,
    }
    try:
        proxies = {"http://": PROXY_URL, "https://": PROXY_URL}
        async with httpx.AsyncClient(headers=HEADERS, proxies=proxies, timeout=30) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                text = r.text.strip()
                logger.info(f"API center={center} vtype={vtype}: '{text[:60]}'")
                if text and text not in ("", "null", "false"):
                    if "капч" in text.lower() or "введите" in text.lower():
                        logger.warning("Captcha required!")
                        token_expired = True
                        return []
                    if re.match(r'\d{2}\.\d{2}\.\d{4}', text):
                        token_expired = False
                        return [text]
                    if "нет" in text.lower() or "2 недели" in text.lower():
                        return []
            elif r.status_code == 403:
                token_expired = True
    except Exception as e:
        logger.error(f"API error: {e}")
    return []


# ─── Форматирование ───────────────────────────────────────────────────────────
def make_link(center: str, vtype: str) -> str:
    return f"https://italyvms.com/autoform/?t={SESSION_TOKEN}&lang=ru&center={center}&vtype={vtype}"


def format_message(results: dict) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"<b>Italyvms.com — доступные окна zapisи</b>", f"Обновлено: {now} МСК
"]
    has_slots = False
    for (center, city_name, vtype, visa_name), dates in results.items():
        if dates:
            has_slots = True
            link = make_link(center, vtype)
            lines.append(f"<b>{city_name} / {visa_name}</b>")
            lines.append(f"Дата: {' * '.join(dates)}")
            lines.append(f'<a href="{link}">Записаться</a>
')
    if not has_slots:
        lines.append("Свободных мест нет. Следующая проверка через 30 минут.")
    return "
".join(lines)


# ─── Основной цикл ────────────────────────────────────────────────────────────
async def monitor_loop(bot: Bot):
    global last_known, token_expired
    auto_retry_count = 0

    while True:
        if token_expired:
            logger.info("Token expired, trying auto-renewal via Playwright...")
            new_token = await get_token_via_playwright()
            if new_token:
                logger.info("Token auto-renewed successfully!")
                auto_retry_count = 0
            else:
                auto_retry_count += 1
                if auto_retry_count <= 3:
                    logger.warning(f"Auto-renewal failed (attempt {auto_retry_count}/3), retrying in 5 min...")
                    await asyncio.sleep(300)
                    continue
                else:
                    # После 3 неудачных попыток — уведомляем админа
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_ID,
                            text=(
                                "Не удалось автоматически обновить токен!\n\n"
                                "1. Зайди на italyvms.com/autoform/?lang=ru\n"
                                "2. Заполни форму до страницы с датами\n"
                                "3. Скопируй токен из адресной строки\n"
                                "4. Отправь: /token НОВЫЙ_ТОКЕН"
                            )
                        )
                    except Exception:
                        pass
                    auto_retry_count = 0
                    await asyncio.sleep(600)
                    continue

        logger.info("Starting slot check...")
        results = {}
        changed = False

        for i, (center, city_name, vtype, visa_name) in enumerate(TARGETS):
            key = f"{city_name}/{visa_name}"
            logger.info(f"Checking {i+1}/{len(TARGETS)}: {key}")
            dates = await check_slots_api(center, vtype)
            results[(center, city_name, vtype, visa_name)] = dates

            prev = last_known.get(key, [])
            new_dates = [d for d in dates if d not in prev]
            if new_dates:
                changed = True
                try:
                    await bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"Новые окна!\n{city_name} / {visa_name}: {', '.join(new_dates)}"
                    )
                except Exception:
                    pass

            last_known[key] = dates
            await asyncio.sleep(10)

        if token_expired:
            continue

        # Публикуем только если есть свободные места
        has_any_slots = any(dates for dates in results.values())
        if has_any_slots:
            try:
                msg = format_message(results)
                await bot.send_message(
                    chat_id=CHANNEL_ID, text=msg,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                logger.info("Published to channel")
            except Exception as e:
                logger.error(f"Channel error: {e}")
        else:
            logger.info("No slots available, skipping channel post")

        if changed and subscribers:
            for chat_id in list(subscribers):
                try:
                    await bot.send_message(chat_id=chat_id, text="Появились новые окна! Смотри канал.")
                except Exception:
                    pass

        save_state()
        logger.info(f"Next check in 30 min...")
        await asyncio.sleep(CHECK_INTERVAL)


# ─── Telegram команды ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Текущие слоты", callback_data="slots"),
        InlineKeyboardButton("Подписаться", callback_data="subscribe"),
    ]])
    await update.message.reply_text(
        "Бот мониторит italyvms.com\nГорода: Москва, Санкт-Петербург\nТипы: Туризм, Бизнес, Приглашение",
        reply_markup=kb
    )


async def cmd_slots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not last_known:
        await update.message.reply_text("Данных ещё нет, подождите первую проверку.")
        return
    lines = ["<b>Текущие слоты:</b>\n"]
    has_slots = False
    kb_buttons = []
    for key, dates in last_known.items():
        if dates:
            has_slots = True
            # Находим center и vtype для ссылки
            for (center, city_name, vtype, visa_name) in TARGETS:
                if f"{city_name}/{visa_name}" == key:
                    link = make_link(center, vtype)
                    lines.append(f"<b>{key}</b>: {', '.join(dates)}")
                    kb_buttons.append([InlineKeyboardButton(f"Записаться: {key}", url=link)])
                    break
    if not has_slots:
        lines.append("Свободных мест нет.")
    kb = InlineKeyboardMarkup(kb_buttons) if kb_buttons else None
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SESSION_TOKEN, token_expired
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    if not ctx.args:
        await update.message.reply_text("Использование: /token НОВЫЙ_ТОКЕН")
        return
    new_token = ctx.args[0].strip()
    match = re.search(r'[?&]t=([\w\-]+)', new_token)
    if match:
        new_token = match.group(1)
    SESSION_TOKEN = new_token
    token_expired = False
    save_state()
    await update.message.reply_text(f"Токен обновлён! Бот продолжает работу.")


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    subscribers.add(update.effective_chat.id)
    save_state()
    await update.message.reply_text("Подписка оформлена!")


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    subscribers.discard(update.effective_chat.id)
    save_state()
    await update.message.reply_text("Подписка отменена.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    total = sum(1 for v in last_known.values() if v)
    status = "ТОКЕН ИСТЁК!" if token_expired else "Работает"
    await update.message.reply_text(
        f"Статус: {status}\n"
        f"Направлений с местами: {total}/{len(TARGETS)}\n"
        f"Подписчиков: {len(subscribers)}\n"
        f"Интервал: 30 мин\n"
        f"Прокси: {PROXY_HOST}\n"
        f"2captcha: подключен\n"
        f"Токен: {SESSION_TOKEN[:20]}..."
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
    logger.info(f"Starting Bot v5 with token: {SESSION_TOKEN[:20]}...")
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
    logger.info("Bot v5 started! Playwright + 2captcha + Proxy enabled.")
    await monitor_loop(app.bot)


if __name__ == "__main__":
    asyncio.run(main())
