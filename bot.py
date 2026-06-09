"""
VFS Global Slot Monitor Bot
Германия, Испания, Франция — Москва и СПб
"""

import asyncio
import logging
import os
import json
import random
import httpx
from datetime import datetime
from typing import Optional

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Конфигурация ─────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN", "8623727460:AAGia4P5xYIPXqz5HR5ZyDTd6K5Qc8syvvs")
CHANNEL_ID     = os.getenv("CHANNEL_ID", "-1003947723186")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "900"))

# Только 3 страны — Германия, Испания, Франция
TARGETS = [
    ("deu", "🇩🇪 Германия",  "Moscow",           "Москва"),
    ("deu", "🇩🇪 Германия",  "Saint Petersburg", "Санкт-Петербург"),
    ("esp", "🇪🇸 Испания",   "Moscow",           "Москва"),
    ("esp", "🇪🇸 Испания",   "Saint Petersburg", "Санкт-Петербург"),
    ("fra", "🇫🇷 Франция",   "Moscow",           "Москва"),
    ("fra", "🇫🇷 Франция",   "Saint Petersburg", "Санкт-Петербург"),
]

# Разные User-Agent для ротации
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ─── Хранилище состояния ──────────────────────────────────────────────────────
SLOTS_FILE       = "last_slots.json"
SUBSCRIBERS_FILE = "subscribers.json"
last_known: dict = {}
subscribers: set = set()


def load_state():
    global last_known, subscribers
    if os.path.exists(SLOTS_FILE):
        with open(SLOTS_FILE) as f:
            last_known = json.load(f)
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE) as f:
            subscribers = set(json.load(f))


def save_state():
    with open(SLOTS_FILE, "w") as f:
        json.dump(last_known, f, ensure_ascii=False, indent=2)
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(list(subscribers), f)


# ─── HTTP парсер с обходом защиты ─────────────────────────────────────────────
async def check_slots_for_target(
    country_code: str,
    city_name: str,
) -> Optional[list]:
    """
    Проверяет доступные слоты через прямые HTTP запросы к VFS API.
    VFS использует Angular + REST API, перехватываем запросы.
    """
    ua = random.choice(USER_AGENTS)
    
    # Заголовки максимально похожие на реальный браузер
    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"https://visa.vfsglobal.com/rus/en/{country_code}/book-an-appointment",
        "Origin": "https://visa.vfsglobal.com",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    # Известные API endpoints VFS Global
    api_urls = [
        # Основной API для получения дат
        f"https://visa.vfsglobal.com/api/appointment/appointment/get-available-dates"
        f"?missionCode={country_code.upper()}&locationCode={city_name.replace(' ', '%20')}&categoryCode=TOURVIS",
        
        # Альтернативный endpoint
        f"https://visa.vfsglobal.com/api/appointment/appointment/get-slots"
        f"?countryCode=RUS&missionCode={country_code.upper()}&centerCode={city_name.replace(' ', '%20')}",
        
        # Ещё один вариант
        f"https://visa.vfsglobal.com/rus/en/{country_code}/appointment/get-available-appointments"
        f"?locationCode={city_name.replace(' ', '%20')}",
    ]

    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        verify=False,  # Некоторые прокси могут иметь проблемы с SSL
    ) as client:
        # Сначала делаем запрос на главную страницу для получения cookies
        try:
            main_url = f"https://visa.vfsglobal.com/rus/en/{country_code}/book-an-appointment"
            resp = await client.get(main_url, headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
            })
            logger.info(f"Main page status for {country_code}/{city_name}: {resp.status_code}")
            
            # Добавляем задержку как реальный пользователь
            await asyncio.sleep(random.uniform(2, 5))
            
        except Exception as e:
            logger.warning(f"Main page error {country_code}/{city_name}: {e}")

        # Пробуем все API endpoints
        for api_url in api_urls:
            try:
                resp = await client.get(api_url, headers=headers)
                logger.info(f"API {api_url[:60]}... status: {resp.status_code}")
                
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        dates = []
                        
                        if isinstance(data, list):
                            for item in data:
                                d = (item.get("appointmentDate") or 
                                     item.get("date") or 
                                     item.get("slotDate") or
                                     item.get("availableDate"))
                                if d:
                                    dates.append(str(d)[:10])
                        elif isinstance(data, dict):
                            raw = (data.get("availableDates") or 
                                   data.get("dates") or 
                                   data.get("slots") or [])
                            for d in raw:
                                if isinstance(d, str):
                                    dates.append(d[:10])
                                elif isinstance(d, dict):
                                    dd = d.get("date") or d.get("appointmentDate")
                                    if dd:
                                        dates.append(str(dd)[:10])
                        
                        if dates:
                            logger.info(f"Found {len(dates)} dates for {country_code}/{city_name}")
                            return sorted(set(dates))
                        else:
                            logger.info(f"Empty response for {country_code}/{city_name}")
                            return []
                            
                    except Exception as e:
                        logger.warning(f"JSON parse error: {e}, body: {resp.text[:200]}")
                        
                elif resp.status_code == 403:
                    logger.warning(f"Blocked by Cloudflare for {country_code}/{city_name}")
                    return None  # Заблокировано
                    
                elif resp.status_code == 404:
                    continue  # Попробуем следующий endpoint
                    
                await asyncio.sleep(random.uniform(1, 3))
                
            except Exception as e:
                logger.warning(f"Request error {api_url[:50]}: {e}")
                continue

    return None


