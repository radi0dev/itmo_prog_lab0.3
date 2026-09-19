#!/usr/bin/env python3

# НУЖЕН Python 3.14+ !!!

import json
import os
import asyncio
from datetime import date, time as dt_time, timezone
from zoneinfo import ZoneInfo

import requests
from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# пути к файлам

CONFIG_FILE = "token.txt"
USERS_FILE = "users.json"

# текстовые ответы

INVALID_TIME_TEXT = "Неверное время. Формат должен быть HH:MM (например 05:00), время указывается в UTC.\nПопробуйте ещё раз."
INVALID_POS_TEXT = "Неверные координаты. Формат: широта,долгота (например 59.93,30.36).\nШирота от -90 до 90, долгота от -180 до 180. Попробуйте ещё раз."
INVALID_TZ_TEXT = "Неверный часовой пояс. Пришлите его в формате IANA, например\nEurope/Moscow, Europe/Kaliningrad, Asia/Yekaterinburg. Попробуйте ещё раз."

# словарь c (chat_id : что сейчас ждём от пользователя)
# нужен только когда команда вызвана без аргумента (например просто /settime)
# значение: "time" / "pos" / "tz"

waiting_state = {}

# список команд для чата

COMMANDS = [
    BotCommand("start", "Начать работу с ботом"),
    BotCommand("settime", "Установить время рассылки в UTC, напр. /settime 08:00"),
    BotCommand("setpos", "Установить координаты, напр. /setpos 59.93,30.36"),
    BotCommand("settz", "Установить часовой пояс, напр. /settz Europe/Moscow"),
    BotCommand("weather", "Прислать прогноз погоды сейчас"),
    BotCommand("stop", "Отключить ежедневную рассылку"),
    BotCommand("help", "Показать список команд"),
]

# коды погоды (Open-Meteo)

WEATHER_MAP = {
    0: "ясно",
    1: "в основном ясно",
    2: "переменная облачность",
    3: "облачно",
    45: "туман",
    48: "изморозь",
    51: "морось слабая",
    53: "морось умеренная",
    55: "морось сильная",
    61: "дождь слабый",
    63: "дождь умеренный",
    65: "дождь сильный",
    71: "снег слабый",
    73: "снег умеренный",
    75: "снег сильный",
    80: "ливень слабый",
    81: "ливень умеренный",
    82: "ливень сильный",
    95: "гроза",
    96: "гроза с градом",
    99: "гроза с сильным градом",
}


def weather_text(code):
    if code in WEATHER_MAP:
        return WEATHER_MAP[code]
    return "код " + str(code)


# чтение config.txt (TOKEN=...)

def read_config():
    config = {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line == "" or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
    return config


config = read_config()
TOKEN = config["TOKEN"]

# работа с users.json 
# структура одной записи:
# {"time": "08:00", "latitude": 59.93, "longitude": 30.36,
#  "timezone": "Europe/Moscow", "active": true}

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def get_user(chat_id):
    users = load_users()
    return users.get(str(chat_id))

def set_user_time(chat_id, time_str):
    users = load_users()
    key = str(chat_id)
    if key in users:
        users[key]["time"] = time_str
        users[key]["active"] = True
    else:
        users[key] = {"time": time_str, "active": True}
    save_users(users)

def set_user_position(chat_id, latitude, longitude):
    users = load_users()
    key = str(chat_id)
    if key in users:
        users[key]["latitude"] = latitude
        users[key]["longitude"] = longitude
    else:
        users[key] = {"latitude": latitude, "longitude": longitude, "active": False}
    save_users(users)

def set_user_timezone(chat_id, timezone_name):
    users = load_users()
    key = str(chat_id)
    if key in users:
        users[key]["timezone"] = timezone_name
    else:
        users[key] = {"timezone": timezone_name, "active": False}
    save_users(users)

def deactivate_user(chat_id):
    users = load_users()
    key = str(chat_id)
    if key in users and users[key].get("active"):
        users[key]["active"] = False
        save_users(users)
        return True
    return False

def get_active_users():
    users = load_users()
    result = {}
    for key in users:
        if users[key].get("active"):
            result[key] = users[key]
    return result

def has_position(user):
    return user is not None and "latitude" in user and "longitude" in user

def has_timezone(user):
    return user is not None and "timezone" in user

def has_time(user):
    return user is not None and "time" in user

# проверка корректности времени (HH:MM)

def check_time_format(text):
    parts = text.split(":")
    if len(parts) != 2:
        return None

    hour_text = parts[0]
    minute_text = parts[1]

    if not hour_text.isdigit() or not minute_text.isdigit():
        return None
    if len(hour_text) != 2 or len(minute_text) != 2:
        return None

    hour = int(hour_text)
    minute = int(minute_text)

    if hour < 0 or hour > 23:
        return None
    if minute < 0 or minute > 59:
        return None

    return hour, minute

# проверка корректности координат (широта,долгота)

def check_coordinates(text):
    parts = text.split(",")
    if len(parts) != 2:
        return None

    lat_text = parts[0].strip()
    lon_text = parts[1].strip()

    try:
        latitude = float(lat_text)
        longitude = float(lon_text)
    except ValueError:
        return None

    if latitude < -90 or latitude > 90:
        return None
    if longitude < -180 or longitude > 180:
        return None

    return latitude, longitude

# проверка корректности часового пояса

def check_timezone(text):
    try:
        ZoneInfo(text) # бросает исключение, если такого часового пояса нет в базе
    except Exception:
        return None
    return text

# получение погоды по api

def get_weather(day, latitude, longitude, timezone_name):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone_name,
        "hourly": "temperature_2m,wind_speed_10m,weather_code",
        "daily": "temperature_2m_mean",
        "start_date": day,
        "end_date": day,
    }

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    result = {
        "date": day,
        "average_temperature": None,
        "hourly_forecast": [],
    }

    daily = data.get("daily", {})
    daily_times = daily.get("time", [])
    daily_means = daily.get("temperature_2m_mean", [])
    if daily_times and daily_means:
        result["average_temperature"] = daily_means[0]

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    winds = hourly.get("wind_speed_10m", [])
    codes = hourly.get("weather_code", [])

    for t, temp, wind, code in zip(times, temps, winds, codes):
        result["hourly_forecast"].append({
            "time": t,
            "temperature_2m": temp,
            "wind_speed_10m": wind,
            "weather": weather_text(code),
        })

    return result

