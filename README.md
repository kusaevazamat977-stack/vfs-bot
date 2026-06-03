# VFS Global Slot Monitor Bot 🤖

Telegram-бот для мониторинга доступных окон записи в визовых центрах **VFS Global** (Москва и Санкт-Петербург).

## Что умеет

- ✅ Проверяет слоты каждые 15 минут (настраивается)
- ✅ Публикует обновления в Telegram-канал
- ✅ Рассылает личные уведомления подписчикам при появлении новых мест
- ✅ Мониторит Германию, Францию, Италию, Испанию, Австрию, Нидерланды, Швецию, Финляндию, Чехию
- ✅ Работает в облаке (Railway, Heroku, VPS)

## Команды бота

| Команда | Описание |
|---|---|
| `/start` | Главное меню |
| `/slots` | Показать текущие доступные даты |
| `/subscribe` | Подписаться на личные уведомления |
| `/unsubscribe` | Отписаться |
| `/status` | Статистика бота |

## Пример сообщения в канале

```
🗓 VFS Global — доступные окна
Обновлено: 03.06.2026 15:30

🇩🇪 Германия — Москва
🟢 4 дат: 2026-06-10 · 2026-06-15 · 2026-06-20 · 2026-06-25

🇫🇷 Франция — Санкт-Петербург
🟢 2 дат: 2026-06-12 · 2026-06-18

🔗 Открыть VFS Global
```

---

## Установка и запуск

### 1. Создай бота в Telegram

1. Напиши [@BotFather](https://t.me/BotFather)
2. Отправь `/newbot`, задай имя и username
3. Скопируй токен

### 2. Создай Telegram-канал

1. Создай канал (публичный или приватный)
2. Добавь бота в канал как **администратора** с правом публикации
3. Узнай ID канала:
   - Для публичного: `@username_канала`
   - Для приватного: перешли любое сообщение из канала боту `@userinfobot`

### 3. Настрой переменные окружения

Скопируй `.env.example` в `.env` и заполни:

```bash
cp .env.example .env
```

```env
BOT_TOKEN=1234567890:ABCdef...
CHANNEL_ID=-1001234567890
CHECK_INTERVAL=900
```

---

## Деплой на Railway (рекомендуется, бесплатно)

1. Зарегистрируйся на [railway.app](https://railway.app)
2. Создай новый проект → **Deploy from GitHub repo**
3. Подключи репозиторий с этим кодом
4. В разделе **Variables** добавь все переменные из `.env.example`
5. Railway автоматически подберёт Dockerfile и запустит бота

> 💡 Railway даёт $5/месяц бесплатно — хватит для постоянной работы бота.

---

## Деплой на Heroku

```bash
heroku create my-vfs-bot
heroku config:set BOT_TOKEN=... CHANNEL_ID=... CHECK_INTERVAL=900
heroku buildpacks:add heroku/python
git push heroku main
heroku ps:scale worker=1
```

---

## Деплой на VPS (Ubuntu)

```bash
# 1. Установи зависимости
pip install -r requirements.txt
playwright install chromium --with-deps

# 2. Создай .env файл

# 3. Запусти через systemd или screen
screen -S vfsbot
python bot.py
```

---

## Структура проекта

```
vfs_bot/
├── bot.py              # Основной код
├── requirements.txt    # Python зависимости
├── Dockerfile          # Для Railway/Docker
├── railway.toml        # Конфиг Railway
├── Procfile            # Для Heroku
├── .env.example        # Шаблон переменных
├── .gitignore
└── README.md
```

## Важные замечания

- VFS Global периодически обновляет защиту (Cloudflare). Если бот перестал получать данные — обнови `user_agent` в `bot.py`.
- Интервал 15 минут (`CHECK_INTERVAL=900`) — безопасный. Не ставь меньше 5 минут.
- Не используй для автоматического бронирования (нарушение ToS VFS).
