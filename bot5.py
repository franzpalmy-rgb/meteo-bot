import requests
import os
import logging
from datetime import datetime
from math import floor

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ============================
# CONFIG
# ============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

last_location = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
# METEO DECODE
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
# METEO
# ============================

def get_weather(lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "hourly": "windspeed_10m,windgusts_10m,winddirection_10m,weathercode",
        "timezone": "auto"
    }

    r = requests.get(OPEN_METEO_URL, params=params)
    data = r.json()

    return data["current_weather"], data["hourly"]

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
        "🌒 Crescente",
        "🌓 Primo quarto",
        "🌔 Gibosa crescente",
        "🌕 Piena",
        "🌖 Gibosa calante",
        "🌗 Ultimo quarto",
        "🌘 Calante"
    ]

    return phases[index]

# ============================
# FORECAST (con descrizione + raffiche)
# ============================

def format_forecast(hourly):
    out = []

    times = hourly["time"][:12]
    winds = hourly["windspeed_10m"][:12]
    gusts = hourly["windgusts_10m"][:12]
    directions = hourly["winddirection_10m"][:12]
    codes = hourly["weathercode"][:12]

    for i in range(len(times)):
        ora = times[i].split("T")[1]
        icona, desc = meteo_code(codes[i])

        out.append(
            f"{ora} → {icona} {desc}\n"
            f"   🌬 {kmh_to_kn(winds[i])} kn "
            f"(raff. {kmh_to_kn(gusts[i])} kn) "
            f"da {direzione(directions[i])}"
        )

    return "\n".join(out)

# ============================
# REPORT
# ============================

def genera_report(lat, lon):
    current, hourly = get_weather(lat, lon)

    wind = current["windspeed"]
    wind_dir = current["winddirection"]

    return f"""
📅 {datetime.now().strftime('%d %B %Y')}

📍 METEO GENERALE
🌬️ Vento: {kmh_to_kn(wind)} kn da {direzione(wind_dir)}
🌙 Fase lunare: {moon_phase()}

🕒 FORECAST 12H
{format_forecast(hourly)}
"""

# ============================
# TELEGRAM
# ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("📍 Posizione", request_location=True)]]

    await update.message.reply_text(
        "Invia la tua posizione 📍",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def pos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_location

    loc = update.message.location
    last_location = (loc.latitude, loc.longitude)

    report = genera_report(loc.latitude, loc.longitude)
    await update.message.reply_text(report)

async def meteo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not last_location:
        await update.message.reply_text("Invia prima la posizione.")
        return

    lat, lon = last_location
    report = genera_report(lat, lon)

    await update.message.reply_text(report)

# ============================
# MAIN
# ============================

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("meteo", meteo))
    app.add_handler(MessageHandler(filters.LOCATION, pos))

    app.run_polling()

if __name__ == "__main__":
    main()