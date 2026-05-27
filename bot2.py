import requests
import time
from datetime import datetime
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram import ReplyKeyboardMarkup, KeyboardButton
from threading import Thread

# ============================
# CONFIG
# ============================

TELEGRAM_TOKEN = "8772925806:AAHc6eK2tLm4AfmL63RlHnAUlmDpZB1hjW8"
METEOBLUE_KEY = "ewCtvPsQ3DuiHq9N"
STORMGLASS_KEY = "41beea66-59ad-11f1-8721-0242ac120004-41beebc4-59ad-11f1-8721-0242ac120004"

# storage utenti + cache
user_data_store = {}
wave_cache = {}  # {(lat, lon): {"value": x, "time": timestamp}}

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
# OPEN METEO
# ============================

def get_weather(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?" \
          f"latitude={lat}&longitude={lon}&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,weather_code"

    return requests.get(url).json()["hourly"]


# ============================
# STORMGLASS OTTIMIZZATO
# ============================

def get_waves(lat, lon):
    key = (round(lat,2), round(lon,2))
    now = time.time()

    # ✅ usa cache se < 3 ore
    if key in wave_cache:
        if now - wave_cache[key]["time"] < 10800:
            return wave_cache[key]["value"], "cache"

    # ✅ prova API (consuma richiesta)
    try:
        url = "https://api.stormglass.io/v2/weather/point"
        params = {"lat": lat, "lng": lon, "params": "waveHeight"}
        headers = {"Authorization": STORMGLASS_KEY}

        data = requests.get(url, params=params, headers=headers).json()
        wave = data["hours"][0]["waveHeight"]["sg"]

        # salva cache
        wave_cache[key] = {"value": wave, "time": now}

        return wave, "reale"

    except:
        return None, "fallback"


# ============================
# ONDE STIMATE (backup)
# ============================

def stima_onde(vento):
    if vento < 5:
        return 0.1
    elif vento < 10:
        return 0.5
    elif vento < 20:
        return 1.2
    elif vento < 30:
        return 2.5
    else:
        return 4.0


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
# ALLERTE (NO API EXTRA)
# ============================

def check_alert(speed, gust, wave):
    if speed > 25 or gust > 35 or wave > 2.5:
        return True
    return False


# ============================
# REPORT
# ============================

def genera_report(lat, lon):
    data = get_weather(lat, lon)

    speed = kmh_to_kn(data["wind_speed_10m"][0])
    gust = kmh_to_kn(data["wind_gusts_10m"][0])
    dir_txt = direzione(data["wind_direction_10m"][0])
    meteo = meteo_code(data["weather_code"][0])

    # onde
    wave, source = get_waves(lat, lon)

    if wave is None:
        wave = stima_onde(speed)
        source = "stimato"

    trend = evoluzione(data)

    ora = datetime.now().strftime("%H:%M")

    report = f"""
🌊 METEO MARE PRO

🕒 {ora}
📍 {lat:.3f},{lon:.3f}

{meteo}

🌬️ {speed}kn da {dir_txt}
raffiche {gust}kn

🌊 onde: {wave} m ({source})

📊 12H
{trend}
"""

    return report, speed, gust, wave


# ============================
# ALERT LOOP (usa SOLO open-meteo)
# ============================

def alert_loop(bot):
    while True:
        for user_id, data in user_data_store.items():
            lat, lon = data

            meteo = get_weather(lat, lon)

            speed = kmh_to_kn(meteo["wind_speed_10m"][0])
            gust = kmh_to_kn(meteo["wind_gusts_10m"][0])

            wave = stima_onde(speed)  # NON usa StormGlass

            if check_alert(speed, gust, wave):
                report, _, _, _ = genera_report(lat, lon)

                bot.send_message(
                    chat_id=user_id,
                    text="🚨 ALERT METEO\n\n" + report
                )

        time.sleep(900)  # ogni 15 min


# ============================
# INVIO SICURO TELEGRAM (ANTI PROXY ERROR)
# ============================

import time

def safe_send(bot, chat_id, text):
    for i in range(3):  # prova 3 volte
        try:
            bot.send_message(chat_id=chat_id, text=text)
            return
        except Exception as e:
            print("Errore Telegram:", e)
            time.sleep(2)

# ============================
# TELEGRAM
# ============================

def start(update, context):
    keyboard = [[KeyboardButton("📍 Invia posizione", request_location=True)]]

    update.message.reply_text(
        "Premi per inviare posizione",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )


def posizione(update, context):
    user_id = update.message.chat_id
    loc = update.message.location

    user_data_store[user_id] = (loc.latitude, loc.longitude)

    report, _, _, _ = genera_report(loc.latitude, loc.longitude)

    update.message.reply_text("✅ Posizione salvata\n\n" + report)


def meteo_cmd(update, context):
    user_id = update.message.chat_id

    if user_id not in user_data_store:
        update.message.reply_text("Invia prima posizione 📍")
        return

    lat, lon = user_data_store[user_id]

    report, _, _, _ = genera_report(lat, lon)

    update.message.reply_text(report)


# ============================
# MAIN
# ============================

def main():
    print("✅ BOT AVVIATO")

    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("meteo", meteo_cmd))
    dp.add_handler(MessageHandler(Filters.location, posizione))

    updater.start_polling()

    Thread(target=alert_loop, args=(updater.bot,), daemon=True).start()

    updater.idle()


if __name__ == "__main__":
    main()