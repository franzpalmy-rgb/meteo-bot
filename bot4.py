import requests
import time
import os
import logging
from datetime import datetime, timedelta
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import ReplyKeyboardMarkup, KeyboardButton
from threading import Thread

# ============================
# CONFIG
# ============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
STORMGLASS_KEY = os.getenv("STORMGLASS_KEY")
WORLDTIDES_KEY = os.getenv("WORLDTIDES_KEY")

user_data_store = {}

weather_cache = {}
wave_cache = {}
tide_cache = {}

CACHE_WEATHER_TTL = 600
CACHE_WAVE_TTL = 900
CACHE_TIDE_TTL = 1800

# ============================
# LOG
# ============================

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
# METEO (Open-Meteo completo)
# ============================

def get_weather(lat, lon):
    key = (round(lat,2), round(lon,2))
    now = time.time()

    if key in weather_cache:
        val, ts = weather_cache[key]
        if now - ts < CACHE_WEATHER_TTL:
            return val

    try:
        url = (
            "https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
            "weather_code,cloud_cover,precipitation"
        )

        data = requests.get(url, timeout=10).json()["hourly"]

        forecast = []
        for i in range(12):
            forecast.append({
                "wind": data["wind_speed_10m"][i],
                "gust": data["wind_gusts_10m"][i],
                "dir": data["wind_direction_10m"][i],
                "code": data["weather_code"][i],
                "clouds": data["cloud_cover"][i],
                "rain": data["precipitation"][i]
            })

        weather_cache[key] = (forecast, now)
        return forecast

    except Exception as e:
        logging.error(f"Weather error: {e}")
        return None

# ============================
# METEO DECODE
# ============================

def meteo_code(code):
    mapping = {
        0: "☀️",
        1: "🌤",
        2: "⛅",
        3: "☁️",
        45: "🌫",
        51: "🌦",
        61: "🌧",
        80: "🌧",
        95: "⛈"
    }
    return mapping.get(code, "⛅")

# ============================
# ONDE (StormGlass multi-source)
# ============================

def extract_wave(hour):
    sources = ["noaa", "dwd", "sg"]

    def avg(param):
        vals = []
        for s in sources:
            v = hour.get(param, {}).get(s)
            if v is not None:
                vals.append(v)

        if not vals:
            return None

        vals.sort()
        if len(vals) >= 3:
            vals = vals[1:-1]

        return round(sum(vals)/len(vals), 2)

    return {
        "height": avg("waveHeight"),
        "period": avg("wavePeriod"),
        "direction": avg("waveDirection")
    }

def get_waves(lat, lon):
    key = (round(lat,2), round(lon,2))
    now = time.time()

    if key in wave_cache:
        val, ts = wave_cache[key]
        if now - ts < CACHE_WAVE_TTL:
            return val

    try:
        url = (
            "https://api.stormglass.io/v2/weather/point?"
            f"lat={lat}&lng={lon}&params=waveHeight,wavePeriod,waveDirection"
        )
        headers = {"Authorization": STORMGLASS_KEY}
        res = requests.get(url, headers=headers, timeout=10).json()

        wave = extract_wave(res["hours"][0])

        wave_cache[key] = (wave, now)
        return wave

    except Exception as e:
        logging.error(f"Wave error: {e}")
        return None

# ============================
# MAREE
# ============================

def get_tide(lat, lon):
    key = (round(lat,2), round(lon,2))
    now = time.time()

    if key in tide_cache:
        val, ts = tide_cache[key]
        if now - ts < CACHE_TIDE_TTL:
            return val

    try:
        url = f"https://www.worldtides.info/api/v3?heights&lat={lat}&lon={lon}&key={WORLDTIDES_KEY}"
        data = requests.get(url, timeout=10).json()

        tides = []
        for t in data.get("heights", [])[:4]:
            dt = datetime.utcfromtimestamp(t["dt"]).strftime("%H:%M")
            tides.append(f"{dt} ({round(t['height'],1)}m)")

        tide_cache[key] = (tides, now)
        return tides

    except Exception as e:
        logging.error(f"Tide error: {e}")
        return None

