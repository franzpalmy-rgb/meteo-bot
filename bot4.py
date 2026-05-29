import requests
import time
import os
import logging
from datetime import datetime
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import ReplyKeyboardMarkup, KeyboardButton
from math import floor

# ============================
# CONFIG
# ============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

user_data_store = {}

logging.basicConfig(level=logging.INFO)

# ============================
# UTILS
# ============================

def kmh_to_kn(kmh):
    return round(kmh * 0.539957, 1)

def direzione(gradi):
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[int((gradi + 11.25)/22.5) % 16]

# ============================
# METEO ICONE + TESTO
# ============================

def meteo_decode(code):
    mapping = {
        0: ("☀️", "Sereno"),
        1: ("🌤", "Prevalentemente sereno"),
        2: ("⛅", "Poco nuvoloso"),
        3: ("☁️", "Nuvoloso"),
        45: ("🌫", "Nebbia"),
        51: ("🌦", "Pioggerella"),
        61: ("🌧", "Pioggia"),
        80: ("🌧", "Rovesci"),
        95: ("⛈", "Temporale")
    }
    return mapping.get(code, ("⛅", "Variabile"))

# ============================
# METEO (Open-Meteo)
# ============================

def get_weather(lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "hourly": "windspeed_10m,winddirection_10m,weathercode",
        "timezone": "auto"
    }
    r = requests.get(OPEN_METEO_URL, params=params)
    data = r.json()

    current = data["current_weather"]
    hourly = data["hourly"]

    forecast = []
    for i in range(12):
        forecast.append({
            "wind": hourly["windspeed_10m"][i],
            "dir": hourly["winddirection_10m"][i],
            "code": hourly["weathercode"][i]
        })

    return current, forecast

# ============================
# FASI LUNARI
# ============================

def moon_phase():
    now = datetime.utcnow()
    diff = now - datetime(2001, 1, 1)
    days = diff.days + (diff.seconds / 86400)
    lunations = 0.20439731 + (days / 29.53058867)
    phase = lunations % 1
    index = floor(phase * 8)

    phases = [
        "🌑 Luna nuova",
        "🌒 Falce crescente",
        "🌓 Primo quarto",
        "🌔 Gibbosa crescente",
        "🌕 Luna piena",
        "🌖 Gibbosa calante",
        "🌗 Ultimo quarto",
        "🌘 Falce calante"
    ]

    return phases[index]

# ============================
# FORECAST
# ============================

def format_forecast(forecast):
    out = []
    now_hour = datetime.utcnow().hour

    for i, f in enumerate(forecast):
        hour = (now_hour + i) % 24
        icon, _ = meteo_decode(f["code"])

        out.append(
            f"{hour:02d}:00 "
            f"{kmh_to_kn(f['wind'])}kn {direzione(f['dir'])} {icon}"
        )
    return "\n".join(out)

# ============================
# REPORT
# ============================

def genera_report(lat, lon):

    current, forecast = get_weather(lat, lon)

    wind = current["windspeed"]
    dir_deg = current["winddirection"]
    code = current["weathercode"]

    icon, desc = meteo_decode(code)
    dir_txt = direzione(dir_deg)

    report = f"""
📅 {datetime.now().strftime('%d %B %Y')}

📍 METEO GENERALE: {icon} {desc}

🌬️ Vento: {kmh_to_kn(wind)} kn da {dir_txt}

🌙 Fase lunare: {moon_phase()}

🕒 FORECAST 12H
{format_forecast(forecast)}
"""

    return report

# ============================
# TELEGRAM
# ============================

def start(update, context):
    kb = [[KeyboardButton("📍 Posizione", request_location=True)]]
    update.message.reply_text(
        "Invia la posizione",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

def pos(update, context):
    chat_id = update.message.chat_id
    loc = update.message.location

    lat = loc.latitude
    lon = loc.longitude

    user_data_store[chat_id] = (lat, lon)

    update.message.reply_text("Posizione salvata ✅")
    update.message.reply_text(genera_report(lat, lon))

def meteo(update, context):
    chat_id = update.message.chat_id

    if chat_id in user_data_store:
        lat, lon = user_data_store[chat_id]
        update.message.reply_text(genera_report(lat, lon))
    else:
        update.message.reply_text("Invia prima la posizione")

# ============================
# ALERT LOOP
# ============================

def alert_loop(bot):
    while True:
        try:
            for uid, (lat, lon) in user_data_store.items():
                bot.send_message(chat_id=uid, text=genera_report(lat, lon))
            time.sleep(3600)
        except:
            time.sleep(60)

# ============================
# MAIN
# ============================

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.location, pos))
    dp.add_handler(CommandHandler("meteo", meteo))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()