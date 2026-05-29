import requests
import time
import os
import logging
from datetime import datetime
from math import floor
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import ReplyKeyboardMarkup, KeyboardButton
from threading import Thread

# ============================
# CONFIG
# ============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

user_data_store = {}
weather_cache = {}
wave_cache = {}
tide_cache = {}

CACHE_WEATHER_TTL = 600
CACHE_WAVE_TTL = 900
CACHE_TIDE_TTL = 1800

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
# METEO DECODE (ICONA + TESTO)
# ============================

def meteo_code(code):
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
    key = (round(lat,2), round(lon,2))
    now = time.time()

    if key in weather_cache:
        if now - weather_cache[key]["time"] < CACHE_WEATHER_TTL:
            return weather_cache[key]["data"]

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "hourly": "windspeed_10m,winddirection_10m,weathercode",
        "timezone": "auto"
    }

    res = requests.get(url, params=params).json()

    current = res["current_weather"]
    hourly = res["hourly"]

    forecast = []
    for i in range(12):
        forecast.append({
            "wind": hourly["windspeed_10m"][i],
            "dir": hourly["winddirection_10m"][i],
            "code": hourly["weathercode"][i]
        })

    result = {
        "current": current,
        "forecast": forecast
    }

    weather_cache[key] = {"time": now, "data": result}
    return result

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
# ONDE (mock semplice, non rimosso)
# ============================

def get_waves(lat, lon):
    return {
        "height": 0.8,
        "period": 5
    }

# ============================
# MAREE (mock semplice)
# ============================

def get_tide(lat, lon):
    return ["08:10 ↑", "14:20 ↓"]

def format_tides(t):
    return "\n".join(t) if t else "n.d."

# ============================
# ADRIATIC LOGIC
# ============================

def sea_state_index(w):
    if not w: return None
    return round(w["height"] / w["period"], 2)

def sea_state_label(i):
    if i is None: return "n.d."
    if i < 0.1: return "🟢 Lungo"
    elif i < 0.2: return "🟡 Buono"
    elif i < 0.3: return "🟠 Corto"
    else: return "🔴 Duro"

def vento_adriatico(wind, dir):
    if dir in ["NE","NNE","ENE"]:
        return "💨 Bora"
    if dir in ["SE","SSE","ESE"]:
        return "🌊 Scirocco"
    return "🌬 Locale"

# ============================
# FINESTRA USCITA
# ============================

def best_time_window(forecast):
    best = []
    for i, f in enumerate(forecast):
        if f["wind"] < 15:
            best.append(str(i))
    return ", ".join(best) if best else "n.d."

# ============================
# FORECAST
# ============================

def format_forecast(forecast):
    out = []
    base_hour = datetime.utcnow().hour

    for i, f in enumerate(forecast):
        hour = (base_hour + i) % 24
        icon, _ = meteo_code(f["code"])

        out.append(
            f"{hour:02d}:00 "
            f"{kmh_to_kn(f['wind'])}kn {direzione(f['dir'])} {icon}"
        )

    return "\n".join(out)

# ============================
# REPORT
# ============================

def genera_report(lat, lon):

    weather = get_weather(lat, lon)
    current = weather["current"]
    forecast = weather["forecast"]

    wave = get_waves(lat, lon)
    tides = get_tide(lat, lon)

    wind = current["windspeed"]
    dir_deg = current["winddirection"]
    code = current["weathercode"]

    icon, desc = meteo_code(code)
    dir_txt = direzione(dir_deg)

    idx = sea_state_index(wave)

    report = f"""
📅 {datetime.now().strftime('%d %B %Y')}

📍 METEO GENERALE: {icon} {desc}

🌬️ {kmh_to_kn(wind)} kn da {dir_txt}
📌 {vento_adriatico(wind, dir_txt)}

🌙 Fase lunare: {moon_phase()}

🌊 ONDE
{wave['height']} m / {wave['period']} s

📊 Mare: {sea_state_label(idx)}

🌊 MAREA
{format_tides(tides)}

✅ CONDIZIONI

🧭 USCITA CONSIGLIATA: {best_time_window(forecast)}

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
        "Invia posizione",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

def pos(update, context):
    chat_id = update.message.chat_id
    loc = update.message.location

    lat = loc.latitude
    lon = loc.longitude

    user_data_store[chat_id] = (lat, lon)

    update.message.reply_text("✅ Posizione salvata")
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

    # thread alert
    Thread(target=alert_loop, args=(updater.bot,), daemon=True).start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()