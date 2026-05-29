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

def kmh_to_kn(kmh):
    return round(kmh * 0.539957, 1)

def direzione(gradi):
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    i = int((gradi + 11.25)/22.5) % 16
    return f"{dirs[i]} ({gradi}°)"

def meteo_code(code):
    mapping = {
        0: "☀️ sereno",
        1: "🌤️ poco nuvoloso",
        2: "⛅ variabile",
        3: "☁️ coperto",
        61: "🌧️ pioggia",
        80: "🌧️ rovesci",
        95: "⛈️ temporale"
    }
    return mapping.get(code, "variabile")

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
        return "🌑 Luna Nuova"
    elif phase < 0.22:
        return "🌒 Crescente"
    elif phase < 0.28:
        return "🌓 Primo Quarto"
    elif phase < 0.47:
        return "🌔 Gibbosa crescente"
    elif phase < 0.53:
        return "🌕 Luna Piena"
    elif phase < 0.72:
        return "🌖 Gibbosa calante"
    elif phase < 0.78:
        return "🌗 Ultimo Quarto"
    else:
        return "🌘 Calante"

# ============================
# OPEN METEO
# ============================

def get_weather(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?" \
          f"latitude={lat}&longitude={lon}&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,weather_code"
    return requests.get(url).json()["hourly"]

# ============================
# ONDE (StormGlass con cache)
# ============================

def get_waves(lat, lon):
    key = (round(lat,2), round(lon,2))
    now = time.time()

    if key in wave_cache and now - wave_cache[key]["time"] < 10800:
        return wave_cache[key]["value"], "cache"

    try:
        url = "https://api.stormglass.io/v2/weather/point"
        params = {"lat": lat, "lng": lon, "params": "waveHeight"}
        headers = {"Authorization": STORMGLASS_KEY}

        data = requests.get(url, params=params, headers=headers).json()
        wave = data["hours"][0]["waveHeight"]["sg"]

        wave_cache[key] = {"value": wave, "time": now}

        return wave, "reale"
    except:
        return None, "fallback"

def stima_onde(vento):
    if vento < 5: return 0.1
    elif vento < 10: return 0.5
    elif vento < 20: return 1.2
    elif vento < 30: return 2.5
    else: return 4.0

# ============================
# MAREE (WorldTides)
# ============================

def get_tide(lat, lon):
    key = (round(lat,2), round(lon,2))
    now = time.time()

    if key in tide_cache and now - tide_cache[key]["time"] < 21600:
        return tide_cache[key]["high"], tide_cache[key]["low"], "cache"

    try:
        url = "https://www.worldtides.info/api/v3"
        params = {
            "lat": lat,
            "lon": lon,
            "key": WORLDTIDES_KEY,
            "extremes": ""
        }

        data = requests.get(url, params=params).json()

        highs, lows = [], []

        for item in data.get("extremes", []):
            t = datetime.fromtimestamp(item["dt"]).strftime("%H:%M")
            if item["type"] == "High":
                highs.append(t)
            else:
                lows.append(t)

        high_txt = ", ".join(highs[:2]) or "N/D"
        low_txt = ", ".join(lows[:2]) or "N/D"

        tide_cache[key] = {
            "high": high_txt,
            "low": low_txt,
            "time": now
        }

        return high_txt, low_txt, "reale"

    except:
        return "06:00", "12:00", "fallback"

# ============================
# EVOLUZIONE
# ============================

def evoluzione(data):
    out = []

    for i in [0, 3, 6, 9, 12]:
        if i < len(data["wind_speed_10m"]):
            kn = kmh_to_kn(data["wind_speed_10m"][i])
            dir_txt = direzione(data["wind_direction_10m"][i])
            meteo = meteo_code(data["weather_code"][i])
            out.append(f"{i}h {kn}kn {dir_txt} {meteo}")

    return "\n".join(out)

# ============================
# SAFE SEND
# ============================

def safe_send(bot, chat_id, text):
    try:
        bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logging.error(f"Errore Telegram: {e}")

# ============================
# REPORT
# ============================

def genera_report(lat, lon):
    data = get_weather(lat, lon)

    speed = kmh_to_kn(data["wind_speed_10m"][0])
    gust = kmh_to_kn(data["wind_gusts_10m"][0])
    dir_txt = direzione(data["wind_direction_10m"][0])
    meteo = meteo_code(data["weather_code"][0])

    wave, source = get_waves(lat, lon)
    if wave is None:
        wave = stima_onde(speed)
        source = "stimato"

    alta, bassa, src_marea = get_tide(lat, lon)

    luna = fase_lunare()

    trend = evoluzione(data)

    report = f"""
🌊 METEO MARE PRO

📍 {lat:.3f},{lon:.3f}

{meteo}

🌬️ {speed}kn da {dir_txt}
raffiche {gust}kn

🌊 onde: {wave} m ({source})

🌊 MAREA
Alta: {alta}
Bassa: {bassa} ({src_marea})

🌙 {luna}

📊 EVOLUZIONE 12H
{trend}
"""

    return report, speed, gust, wave

# ============================
# ALERT LOOP
# ============================

def alert_loop(bot):
    while True:
        try:
            for user_id, (lat, lon) in user_data_store.items():
                report, speed, gust, wave = genera_report(lat, lon)

                if speed > 25 or gust > 35 or wave > 2.5:
                    safe_send(bot, user_id, "🚨 ALERT METEO\n\n" + report)

            time.sleep(900)

        except Exception as e:
            logging.error(f"Errore alert: {e}")
            time.sleep(30)

# ============================
# TELEGRAM
# ============================

def start(update, context):
    keyboard = [[KeyboardButton("📍 Invia posizione", request_location=True)]]

    update.message.reply_text(
        "Invia posizione",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

def posizione(update, context):
    chat_id = update.message.chat_id
    loc = update.message.location

    user_data_store[chat_id] = (loc.latitude, loc.longitude)

    report, _, _, _ = genera_report(loc.latitude, loc.longitude)

    safe_send(context.bot, chat_id, "✅ Posizione salvata\n\n" + report)

def meteo_cmd(update, context):
    chat_id = update.message.chat_id

    if chat_id not in user_data_store:
        safe_send(context.bot, chat_id, "Invia prima posizione 📍")
        return

    lat, lon = user_data_store[chat_id]
    report, _, _, _ = genera_report(lat, lon)

    safe_send(context.bot, chat_id, report)

# ============================
# MAIN (ANTI CRASH)
# ============================

def main():
    while True:
        try:
            logging.info("✅ BOT AVVIATO")

            updater = Updater(TELEGRAM_TOKEN, use_context=True)
            dp = updater.dispatcher

            dp.add_handler(CommandHandler("start", start))
            dp.add_handler(CommandHandler("meteo", meteo_cmd))
            dp.add_handler(MessageHandler(Filters.location, posizione))

            Thread(target=alert_loop, args=(updater.bot,), daemon=True).start()

            updater.start_polling()
            updater.idle()

        except Exception as e:
            logging.error(f"CRASH: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()