def format_table(data):
    header_line = "Погода на " + data["date"]

    avg = data.get("average_temperature")
    if avg is not None:
        avg_line = "Средняя температура: " + str(avg) + "°C"
    else:
        avg_line = "Средняя температура: нет данных"

    hourly = data.get("hourly_forecast", [])

    header = "{:<7}{:>8}{:>10}  Погода".format("Время", "Темп.", "Ветер")
    separator = "-" * len(header)

    rows = [header, separator]
    if not hourly:
        rows.append("нет данных")
    else:
        for item in hourly:
            time_s = item["time"][11:16]
            temp_s = str(item["temperature_2m"]) + "°C"
            wind_s = str(item["wind_speed_10m"]) + "км/ч"
            row = "{:<7}{:>8}{:>10}  {}".format(time_s, temp_s, wind_s, item["weather"])
            rows.append(row)

    table_text = "\n".join(rows)

    text = "<b>" + header_line + "</b>\n" + avg_line + "\n\n<pre>" + table_text + "</pre>"
    return text

def build_forecast_text(latitude, longitude, timezone_name):
    today = date.today().isoformat()
    try:
        data = get_weather(today, latitude, longitude, timezone_name)
        return format_table(data)
    except Exception as e:
        print("Ошибка получения прогноза погоды:", e)
        return "Не удалось получить прогноз погоды: " + str(e)

def missing_settings_text(user):
    missing = []
    if not has_time(user):
        missing.append("время рассылки (/settime)")
    if not has_position(user):
        missing.append("координаты (/setpos)")
    if not has_timezone(user):
        missing.append("часовой пояс (/settz)")
    return "Прогноз недоступен, не хватает настроек: " + ", ".join(missing)

def commands_text():
    lines = ["Доступные команды:"]
    for c in COMMANDS:
        lines.append("/" + c.command + " — " + c.description)
    return "\n".join(lines)

# планирование задач

def schedule_user(app, chat_id, time_str):
    hour, minute = check_time_format(time_str)
    name = str(chat_id)

    unschedule_user(app, chat_id) # снимаем старую задачу, чтобы не было дублей при повторном /settime

    app.job_queue.run_daily(
        send_daily_forecast,
        time=dt_time(hour=hour, minute=minute, tzinfo=timezone.utc), # время всегда в utc
        chat_id=chat_id,
        name=name,
    )

def unschedule_user(app, chat_id):
    if app.job_queue is None:
        return
    name = str(chat_id)
    jobs = app.job_queue.get_jobs_by_name(name)
    for job in jobs:
        job.schedule_removal()

async def send_daily_forecast(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    user = get_user(chat_id)

    # задача поставлена только если время было задано, но координаты и часовой
    # пояс могли остаться не настроеным - тогда шлем напоминание вместо прогноза
    if not has_position(user) or not has_timezone(user):
        try:
            await context.bot.send_message(chat_id=chat_id, text=missing_settings_text(user))
        except Exception as e:
            print("Не удалось отправить напоминание пользователю", chat_id, ":", e)
        return

    text = build_forecast_text(user["latitude"], user["longitude"], user["timezone"])
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML) # html нужен из-за <b> и <pre> в format_table
    except Exception as e:
        print("Не удалось отправить прогноз пользователю", chat_id, ":", e)


# обработчики команд

async def cmd_start(update, context):
    text = (
        commands_text()
        + "\n\nЧтобы получать прогноз (и по расписанию, и по /weather), "
        "настройте /settime, /setpos и /settz - без них бот ничего не пришлёт."
    )
    await update.message.reply_text(text)

