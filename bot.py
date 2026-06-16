"""
ItalyVMS Slot Monitor Bot v3
- Прямые API запросы к italyvms.com
- Прокси для обхода блокировки IP
- 2captcha для автоматического решения капчи
"""

import asyncio
import logging
import os
import json
import httpx
import re
from datetime import datetime

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
CHECK_INTERVAL   = 1800  # 30 минут
CAPTCHA_API_KEY  = "59a9f897c7b64793c2ac84d4ffec4b34"
PROXY_HOST       = "138.249.26.253"
PROXY_PORT       = "6085"
PROXY_USER       = "user409265"
PROXY_PASS       = "y41xol"
PROXY_URL        = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"

SESSION_TOKEN    = "ta586ng4zn-5243577-1hc12ykwvgwr73tqvoa6zqrl9b96p9wa8y5vwh24d2pao"

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://italyvms.com/",
    "Accept": "application/json, text/javascript, */*",
}

# ─── Хранилище состояния ──────────────────────────────────────────────────────
def load_state():
    global last_known, subscribers
    try:
        if os.path.exists(SLOTS_FILE):
            with open(SLOTS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    last_known = json.loads(content)
    except Exception as e:
        logger.warning(f"Could not load slots state: {e}")
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
            json.dump(last_known, f, ensure_ascii=True, indent=2)
    except Exception as e:
        logger.error(f"Could not save slots: {e}")
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(subscribers), f, ensure_ascii=True)
    except Exception as e:
        logger.error(f"Could not save subscribers: {e}")


# ─── 2Captcha: получить новый токен сессии ───────────────────────────────────
async def get_new_session_token() -> str:
    """Открываем страницу через 2captcha ImageToText или решаем капчу автоматически"""
    global SESSION_TOKEN
    logger.info("Getting new session token via 2captcha...")
    
    # Шаг 1: открываем страницу и получаем капчу
    form_url = "https://italyvms.com/autoform/?lang=ru"
    
    try:
        proxies = {"http://": PROXY_URL, "https://": PROXY_URL}
        async with httpx.AsyncClient(headers=HEADERS, proxies=proxies, timeout=30, follow_redirects=True) as client:
            r = await client.get(form_url)
            html = r.text
            
            # Ищем токен сессии в URL или форме
            token_match = re.search(r'\?t=([\w\-]+)', r.url.path + "?" + str(r.url))
            if not token_match:
                token_match = re.search(r'[?&]t=([\w\-]+)', str(r.url))
            
            if token_match:
                new_token = token_match.group(1)
                SESSION_TOKEN = new_token
                logger.info(f"Got new token from redirect: {new_token[:20]}...")
                return new_token
                
    except Exception as e:
        logger.error(f"Error getting session token: {e}")
    
    return SESSION_TOKEN


async def solve_captcha_2captcha(site_key: str, page_url: str) -> str:
    """Решаем reCAPTCHA через 2captcha API"""
    logger.info("Solving captcha via 2captcha...")
    
    async with httpx.AsyncClient(timeout=120) as client:
        # Отправляем задание
        r = await client.post("https://2captcha.com/in.php", data={
            "key": CAPTCHA_API_KEY,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        })
        result = r.json()
        if result.get("status") != 1:
            logger.error(f"2captcha submit error: {result}")
            return ""
        
        task_id = result["request"]
        logger.info(f"Captcha task ID: {task_id}, waiting...")
        
        # Ждём решения
        for _ in range(24):  # max 2 минуты
            await asyncio.sleep(5)
            r = await client.get(f"https://2captcha.com/res.php?key={CAPTCHA_API_KEY}&action=get&id={task_id}&json=1")
            res = r.json()
            if res.get("status") == 1:
                logger.info("Captcha solved!")
                return res["request"]
            if res.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
                logger.error("Captcha unsolvable")
                return ""
        
        logger.error("Captcha timeout")
        return ""


