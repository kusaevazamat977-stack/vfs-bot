"""
VFS Global Slot Monitor Bot
Мониторит доступные окна записи на VFS Global (Москва и СПб)
и публикует в Telegram-канал + рассылает подписчикам.
"""

import asyncio
import logging
import os
import json
import random
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

# ─── Логгер ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Конфигурация ─────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN", "8623727460:AAGia4P5xYIPXqz5HR5ZyDTd6K5Qc8syvvs")
CHANNEL_ID     = os.getenv("CHANNEL_ID", "-1003947723186")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "900"))  # 15 минут

# Список стран и городов для мониторинга
TARGETS = [
    ("deu", "🇩🇪 Германия",    "moscow",           "Москва"),
    ("deu", "🇩🇪 Германия",    "saint-petersburg", "Санкт-Петербург"),
    ("fra", "🇫🇷 Франция",     "moscow",           "Москва"),
    ("fra", "🇫🇷 Франция",     "saint-petersburg", "Санкт-Петербург"),
    ("ita", "🇮🇹 Италия",      "moscow",           "Москва"),
    ("ita", "🇮🇹 Италия",      "saint-petersburg", "Санкт-Петербург"),
    ("esp", "🇪🇸 Испания",     "moscow",           "Москва"),
    ("esp", "🇪🇸 Испания",     "saint-petersburg", "Санкт-Петербург"),
    ("aut", "🇦🇹 Австрия",     "moscow",           "Москва"),
    ("nld", "🇳🇱 Нидерланды",  "moscow",           "Москва"),
    ("nld", "🇳🇱 Нидерланды",  "saint-petersburg", "Санкт-Петербург"),
    ("swe", "🇸🇪 Швеция",      "moscow",           "Москва"),
    ("fin", "🇫🇮 Финляндия",   "moscow",           "Москва"),
    ("fin", "🇫🇮 Финляндия",   "saint-petersburg", "Санкт-Петербург"),
    ("cze", "🇨🇿 Чехия",       "moscow",           "Москва"),
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


# ─── Playwright: проверка слотов ──────────────────────────────────────────────
async def check_slots_for_target(
    context: BrowserContext,
    country_code: str,
    city_slug: str,
) -> Optional[list]:
    url = f"https://visa.vfsglobal.com/rus/en/{country_code}/book-an-appointment"
    page: Page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(random.uniform(2, 4))

        api_dates = []

        async def handle_response(response):
            if "appointment" in response.url.lower() and response.status == 200:
                try:
                    data = await response.json()
                    if isinstance(data, list):
                        for item in data:
                            d = item.get("appointmentDate") or item.get("date") or item.get("slotDate")
                            if d:
                                api_dates.append(str(d)[:10])
                    elif isinstance(data, dict):
                        dates = data.get("availableDates") or data.get("dates") or []
                        for d in dates:
                            api_dates.append(str(d)[:10])
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            city_selects = await page.query_selector_all("select")
            for sel in city_selects:
                options = await sel.inner_html()
                if city_slug.replace("-", " ").lower() in options.lower():
                    await sel.select_option(label=city_slug.replace("-", " ").title())
                    await asyncio.sleep(2)
                    break

            for btn_text in ["Book an Appointment", "Check Availability", "Book", "Continue"]:
                btn = await page.query_selector(f"button:has-text('{btn_text}')")
                if btn:
                    await btn.click()
                    await asyncio.sleep(3)
                    break
        except Exception as e:
            logger.debug(f"UI interaction error for {country_code}/{city_slug}: {e}")

        await asyncio.sleep(4)

        if api_dates:
            return sorted(set(api_dates))

        date_cells = await page.query_selector_all(
            "td.available, td[aria-disabled='false'], .slot-date, "
            "[class*='available'], [data-date]"
        )
        slots_found = []
        for cell in date_cells:
            d = await cell.get_attribute("data-date")
            if not d:
                d = await cell.inner_text()
            if d and len(d) >= 6:
                slots_found.append(d.strip()[:10])

        if slots_found:
            return sorted(set(slots_found))

        body_text = await page.inner_text("body")
        if any(phrase in body_text.lower() for phrase in
               ["no appointment", "no slot", "нет мест", "недоступно", "unavailable"]):
            return []

        return None

    except Exception as e:
        logger.warning(f"Error checking {country_code}/{city_slug}: {e}")
        return None
    finally:
        await page.close()


async def check_all_slots() -> dict:
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8"},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        for country_code, country_name, city_slug, city_name in TARGETS:
            key = f"{country_code}_{city_slug}"
            logger.info(f"Checking {country_name} / {city_name}...")
            dates = await check_slots_for_target(context, country_code, city_slug)
            results[key] = {
                "country_code": country_code,
                "country_name": country_name,
                "city_name": city_name,
                "dates": dates,
            }
            await asyncio.sleep(random.uniform(3, 7))

        await browser.close()
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
            status = "⚠️ <i>сайт недоступен</i>"
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
                    logger.info(f"Posted update to channel for {len(changed)} targets")

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
        "Слежу за доступными окнами записи в визовых центрах VFS Global "
        "в <b>Москве</b> и <b>Санкт-Петербурге</b> для всех стран.\n\n"
        "📢 Обновления публикуются в канале каждые 15 мин\n"
        "🔔 Личные уведомления — нажми «Подписаться»",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю последние данные...")
    if last_known:
        text = format_slots_message(last_known)
        await msg.edit_text(
            text or "Данных пока нет.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    else:
        await msg.edit_text("⏳ Данные ещё не загружены. Попробуй через минуту.")


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers.add(chat_id)
    save_state()
    await update.message.reply_text(
        "✅ Подписка оформлена!\n"
        "Пришлю уведомление как только появятся свободные окна."
    )


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers.discard(update.effective_chat.id)
    save_state()
    await update.message.reply_text("🔕 Вы отписаны от уведомлений.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = sum(1 for v in last_known.values() if v.get("dates") and len(v["dates"]) > 0)
    await update.message.reply_text(
        f"📊 <b>Статус бота</b>\n\n"
        f"• Направлений под мониторингом: {len(TARGETS)}\n"
        f"• Открытых слотов сейчас: {total}\n"
        f"• Подписчиков: {len(subscribers)}\n"
        f"• Интервал проверки: {CHECK_INTERVAL // 60} мин\n"
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
        await query.edit_message_text("✅ Подписка оформлена! Пришлю уведомление при появлении мест.")
    elif query.data == "unsubscribe":
        subscribers.discard(query.message.chat_id)
        save_state()
        await query.edit_message_text("🔕 Вы отписаны от уведомлений.")


# ─── Точка входа ──────────────────────────────────────────────────────────────
async def main():
    load_state()
    logger.info(f"Bot token: {BOT_TOKEN[:20]}...")
    logger.info(f"Channel ID: {CHANNEL_ID}")
    logger.info(f"Check interval: {CHECK_INTERVAL}s")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("slots",       cmd_slots))
    app.add_handler(CommandHandler("subscribe",   cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CallbackQueryHandler(button_handler))

    async with app:
        await app.start()
        # drop_pending_updates + allowed_updates чтобы сбросить старые сессии
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        logger.info("✅ Bot started! Beginning monitor loop...")
        await monitor_loop(app.bot)


if __name__ == "__main__":
    import sys
    max_retries = 5
    for attempt in range(max_retries):
        try:
            asyncio.run(main())
            break
        except Exception as e:
            if "Conflict" in str(e):
                wait = 30 * (attempt + 1)
                logger.warning(f"Conflict error (attempt {attempt+1}/{max_retries}). Waiting {wait}s...")
                import time; time.sleep(wait)
            else:
                logger.error(f"Fatal error: {e}")
                sys.exit(1)
    else:
        logger.error("Max retries reached. Exiting.")
        sys.exit(1)
