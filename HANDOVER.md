# 📦 AGL Dispatch Bot — инструкция для сисадмина

Этот архив — production-ready версия Telegram-бота AGL Dispatch для логистических компаний AGL и BALTCRAFT.

## ⚡ Быстрый запуск (Docker, 5 минут)

### 1. Распаковать

```bash
mkdir -p /opt/agl-bot && cd /opt/agl-bot
tar -xzf /path/to/AGLDispatchBot-production-ready.tar.gz --strip-components=1
mkdir -p data backups
# Контейнер запускается под non-root юзером с UID 1000.
# Каталоги для голосовых и бэкапов должны быть писабельны для него:
chown -R 1000:1000 data backups
```

### 2. Заполнить `.env`

Файл `.env` уже содержит сгенерированные секреты (`LOGIN_KEY_PEPPER`, `DB_PASSWORD`) — **их трогать не надо**.

Откройте `.env` и заполните **семь** значений, помеченных `PASTE_*_HERE`:

| Переменная | Где взять |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` или `/token` |
| `CHANNEL_ID` | Telegram канал для постинга заявок. Узнать ID: добавить [@userinfobot](https://t.me/userinfobot) в канал, переслать сообщение из канала |
| `DISCUSSION_GROUP_ID` | Группа-обсуждение, привязанная к каналу (если нет — оставить пустым) |
| `WEBAPP_URL` | Полный URL (с https://), на котором будет хоститься MiniApp. Например `https://agl-bot.example.com` |
| `SUPERUSER_IDS` | Telegram ID администратора компании (узнать через [@userinfobot](https://t.me/userinfobot)) |
| `OPENAI_API_KEY` | Создать на https://platform.openai.com/api-keys |
| `GOOGLE_SHEETS_CREDENTIALS` (опц.) | JSON service-account на одной строке. Если Sheets не нужны — оставить пустым |

### 3. Поднять контейнеры

```bash
docker compose up -d --build
```

PostgreSQL запустится первым, потом бот — миграции БД проедут автоматически.

### 4. Проверка

```bash
# Health-endpoint должен ответить {"status":"ok"}
curl -fsS http://127.0.0.1:8000/health

# Контейнеры должны быть в статусе Up (healthy)
docker compose ps

# Логи (Ctrl+C для выхода)
docker compose logs -f bot
```

В логах должно появиться:
```
✅ PostgreSQL connected via asyncpg
✅ Database schema verified and initialized.
🤖 Bot starting...
🚀 System initialized: DB, Cron, and API Server are live on port 8000.
```

### 5. Nginx + HTTPS (обязательно для MiniApp)

Telegram MiniApp работает **только через HTTPS**. Если у вас уже есть TLS-сертификат на нужный домен — положите такой `nginx.conf`:

```nginx
server {
    listen 80;
    server_name agl-bot.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name agl-bot.example.com;

    ssl_certificate     /etc/letsencrypt/live/agl-bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/agl-bot.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Если HTTPS-сертификата нет — установить через Let's Encrypt:
```bash
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d agl-bot.example.com
```

После этого в `.env` установить `WEBAPP_URL=https://agl-bot.example.com` и перезапустить:
```bash
docker compose restart bot
```

### 6. Настроить MiniApp в @BotFather

```
/mybots → выбрать бота → Bot Settings → Menu Button → Configure Menu Button
URL: https://agl-bot.example.com
Text: Открыть базу
```

## 🔑 Раздача доступов сотрудникам

Список сотрудников зашит в коде (`database.py::init_staff`) — 17 человек. После первого запуска у каждого автоматически создаётся аккаунт со старым ключом доступа (формат `AGL_XX`).

**Рекомендуется сразу ротировать все ключи через UI:**

1. Суперпользователь (тот, чей `telegram_id` в `SUPERUSER_IDS`) открывает MiniApp.
2. Вкладка «Управление» → «Сотрудники» → каждому жмёт «Редактировать» → «🔑 Сгенерировать новый ключ».
3. Новый ключ показывается **один раз** в alert. Записать на бумажку, передать сотруднику.
4. Сотрудник пишет боту в личные сообщения свой ключ → авторизация.

## 💾 Бэкапы БД

```bash
# Ручной бэкап
docker compose exec db pg_dump -U agl_user agl_dispatch | gzip > /opt/agl-bot/backups/agl_$(date +%Y%m%d).sql.gz

# Автоматизация — добавить в crontab (раз в сутки в 3:00 ночи):
echo '0 3 * * * cd /opt/agl-bot && docker compose exec -T db pg_dump -U agl_user agl_dispatch | gzip > /opt/agl-bot/backups/agl_$(date +\%Y\%m\%d).sql.gz && find /opt/agl-bot/backups -name "*.sql.gz" -mtime +30 -delete' | crontab -
```

Восстановление:
```bash
gunzip -c /opt/agl-bot/backups/agl_20260501.sql.gz | docker compose exec -T db psql -U agl_user agl_dispatch
```

## 🚨 Что нельзя делать

- ❌ **Не менять `LOGIN_KEY_PEPPER` после запуска** — все ключи доступа сотрудников станут недействительными, придётся всем раздавать новые.
- ❌ **Не менять `DB_PASSWORD` без миграции БД** — контейнер не сможет подключиться к существующему volume.
- ❌ **Не коммитить `.env` в git** (он в `.gitignore`, но всё же).
- ❌ **Не выставлять порт 8000 в публичный доступ напрямую** (только через nginx + HTTPS).

## 🔍 Логи и отладка

```bash
# Все логи бота
docker compose logs -f bot

# Только ошибки
docker compose logs bot 2>&1 | grep -E "ERROR|CRITICAL"

# Статус контейнеров
docker compose ps

# Подключиться к БД
docker compose exec db psql -U agl_user -d agl_dispatch
```

## 🧪 Тесты (опционально)

Запустить локально перед деплоем:
```bash
pip install -r requirements-dev.txt
LOGIN_KEY_PEPPER=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))") python3 -m pytest tests/ -v
```
Должно быть `28 passed`.

## 📞 Структура проекта

- `main.py` — точка входа
- `bot.py` — alias для main.py
- `database.py` — слой PostgreSQL
- `api/server.py` — REST API для MiniApp на aiohttp
- `webapp/index.html` — MiniApp (vanilla JS + Tailwind)
- `handlers/` — Telegram-команды, AI, callbacks, cron
- `ai_assistant.py` — OpenAI GPT-4o-mini + Whisper
- `sheets.py` — синхронизация в Google Sheets (опционально)
- `tests/` — pytest, 28 тестов
- `Dockerfile`, `docker-compose.yml` — оркестрация

## 📋 Системные требования

- Linux (Ubuntu 20.04+ / Debian 11+)
- Docker + Docker Compose plugin
- 1 GB RAM минимум, 2 GB рекомендуется
- 5 GB диска (под PostgreSQL volume)
- Открытые порты: 80, 443 (для nginx)
