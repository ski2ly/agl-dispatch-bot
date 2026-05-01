# 🚀 Инструкция по деплою AGL Dispatch Bot (Ubuntu)

## 1. Первоначальная установка

```bash
# Обновить пакеты
apt-get update && apt-get upgrade -y

# Установить Docker и Docker Compose
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin

# Клонировать репозиторий
git clone https://github.com/ski2ly/agl-dispatch-bot /opt/agl-bot
cd /opt/agl-bot

# Подготовить конфиг
cp .env.example .env
nano .env # Заполнить BOT_TOKEN, DB_PASSWORD, LOGIN_KEY_PEPPER и т.д.

# Сгенерировать pepper для хеширования login_key (>= 32 символа):
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# Полученное значение — в .env как LOGIN_KEY_PEPPER.
# ВАЖНО: смена pepper инвалидирует все существующие ключи доступа.

# Создать папки для данных
mkdir -p data backups

# Запустить систему
docker compose up -d --build
```

## 1.1 Проверка после деплоя

```bash
# Проверить health-endpoint
curl -fsS http://127.0.0.1:8000/health
# {"status":"ok"}

# Контейнер должен быть в статусе "healthy"
docker compose ps
```

## 2. Обновление бота

```bash
cd /opt/agl-bot
git pull
docker compose up -d --build
```

## 3. Полезные команды

```bash
# Просмотр логов
docker compose logs -f bot

# Бэкап базы данных
docker compose exec db pg_dump -U agl_user agl_dispatch | gzip > ./backups/agl_$(date +%Y%m%d).sql.gz

# Проверка статуса
docker compose ps

# Запуск тестов локально (нужен pip install -r requirements-dev.txt)
LOGIN_KEY_PEPPER=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))") \
  python3 -m pytest tests/ -v
```

## 4. Настройка Nginx + SSL (Обязательно для Mini App)

Mini App работает только через HTTPS. Рекомендуется использовать Nginx как реверс-прокси:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 5. Безопасность — что важно знать

- `LOGIN_KEY_PEPPER` — обязательная переменная. Без неё бот не запустится при попытке логина.
- `WEBAPP_URL` обязательно указывайте **полностью** (с https://). Это контролирует CORS — без него все cross-origin запросы будут отклонены.
- `SUPERUSER_IDS` — единственный способ дать кому-то роль superuser. Через UI/API сделать superuser нельзя.
- При компрометации pepper'а: сменить → перезапустить → раздать новые ключи всем сотрудникам через UI.
- Login keys больше не хранятся в открытом виде. После создания/ротации ключ показывается **один раз** через alert. Запись на бумажку обязательна.
