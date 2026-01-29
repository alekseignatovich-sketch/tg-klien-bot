import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import json
import re
import os
import base64
from io import StringIO
from dotenv import load_dotenv

load_dotenv()

# Конфигурация из переменных окружения (Railway / .env)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")          # например: "@yourchannel" или -1001234567890
SHEET_ID = os.getenv("SHEET_ID")              # ID таблицы Google Sheets
N_DAYS_PLANNED = int(os.getenv("N_DAYS_PLANNED", "5"))
M_DAYS_STORAGE = int(os.getenv("M_DAYS_STORAGE", "14"))
P_DAYS_NEW_PLANNED = int(os.getenv("P_DAYS_NEW_PLANNED", "3"))

# Google credentials из base64 (безопаснее, чем загружать файл)
GOOGLE_CREDENTIALS_BASE64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")
if not GOOGLE_CREDENTIALS_BASE64:
    raise ValueError("GOOGLE_CREDENTIALS_BASE64 не задан в переменных окружения")

creds_json_str = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode("utf-8")
creds_dict = json.loads(creds_json_str)

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# Загружаем шаблоны сообщений
try:
    with open("templates.json", encoding="utf-8") as f:
        TEMPLATES = json.load(f)
except FileNotFoundError:
    print("templates.json не найден → используем дефолтные значения")
    TEMPLATES = {
        "ready_1": "Ваш заказ №{order} **готов** к выдаче!",
        "ready_2": "Заберите заказ в филиале **{branch}**.\nСрок хранения до {storage_date}.",
        "not_yet": "Заказ №{order} ещё в работе.\nПлановая дата готовности: **{planned_date}**.",
        "delayed": "Заказ №{order} задерживается.\nНовая плановая дата готовности: **{new_planned_date}**.\n\nПриносим извинения за неудобства."
    }

bot = telebot.TeleBot(BOT_TOKEN)

# Состояния пользователей
user_data = {}  # {user_id: {"state": str, "order": str, "branch": str}}

BRANCHES = ["Филиал 1", "Филиал 2", "Филиал 3"]
BRANCH_SHEET_NAMES = ["Филиал1", "Филиал2", "Филиал3"]  # ← подставьте реальные названия вкладок!


@bot.message_handler(commands=["start"])
def cmd_start(message):
    user_id = message.from_user.id
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status not in ["member", "administrator", "creator"]:
    bot.reply_to(
        message,
        f"Сначала подпишитесь на канал **{CHANNEL_NAME}**:\n{CHANNEL_LINK}\n\nПосле подписки напишите /start снова.",
        parse_mode="Markdown"
    )
    return
    except Exception as e:
        bot.reply_to(message, "Не удалось проверить подписку. Попробуйте позже.")
        print("Ошибка проверки подписки:", e)
        return

    bot.reply_to(
        message,
        "Введите номер заказа (ровно 8 цифр):",
        reply_markup=ReplyKeyboardRemove()
    )
    user_data[user_id] = {"state": "wait_order"}


@bot.message_handler(func=lambda m: True)
def handle_all(message):
    uid = message.from_user.id
    text = message.text.strip()

    if uid not in user_data:
        cmd_start(message)
        return

    state = user_data[uid].get("state")

    if state == "wait_order":
        if not re.fullmatch(r"\d{8}", text):
            bot.reply_to(message, "Номер заказа должен состоять ровно из 8 цифр.")
            return

        user_data[uid]["order"] = text
        keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        for b in BRANCHES:
            keyboard.add(KeyboardButton(b))
        bot.reply_to(message, "Выберите филиал:", reply_markup=keyboard)
        user_data[uid]["state"] = "wait_branch"

    elif state == "wait_branch":
        if text not in BRANCHES:
            bot.reply_to(message, "Пожалуйста, выберите филиал из предложенных кнопок.")
            return

        user_data[uid]["branch"] = text
        process_order(uid, message.chat.id)

    elif state == "wait_manager":
        if "менеджер" in text.lower() or "связ" in text.lower():
            bot.reply_to(message, "Менеджер: @ваш_менеджер_username\nили напишите в чат поддержки.")
        bot.reply_to(message, "Если нужна ещё помощь — пишите номер заказа заново.")
        user_data.pop(uid, None)

    else:
        cmd_start(message)


def process_order(user_id, chat_id):
    d = user_data.get(user_id, {})
    order_num = d.get("order")
    branch_text = d.get("branch")

    if not order_num or not branch_text:
        bot.send_message(chat_id, "Ошибка состояния. Начните заново /start")
        user_data.pop(user_id, None)
        return

    # Определяем имя вкладки
    try:
        branch_idx = BRANCHES.index(branch_text)
        sheet_name = BRANCH_SHEET_NAMES[branch_idx]
    except ValueError:
        bot.send_message(chat_id, "Неизвестный филиал.")
        user_data.pop(user_id, None)
        return

    try:
        spreadsheet = client.open_by_key(SHEET_ID)
        worksheet = spreadsheet.worksheet(sheet_name)
        rows = worksheet.get_all_values()
    except Exception as e:
        bot.send_message(chat_id, "Не удалось подключиться к таблице. Попробуйте позже.")
        print("Ошибка Google Sheets:", e)
        user_data.pop(user_id, None)
        return

    found = False
    for row in rows:
        if len(row) < 4:
            continue
        if row[1].strip() == order_num:  # столбец B (индекс 1)
            found = True
            date_str = row[0].strip()           # A — дата
            total = int(row[2] or 0)            # C — всего
            ready = int(row[3] or 0)            # D — готово
            break

    if not found:
        bot.send_message(chat_id, f"Заказ №{order_num} не найден в филиале «{branch_text}».")
        user_data.pop(user_id, None)
        return

    try:
        order_date = datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
    except:
        bot.send_message(chat_id, "Неверный формат даты в таблице.")
        user_data.pop(user_id, None)
        return

    today = datetime.date.today()

    if ready >= total and total > 0:
        storage_until = order_date + datetime.timedelta(days=M_DAYS_STORAGE)
        msg1 = TEMPLATES["ready_1"].format(order=order_num)
        msg2 = TEMPLATES["ready_2"].format(
            branch=branch_text,
            storage_date=storage_until.strftime("%d.%m.%Y")
        )
        bot.send_message(chat_id, msg1, parse_mode="Markdown")
        bot.send_message(chat_id, msg2, parse_mode="Markdown")
    else:
        planned = order_date + datetime.timedelta(days=N_DAYS_PLANNED)
        if today < planned:
            msg = TEMPLATES["not_yet"].format(
                order=order_num,
                planned_date=planned.strftime("%d.%m.%Y")
            )
            bot.send_message(chat_id, msg, parse_mode="Markdown")
        else:
            new_planned = today + datetime.timedelta(days=P_DAYS_NEW_PLANNED)
            msg = TEMPLATES["delayed"].format(
                order=order_num,
                new_planned_date=new_planned.strftime("%d.%m.%Y")
            )
            bot.send_message(chat_id, msg, parse_mode="Markdown")

            keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            keyboard.add(KeyboardButton("Связаться с менеджером"))
            bot.send_message(chat_id, "Хотите связаться с менеджером?", reply_markup=keyboard)
            user_data[user_id]["state"] = "wait_manager"
            return  # не удаляем состояние

    user_data.pop(user_id, None)


if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling(timeout=45, long_polling_timeout=30)
