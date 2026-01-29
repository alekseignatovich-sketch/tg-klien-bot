# Telegram бот проверки готовности заказа

Работает с Google Sheets + проверка подписки на канал.

## Переменные окружения (Railway / .env)

- `BOT_TOKEN`
- `CHANNEL_ID`          (@channel или числовой ID)
- `SHEET_ID`
- `N_DAYS_PLANNED`
- `M_DAYS_STORAGE`
- `P_DAYS_NEW_PLANNED`
- `GOOGLE_CREDENTIALS_BASE64`  ← base64 от credentials.json

## Деплой

Railway → New Project → Deploy from GitHub repo → указать этот репозиторий.