async def cmd_settime(update, context):
    chat_id = update.effective_chat.id

    if context.args:
        text = context.args[0] # берём только первый аргумент, остальное игнорируем
        result = check_time_format(text)
        if result is None:
            waiting_state[chat_id] = "time"
            await update.message.reply_text(INVALID_TIME_TEXT)
            return

        set_user_time(chat_id, text)
        schedule_user(context.application, chat_id, text)
        waiting_state.pop(chat_id, None)
        await update.message.reply_text("Время рассылки установлено на " + text + " UTC.")
        return

    waiting_state[chat_id] = "time"
    await update.message.reply_text("Пришлите время в формате HH:MM (UTC), например 08:00")

async def cmd_setpos(update, context):
    chat_id = update.effective_chat.id

    if context.args:
        text = context.args[0]
        result = check_coordinates(text)
        if result is None:
            waiting_state[chat_id] = "pos"
            await update.message.reply_text(INVALID_POS_TEXT)
            return

        latitude, longitude = result
        set_user_position(chat_id, latitude, longitude)
        waiting_state.pop(chat_id, None)
        await update.message.reply_text(
            "Координаты установлены: " + str(latitude) + ", " + str(longitude)
        )
        return

    waiting_state[chat_id] = "pos"
    await update.message.reply_text(
        "Пришлите координаты в формате широта,долгота, например 59.93,30.36"
    )

async def cmd_settz(update, context):
    chat_id = update.effective_chat.id

    if context.args:
        text = context.args[0]
        result = check_timezone(text)
        if result is None:
            waiting_state[chat_id] = "tz"
            await update.message.reply_text(INVALID_TZ_TEXT)
            return

        set_user_timezone(chat_id, result)
        waiting_state.pop(chat_id, None)
        await update.message.reply_text("Часовой пояс установлен: " + result)
        return

    waiting_state[chat_id] = "tz"
    await update.message.reply_text(
        "Пришлите часовой пояс в формате IANA, например Europe/Moscow"
    )

async def handle_plain_text(update, context):
    chat_id = update.effective_chat.id
    state = waiting_state.get(chat_id) # None, если бот сейчас ничего не ждёт - тогда просто игнорируем сообщение

    if state is None:
        return

    text = update.message.text.strip()

    if state == "time":
        result = check_time_format(text)
        if result is None:
            await update.message.reply_text(INVALID_TIME_TEXT)
            return # состояние ожидания не сбрасываем, ждём ещё одну попытку

        set_user_time(chat_id, text)
        schedule_user(context.application, chat_id, text)
        waiting_state.pop(chat_id, None)
        await update.message.reply_text("Время рассылки установлено на " + text + " UTC.")
        return

    if state == "pos":
        result = check_coordinates(text)
        if result is None:
            await update.message.reply_text(INVALID_POS_TEXT)
            return

        latitude, longitude = result
        set_user_position(chat_id, latitude, longitude)
        waiting_state.pop(chat_id, None)
        await update.message.reply_text(
            "Координаты установлены: " + str(latitude) + ", " + str(longitude)
        )
        return

    if state == "tz":
        result = check_timezone(text)
        if result is None:
            await update.message.reply_text(INVALID_TZ_TEXT)
            return

        set_user_timezone(chat_id, result)
        waiting_state.pop(chat_id, None)
        await update.message.reply_text("Часовой пояс установлен: " + result)
        return

async def cmd_stop(update, context):
    chat_id = update.effective_chat.id
    waiting_state.pop(chat_id, None)
    unschedule_user(context.application, chat_id)

    if deactivate_user(chat_id):
        await update.message.reply_text(
            "Ежедневная рассылка отключена. Чтобы включить снова, пришлите /settime ЧЧ:ММ"
        )
    else:
        await update.message.reply_text("Рассылка и так не была включена.")

async def cmd_weather(update, context):
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    # по требованию: без времени, координат и часового пояса прогноз не шлём
    # даже по прямой команде, не только по расписанию
    if not has_time(user) or not has_position(user) or not has_timezone(user):
        await update.message.reply_text(missing_settings_text(user))
        return

    text = build_forecast_text(user["latitude"], user["longitude"], user["timezone"])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# запуск

async def post_init(app):
    if app.job_queue is None:
        print(
            "ОШИБКА: JobQueue недоступен, ежедневная рассылка работать не будет.\n"
            "Установите зависимости командой: pip install -r requirements.txt"
        )
    else:
        users = get_active_users()
        for chat_id_str in users:
            time_str = users[chat_id_str].get("time")
            if time_str:
                schedule_user(app, int(chat_id_str), time_str)
        print("Восстановлено расписаний:", len(users))

    await app.bot.set_my_commands(COMMANDS)

def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop()) 

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("settime", cmd_settime))
    app.add_handler(CommandHandler("setpos", cmd_setpos))
    app.add_handler(CommandHandler("settz", cmd_settz))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_plain_text))

    print("Бот запускается...")
    app.run_polling()


if __name__ == "__main__":
    main()