async def check_all_slots() -> dict:
    results = {}
    for country_code, country_name, city_slug, city_name in TARGETS:
        key = f"{country_code}_{city_slug.replace(' ', '_')}"
        logger.info(f"Checking {country_name} / {city_name}...")
        dates = await check_slots_for_target(country_code, city_slug)
        results[key] = {
            "country_code": country_code,
            "country_name": country_name,
            "city_name": city_name,
            "dates": dates,
        }
        await asyncio.sleep(random.uniform(5, 10))
    return results


# ─── Форматирование сообщений ─────────────────────────────────────────────────
def format_slots_message(results: dict, changed_only: bool = False) -> Optional[str]:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"🗓 <b>VFS Global — доступные окна</b>\n<i>Обновлено: {now} МСК</i>\n"]
    has_content = False

    for key, info in results.items():
        dates = info.get("dates")
        country = info["country_name"]
        city = info["city_name"]

        if changed_only:
            prev = last_known.get(key, {}).get("dates")
            if prev == dates:
                continue

        if dates is None:
            status = "⚠️ <i>сайт недоступен (Cloudflare)</i>"
        elif len(dates) == 0:
            if not changed_only:
                status = "🔴 Нет свободных мест"
            else:
                continue
        else:
            dates_str = " · ".join(dates[:10])
            if len(dates) > 10:
                dates_str += f" (+{len(dates)-10})"
            status = f"🟢 <b>{len(dates)} дат:</b> {dates_str}"

        lines.append(f"<b>{country} — {city}</b>\n{status}\n")
        has_content = True

    if not has_content:
        return None

    lines.append(f'<a href="https://visa.vfsglobal.com/rus/en/">🔗 Записаться на VFS Global</a>')
    return "\n".join(lines)


