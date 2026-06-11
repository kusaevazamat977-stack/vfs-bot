"""
ItalyVMS Slot Monitor Bot v2
Мониторит доступные окна записи через прямой API italyvms.com
Города: Москва и Санкт-Петербург
Типы виз: Turismo (13), Affari (1), Invito (4)
"""

import asyncio
import logging
import os
import json
import httpx
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
BOT_TOKEN      = "8623727460:AAGia4P5xYIPXqz5HR5ZyDTd6K5Qc8syvvs"
CHANNEL_ID     = "-1003947723186"
CHECK_INTERVAL = 900
SESSION_TOKEN  = "tsimtc3r09-5242747-tu4ip5ygy72do7at29e5cu3pek2wz5ovc375tgsvqc0nl"

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


# ─── API запрос ──────────────────────────────────────────────────────────────
async def check_slots_api(center: str, vtype: str) -> list:
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
        async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                text = r.text.strip()
                if text and text not in ("", "null", "false"):
                    # API возвращает дату в формате DD.MM.YYYY
                    return [text]
            elif r.status_code == 403:
                logger.warning(f"403 for center={center} vtype={vtype} - token may be expired")
    except Exception as e:
        logger.error(f"API error center={center} vtype={vtype}: {e}")
    return []


# ─── Форматирование сообщения ─────────────────────────────────────────────────
def format_message(results: dict) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"<b>Italyvms.com — доступные окна записи</b>", f"Обновлено: {now} МСК\n"]
    city_icons = {"Москва (Толмачевский)": "Москва", "Санкт-Петербург": "Санкт-Петербург"}
    visa_icons = {"Туризм": "Туризм", "Бизнес": "Бизнес", "Приглашение": "Приглашение"}

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
            await asyncio.sleep(3)

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
        logger.info(f"Next check in {CHECK_INTERVAL} seconds...")
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
        await update.message.reply_text("Данных ещё нет, подождите первую проверку (~15 мин).")
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
    await update.message.reply_text("Подписка оформлена! Получишь уведомление когда появятся новые слоты.")


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    subscribers.discard(update.effective_chat.id)
    save_state()
    await update.message.reply_text("Подписка отменена.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    total = sum(1 for v in last_known.values() if v)
    await update.message.reply_text(
        f"Статус бота:\nНаправлений с местами: {total}/{len(TARGETS)}\nПодписчиков: {len(subscribers)}\nИнтервал: {CHECK_INTERVAL//60} мин"
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
    logger.info("Bot started! Beginning monitor loop...")
    await monitor_loop(app.bot)


if __name__ == "__main__":
    asyncio.run(main())