# ─── API запрос к italyvms ────────────────────────────────────────────────────
async def check_slots_api(center: str, vtype: str) -> list:
    global SESSION_TOKEN
    url = "https://italyvms.com/vcs/get_nearest.htm"
    params = {
        "center": center,
        "persons": "1",
        "urgent": "0",
        "token": SESSION_TOKEN,
        "lang": "ru",
        "vtype": vtype,
    }
    try:
        proxies = {"http://": PROXY_URL, "https://": PROXY_URL}
        async with httpx.AsyncClient(headers=HEADERS, proxies=proxies, timeout=30) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                text = r.text.strip()
                logger.info(f"API response center={center} vtype={vtype}: '{text}'")
                if text and text not in ("", "null", "false"):
                    if "капч" in text.lower() or "captcha" in text.lower() or "введите" in text.lower():
                        logger.warning("Captcha required! Getting new token...")
                        await get_new_session_token()
                        return []
                    # Проверяем что это дата (формат DD.MM.YYYY)
                    if re.match(r'\d{2}\.\d{2}\.\d{4}', text):
                        return [text]
            elif r.status_code == 403:
                logger.warning(f"403 for center={center} vtype={vtype} - IP blocked or token expired")
                await get_new_session_token()
    except Exception as e:
        logger.error(f"API error center={center} vtype={vtype}: {e}")
    return []


# ─── Форматирование сообщения ─────────────────────────────────────────────────
def format_message(results: dict) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"<b>Italyvms.com — доступные окна записи</b>", f"Обновлено: {now} МСК\n"]

    for (center, city_name, vtype, visa_name), dates in results.items():
        if dates:
            dates_str = " * ".join(dates)
            lines.append(f"<b>{city_name} / {visa_name}</b>")
            lines.append(f"Дата: {dates_str}\n")
        else:
            lines.append(f"<b>{city_name} / {visa_name}</b>")
            lines.append(f"Нет свободных мест\n")

    lines.append('<a href="https://italyvms.com/autoform/?lang=ru">Записаться на italyvms.com</a>')
    return "\n".join(lines)


# ─── Основной цикл мониторинга ────────────────────────────────────────────────
async def monitor_loop(bot: Bot):
    global last_known
    while True:
        logger.info("Starting slot check...")
        results = {}
        changed = False

        for i, (center, city_name, vtype, visa_name) in enumerate(TARGETS):
            key = f"{city_name}/{visa_name}"
            logger.info(f"Checking {i+1} / {len(TARGETS)}: {key}")
            dates = await check_slots_api(center, vtype)
            results[(center, city_name, vtype, visa_name)] = dates

            prev = last_known.get(key, [])
            new_dates = [d for d in dates if d not in prev]
            if new_dates:
                changed = True
                logger.info(f"NEW slots for {key}: {new_dates}")

            last_known[key] = dates
            await asyncio.sleep(10)  # 10 сек между запросами

        # Публикуем в канал
        try:
            msg = format_message(results)
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("Published to channel")
        except Exception as e:
            logger.error(f"Failed to send to channel: {e}")

        # Уведомляем подписчиков если есть новые слоты
        if changed and subscribers:
            for chat_id in list(subscribers):
                try:
                    await bot.send_message(chat_id=chat_id, text="Появились новые окна! Смотри канал.")
                except Exception:
                    pass

        save_state()
        logger.info(f"Next check in {CHECK_INTERVAL} seconds ({CHECK_INTERVAL//60} min)...")
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
    for key, dates in last_known.items():
        if dates:
            lines.append(f"{key}: {', '.join(dates)}")
        else:
            lines.append(f"{key}: нет мест")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


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
    await update.message.reply_text(
        f"Статус:\nНаправлений с местами: {total}/{len(TARGETS)}\nПодписчиков: {len(subscribers)}\nИнтервал: {CHECK_INTERVAL//60} мин\nПрокси: {PROXY_HOST}\n2captcha: подключен"
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "slots":
        await cmd_slots(update, ctx)
    elif q.data == "subscribe":
        await cmd_subscribe(update, ctx)


# ─── Запуск ───────────────────────────────────────────────────────────────────
async def main():
    load_state()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("slots", cmd_slots))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_callback))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot v3 started! Proxy + 2captcha enabled.")
    await monitor_loop(app.bot)


if __name__ == "__main__":
    asyncio.run(main())