def format_tides(t):
    return "\n".join(t) if t else "n.d."

# ============================
# ADRIATIC LOGIC
# ============================

def sea_state_index(w):
    if not w or not w["height"] or not w["period"]: return None
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

def sailing_score(wind, gust, wave):
    if not wind or not wave: return "n.d."

    idx = sea_state_index(wave)
    score = 0

    if 8 <= wind <= 16: score += 3
    elif 5 <= wind < 8 or 16 < wind <= 20: score += 2

    if gust and gust > wind * 1.6: score -= 1

    if idx:
        if idx < 0.12: score += 3
        elif idx < 0.2: score += 2
        elif idx < 0.3: score += 1
        else: score -= 1

    labels = {
        5: "🟢 Perfette",
        4: "🟡 Buone",
        3: "🟠 Impegnative",
        2: "🔴 Dure",
        1: "⛔ Sconsigliate"
    }

    return labels.get(score, "⚠️ Variabili")

# ============================
# BONUS: migliore finestra uscita
# ============================

def best_time_window(forecast):
    best_hours = []

    for i, f in enumerate(forecast):
        wind = f["wind"]
        rain = f["rain"]

        if 8 <= wind <= 16 and rain < 0.2:
            hour = (datetime.utcnow().hour + i) % 24
            best_hours.append(f"{hour:02d}")

    if not best_hours:
        return "Nessuna finestra ideale"

    return f"{best_hours[0]}h–{best_hours[-1]}h"

# ============================
# FORECAST FORMAT
# ============================

def format_forecast(forecast):
    out = []
    for i, f in enumerate(forecast):
        hour = (datetime.utcnow().hour + i) % 24
        out.append(
            f"{hour:02d}h {kmh_to_kn(f['wind'])}kn {direzione(f['dir'])} "
            f"{meteo_code(f['code'])}"
        )
    return "\n".join(out)

# ============================
# REPORT
# ============================

def genera_report(lat, lon):
    forecast = get_weather(lat, lon)
    wave = get_waves(lat, lon)
    tides = get_tide(lat, lon)

    if not forecast:
        return "⚠️ Dati non disponibili"

    now_data = forecast[0]

    wind = now_data["wind"]
    gust = now_data["gust"]
    dir_txt = direzione(now_data["dir"])

    idx = sea_state_index(wave)
    cond = sailing_score(wind, gust, wave)

    report = f"""⛵ METEO VELA ADRIATICO
📍 {lat:.3f}, {lon:.3f}

🌬️ {kmh_to_kn(wind)} kn da {dir_txt}
💨 raffiche {kmh_to_kn(gust)} kn
📌 {vento_adriatico(wind, dir_txt)}

🌊 ONDE
{wave and wave['height']} m | {wave and wave['period']} s

📊 Mare: {sea_state_label(idx)} ({idx})

🌊 MAREA
{format_tides(tides)}

✅ CONDIZIONI: {cond}

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
    update.message.reply_text("Invia posizione", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

def pos(update, context):
    chat_id = update.message.chat_id
    loc = update.message.location

    lat, lon = loc.latitude, loc.longitude
    user_data_store[chat_id] = (lat, lon)

    update.message.reply_text(genera_report(lat, lon))

def meteo(update, context):
    chat_id = update.message.chat_id

    if chat_id not in user_data_store:
        update.message.reply_text("Invia posizione prima")
        return

    lat, lon = user_data_store[chat_id]
    update.message.reply_text(genera_report(lat, lon))

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
    dp.add_handler(CommandHandler("meteo", meteo))
    dp.add_handler(MessageHandler(Filters.location, pos))

    Thread(target=alert_loop, args=(updater.bot,), daemon=True).start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()