# ─── Цикл мониторинга ─────────────────────────────────────────────────────────
async def monitor_loop(bot: Bot):
    global last_known
    while True:
        try:
            logger.info("Starting slot check...")
            results = await check_all_slots()

            new_slots = {}
            for key, info in results.items():
                prev = last_known.get(key, {}).get("dates")
                curr = info.get("dates")
                if curr and (prev is None or prev == []):
                    new_slots[key] = info

            changed = {
                k: v for k, v in results.items()
                if last_known.get(k, {}).get("dates") != v.get("dates")
            }

            last_known = {k: v for k, v in results.items()}
            save_state()

            if changed:
                msg = format_slots_message(changed, changed_only=True)
                if msg:
                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=msg,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )

            if new_slots and subscribers:
                msg = format_slots_message(new_slots)
                if msg:
                    alert = "🔔 <b>Появились новые окна на запись!</b>\n\n" + msg
                    for chat_id in list(subscribers):
                        try:
                            await bot.send_message(
                                chat_id=chat_id,
                                text=alert,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to notify {chat_id}: {e}")
                            if "blocked" in str(e).lower() or "chat not found" in str(e).lower():
                                subscribers.discard(chat_id)
                    save_state()

        except Exception as e:
            logger.error(f"Monitor loop error: {e}", exc_info=True)

        logger.info(f"Next check in {CHECK_INTERVAL} seconds...")
        await asyncio.sleep(CHECK_INTERVAL)


# ─── Telegram-команды ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📋 Текущие слоты", callback_data="slots")],
        [
            InlineKeyboardButton("🔔 Подписаться",  callback_data="subscribe"),
            InlineKeyboardButton("🔕 Отписаться",   callback_data="unsubscribe"),
        ],
    ]
    await update.message.reply_text(
        "👋 <b>VFS Global Monitor Bot</b>\n\n"
        "Слежу за доступными окнами записи в VFS Global:\n"
        "🇩🇪 Германия · 🇪🇸 Испания · 🇫🇷 Франция\n"
        "📍 Москва и Санкт-Петербург\n\n"
        "📢 Обновления в канале каждые 15 мин\n"
        "🔔 Личные уведомления — нажми «Подписаться»",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю последние данные...")
    if last_known:
        text = format_slots_message(last_known)
        await msg.edit_text(
            text or "Нет данных.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    else:
        await msg.edit_text("⏳ Данные ещё не загружены. Попробуй через минуту.")


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers.add(update.effective_chat.id)
    save_state()
    await update.message.reply_text("✅ Подписка оформлена! Пришлю уведомление при появлении мест.")


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers.discard(update.effective_chat.id)
    save_state()
    await update.message.reply_text("🔕 Вы отписаны от уведомлений.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = sum(1 for v in last_known.values() if v.get("dates") and len(v["dates"]) > 0)
    await update.message.reply_text(
        f"📊 <b>Статус бота</b>\n\n"
        f"• Направлений: {len(TARGETS)}\n"
        f"• Открытых слотов: {total}\n"
        f"• Подписчиков: {len(subscribers)}\n"
        f"• Интервал: {CHECK_INTERVAL // 60} мин\n"
        f"• Время: {datetime.now().strftime('%d.%m.%Y %H:%M')} МСК",
        parse_mode=ParseMode.HTML,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "slots":
        if last_known:
            text = format_slots_message(last_known)
            await query.edit_message_text(
                text or "Нет данных.",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        else:
            await query.edit_message_text("⏳ Данные ещё не загружены.")
    elif query.data == "subscribe":
        subscribers.add(query.message.chat_id)
        save_state()
        await query.edit_message_text("✅ Подписка оформлена!")
    elif query.data == "unsubscribe":
        subscribers.discard(query.message.chat_id)
        save_state()
        await query.edit_message_text("🔕 Вы отписаны от уведомлений.")


# ─── Точка входа ──────────────────────────────────────────────────────────────
async def main():
    load_state()
    logger.info(f"Bot token: {BOT_TOKEN[:20]}...")
    logger.info(f"Channel ID: {CHANNEL_ID}")
    logger.info(f"Targets: {len(TARGETS)}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("slots",       cmd_slots))
    app.add_handler(CommandHandler("subscribe",   cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CallbackQueryHandler(button_handler))

    async with app:
        await app.start()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        logger.info("✅ Bot started! Beginning monitor loop...")
        await monitor_loop(app.bot)


if __name__ == "__main__":
    import sys
    import time
    max_retries = 10
    for attempt in range(max_retries):
        try:
            asyncio.run(main())
            break
        except Exception as e:
            if "Conflict" in str(e):
                wait = 30 * (attempt + 1)
                logger.warning(f"Conflict (attempt {attempt+1}/{max_retries}). Waiting {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"Fatal: {e}", exc_info=True)
                sys.exit(1)
