import requests
import time
import os
import logging
from datetime import datetime
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
wave_cache = {}
tide_cache = {}

CACHE_WAVE_TTL = 600
CACHE_TIDE_TTL = 1800

# ============================
# LOGGING
# ============================

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ============================
# UTILS
# ============================

def is_valid(data):
    return data is not None and data != {} and data != []

def kmh_to_kn(kmh):
    return round(kmh * 0.539957, 1)

def direzione(gradi):
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    i = int((gradi + 11.25)/22.5) % 16
    return dirs[i]

def stima_onde(vento):
    if vento < 5: return 0.1
    elif vento < 10: return 0.5
    elif vento < 20: return 1.2
    elif vento < 30: return 2.5
    else: return 4.0

# ============================
# LUNA
# ============================

def fase_lunare():
    now = datetime.utcnow()
    diff = now - datetime(2001, 1, 1)
    days = diff.days + (diff.seconds / 86400)
    lunations = 0.20439731 + days * 0.03386319269
    phase = lunations % 1

    if phase < 0.03 or phase > 0.97:
        return "🌑 nuova"
    elif phase < 0.25:
        return "🌒 crescente"
    elif phase < 0.5:
        return "🌓 primo quarto"
    elif phase < 0.75:
        return "🌔 crescente"
    else:
        return "🌕 piena"

# ============================
# METEO (Open-Meteo)
# ============================

def get_weather(lat, lon):
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        )

        r = requests.get(url, timeout=3)
        data = r.json()

        if "hourly" not in data:
            logging.warning("Weather: risposta senza hourly")
            return None

        return data["hourly"]

    except Exception as e:
        logging.error(f"Weather error: {e}")
        return None

# ============================
# ONDE (SG FIRST)
# ============================

def get_waves(lat, lon):
    key = (round(lat,2), round(lon,2))
    now = time.time()

    if key in wave_cache and now - wave_cache[key]["time"] < CACHE_WAVE_TTL:
        return wave_cache[key]["data"]

    try:
        url = "https://api.stormglass.io/v2/weather/point"
        params = {
            "lat": lat,
            "lng": lon,
            "params": "waveHeight"
        }
        headers = {"Authorization": STORMGLASS_KEY}

        r = requests.get(url, params=params, headers=headers, timeout=3)
        data = r.json()

        wave = data["hours"][0]["waveHeight"]["sg"]

        if is_valid(wave):
            logging.info("🌊 Onde: SG")
            wave_cache[key] = {"data": wave, "time": now}
            return wave

    except Exception as e:
        logging.warning(f"SG onde failed: {e}")

    logging.info("🌊 Onde: fallback")
    return 0.0

# ============================
# MAREE (SG FIRST → WT)
# ============================

def get_tide(lat, lon):
    key = (round(lat,2), round(lon,2))
    now = time.time()

    if key in tide_cache and now - tide_cache[key]["time"] < CACHE_TIDE_TTL:
        return tide_cache[key]["data"]

    # SG
    try:
        url = "https://api.stormglass.io/v2/tide/extremes/point"
        params = {"lat": lat, "lng": lon}
        headers = {"Authorization": STORMGLASS_KEY}

        r = requests.get(url, params=params, headers=headers, timeout=3)
        data = r.json().get("data", [])

        if data:
            logging.info("🌊 Maree: SG")
            tide_cache[key] = {"data": data, "time": now}
            return data

    except Exception as e:
        logging.warning(f"SG maree failed: {e}")

    # fallback WorldTides
    try:
        url = (
            f"https://www.worldtides.info/api/v3?"
            f"extremes&lat={lat}&lon={lon}&key={WORLDTIDES_KEY}"
        )
        data = requests.get(url, timeout=3).json().get("extremes", [])

        if data:
            logging.info("🌊 Maree: WT")
            return data

    except Exception as e:
        logging.warning(f"WT maree failed: {e}")

    return []

# ============================
# FORMAT
# ============================

def format_tides(tides):
    if not tides:
        return "n.d."

    out = []
    for t in tides[:2]:
        tipo = "Alta" if t.get("type") == "high" else "Bassa"
        h = t.get("height", "?")
        out.append(f"{tipo}: {h} m")

    return "\n".join(out)

# ============================
# REPORT
# ============================

def genera_report(lat, lon):
    weather = get_weather(lat, lon)

    wind = weather["wind_speed_10m"][0]
    gust = weather["wind_gusts_10m"][0]
    direction = direzione(weather["wind_direction_10m"][0])

    wave = get_waves(lat, lon)
    tides = get_tide(lat, lon)

    report = f"""
🌊 METEO MARE PRO
📍 {lat:.2f}, {lon:.2f}

🌬️ {kmh_to_kn(wind)} kn da {direction}
💨 raffiche {kmh_to_kn(gust)} kn

🌊 onde: {wave} m

🌊 MAREA
{format_tides(tides)}

🌙 fase lunare: {fase_lunare()}
"""
    return report

# ============================
# TELEGRAM
# ============================

def start(update, context):
    keyboard = [[KeyboardButton("📍 Invia posizione", request_location=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    update.message.reply_text(
        "Ciao! Inviami la tua posizione.",
        reply_markup=reply_markup
    )

def posizione(update, context):
    chat_id = update.message.chat_id
    loc = update.message.location

    user_data_store[chat_id] = (loc.latitude, loc.longitude)

    update.message.reply_text("✅ Posizione salvata!")

def meteo_cmd(update, context):
    chat_id = update.message.chat_id

    if chat_id not in user_data_store:
        update.message.reply_text("Invia prima la posizione.")
        return

    lat, lon = user_data_store[chat_id]
    report = genera_report(lat, lon)

    update.message.reply_text(report)

# ============================
# ALERT LOOP (base)
# ============================

def alert_loop(bot):
    while True:
        try:
            for user_id, (lat, lon) in user_data_store.items():
                report = genera_report(lat, lon)
                bot.send_message(chat_id=user_id, text=report)

        except Exception as e:
            logging.error(f"Alert loop error: {e}")

        time.sleep(3600)  # ogni ora

# ============================
# MAIN
# ============================

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("meteo", meteo_cmd))
    dp.add_handler(MessageHandler(Filters.location, posizione))

    updater.start_polling()

    # thread alert
    Thread(target=alert_loop, args=(updater.bot,), daemon=True).start()

    updater.idle()

# ============================

if __name__ == "__main__":
    main()