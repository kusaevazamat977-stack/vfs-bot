"""
ItalyVMS Slot Monitor Bot
Мониторит доступные окна записи на italyvms.com
Города: Москва и Санкт-Петербург
Типы виз: Turismo, Affari, Invito
"""

import asyncio
import logging
import os
import json
import random
from datetime import datetime
from typing import Optional

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
BOT_TOKEN      = os.getenv("BOT_TOKEN", "8623727460:AAGia4P5xYIPXqz5HR5ZyDTd6K5Qc8syvvs")
CHANNEL_ID     = os.getenv("CHANNEL_ID", "-1003947723186")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "900"))

# Что мониторим: (город_value, город_название, тип_визы_value, тип_визы_название)
TARGETS = [
    ("1",  "🏙 Москва",          "13", "🏖 Туризм"),
    ("Москва (Толмачевский)", "🏙 Москва",          "Affari",   "💼 Бизнес"),
    ("Москва (Толмачевский)", "🏙 Москва",          "Invito",   "✉️ Приглашение"),
    ("Санкт-Петербург",       "🏙 Санкт-Петербург", "Turismo",  "🏖 Туризм"),
    ("Санкт-Петербург",       "🏙 Санкт-Петербург", "Affari",   "💼 Бизнес"),
    ("Санкт-Петербург",       "🏙 Санкт-Петербург", "Invito",   "✉️ Приглашение"),
]

BASE_URL = "https://italyvms.com/autoform/?lang=ru"

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
async def check_slots(city: str, visa_type: str) -> Optional[list]:
    """
    Открывает italyvms.com, заполняет форму и извлекает доступные даты.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ru-RU",
        )
        page: Page = await context.new_page()

        try:
            logger.info(f"Checking {city} / {visa_type}...")
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(random.uniform(1, 2))

            # Выбираем город (name='center', value: 1=Москва, 11=СПб, 27=Архангельск)
            city_val = city
            await page.select_option("select[name='center']", value=city_val)
            await asyncio.sleep(1)

            # Выбираем тип визы (name='vtype')
            await page.select_option("select[name='vtype']", value=visa_type)
            await asyncio.sleep(0.5)

            # Вводим количество заявителей
            await page.fill("input[name='num_of_person']", "1")

            # Вводим email
            await page.fill("input[name='email']", "test@test.com")
            await page.fill("input[name='emailcheck']", "test@test.com")

            # Ставим галочки согласия
            for cb_name in ["pers_info", "mobil_info"]:
                cb = await page.query_selector(f"input[name='{cb_name}']")
                if cb:
                    checked = await cb.is_checked()
                    if not checked:
                        await cb.check()

            await asyncio.sleep(0.5)

            # Нажимаем Далее (input type='button')
            next_btn = await page.query_selector("input[type='button'], input[type='submit'], button[type='submit']")
            if next_btn:
                await next_btn.click()
                await asyncio.sleep(3)
            else:
                logger.warning(f"Next button not found for {city}/{visa_type}")
                return None

            # Ждём загрузки следующей страницы с датами
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            await asyncio.sleep(2)

            # Перехватываем API с датами
            dates = []

            # Ищем даты в DOM — календарь или список
            date_elements = await page.query_selector_all(
                "td.available, td[class*='available'], "
                ".slot-date, [data-date], "
                "td:not(.disabled):not(.unavailable) > a, "
                ".calendar td.active"
            )
            for el in date_elements:
                d = await el.get_attribute("data-date")
                if not d:
                    d = await el.inner_text()
                if d and d.strip():
                    dates.append(d.strip()[:10])

            # Ищем текст "нет мест" или "недоступно"
            body = await page.inner_text("body")
            if any(phrase in body.lower() for phrase in [
                "нет доступных", "no available", "недоступно",
                "нет свободных", "все занято", "мест нет"
            ]):
                logger.info(f"No slots for {city}/{visa_type}")
                return []

            if dates:
                logger.info(f"Found {len(dates)} dates for {city}/{visa_type}")
                return sorted(set(dates))

            # Если дат нет но и ошибки нет — неизвестно
            logger.info(f"Unknown state for {city}/{visa_type}")
            return None

        except Exception as e:
            logger.warning(f"Error checking {city}/{visa_type}: {e}")
            return None
        finally:
            await browser.close()


async def check_all_slots() -> dict:
    results = {}
    for city, city_name, visa_type, visa_name in TARGETS:
        key = f"{city}_{visa_type}".replace(" ", "_").replace("(", "").replace(")", "")
        dates = await check_slots(city, visa_type)
        results[key] = {
            "city": city,
            "city_name": city_name,
            "visa_type": visa_type,
            "visa_name": visa_name,
            "dates": dates,
        }
        await asyncio.sleep(random.uniform(3, 6))
    return results


# ─── Форматирование сообщений ─────────────────────────────────────────────────
def format_message(results: dict, changed_only: bool = False) -> Optional[str]:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"🇮🇹 <b>ItalyVMS — доступные окна записи</b>\n<i>Обновлено: {now} МСК</i>\n"]
    has_content = False

    for key, info in results.items():
        dates = info.get("dates")
        city_name = info["city_name"]
        visa_name = info["visa_name"]

        if changed_only:
            prev = last_known.get(key, {}).get("dates")
            if prev == dates:
                continue

        if dates is None:
            status = "⚠️ <i>не удалось проверить</i>"
        elif len(dates) == 0:
            if not changed_only:
                status = "🔴 Нет свободных мест"
            else:
                continue
        else:
            dates_str = " · ".join(dates[:8])
            if len(dates) > 8:
                dates_str += f" (+{len(dates)-8})"
            status = f"🟢 <b>{len(dates)} дат:</b> {dates_str}"

        lines.append(f"<b>{city_name} — {visa_name}</b>\n{status}\n")
        has_content = True

    if not has_content:
        return None

    lines.append('<a href="https://italyvms.com">🔗 Записаться на italyvms.com</a>')
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
                msg = format_message(changed, changed_only=True)
                if msg:
                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=msg,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                    logger.info(f"Posted update to channel")

            if new_slots and subscribers:
                msg = format_message(new_slots)
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


# ─── Telegram команды ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📋 Текущие слоты", callback_data="slots")],
        [
            InlineKeyboardButton("🔔 Подписаться",  callback_data="subscribe"),
            InlineKeyboardButton("🔕 Отписаться",   callback_data="unsubscribe"),
        ],
    ]
    await update.message.reply_text(
        "👋 <b>ItalyVMS Monitor Bot</b>\n\n"
        "Слежу за доступными окнами записи в визовый центр Италии:\n"
        "🏙 Москва и Санкт-Петербург\n"
        "🏖 Туризм · 💼 Бизнес · ✉️ Приглашение\n\n"
        "📢 Обновления в канале каждые 15 мин\n"
        "🔔 Личные уведомления — нажми «Подписаться»",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю данные...")
    if last_known:
        text = format_message(last_known)
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
            text = format_message(last_known)
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
        await query.edit_message_text("🔕 Вы отписаны.")


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
    import sys, time
    for attempt in range(10):
        try:
            asyncio.run(main())
            break
        except Exception as e:
            if "Conflict" in str(e):
                wait = 30 * (attempt + 1)
                logger.warning(f"Conflict (attempt {attempt+1}). Waiting {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"Fatal: {e}", exc_info=True)
                sys.exit(1)
