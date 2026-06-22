import requests
import os
from datetime import datetime
from math import floor

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ============================
# CONFIG
# ============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

last_location = None

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
# AREA ADRIATICO
# ============================

def is_adriatic(lat, lon):
    return 40.0 <= lat <= 46.5 and 12.0 <= lon <= 20.0

# ============================
# VENTO ADRIATICO
# ============================

def vento_adriatico(gradi):
    d = direzione(gradi)

    if d in ["NE", "NNE", "ENE"]:
        return "💨 Bora"
    elif d in ["SE", "SSE", "ESE"]:
        return "🌊 Scirocco"
    else:
        return "🌬 Vento locale"

# ============================
# METEO
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

def get_weather(lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "hourly": "windspeed_10m,windgusts_10m,winddirection_10m,weathercode",
        "timezone": "auto"
    }

    r = requests.get(WEATHER_URL, params=params)
    data = r.json()

    return data["current_weather"], data["hourly"]

# ============================
# MARINE (ONDE REALI)
# ============================

def get_marine(lat, lon):
    if not is_adriatic(lat, lon):
        return None, None, None

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wave_height,wave_period,wave_direction",
        "timezone": "auto"
    }

    r = requests.get(MARINE_URL, params=params)
    data = r.json()

    try:
        h = data["hourly"]["wave_height"][0]
        p = data["hourly"]["wave_period"][0]
        d = data["hourly"]["wave_direction"][0]
        return h, p, d
    except:
        return None, None, None

# ============================
# STATO MARE (ADRIATICO)
# ============================

def sea_state_index(height, period):
    if not height or not period:
        return None
    return round(height / period, 2)

def sea_state_label(index):
    if index is None:
        return "n.d."
    if index < 0.08:
        return "🟢 Lungo"
    elif index < 0.15:
        return "🟡 Buono"
    elif index < 0.25:
        return "🟠 Corto"
    else:
        return "🔴 Molto mosso"

# ============================
# FASE LUNARE
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
# FORECAST
# ============================


ef format_forecast(hourly):
    out = []

    times = hourly["time"]
    winds = hourly["windspeed_10m"]
    gusts = hourly["windgusts_10m"]
    dirs = hourly["winddirection_10m"]
    codes = hourly["weathercode"]

    # ora attuale
    now = datetime.now()

    # trova indice della prima ora >= now
    start_idx = 0
    for i, t in enumerate(times):
        t_dt = datetime.fromisoformat(t)
        if t_dt >= now:
            start_idx = i
            break

    # prendi le 12 ore successive da lì
    for i in range(start_idx, min(start_idx + 12, len(times))):
        t_dt = datetime.fromisoformat(times[i])
        ora = t_dt.strftime("%H:%M")

        vento = kmh_to_kn(winds[i])
        raffica = kmh_to_kn(gusts[i])
        dir_vento = direzione(dirs[i])
        icon, desc = meteo_code(codes[i])

        out.append(f"{ora} {icon} {vento}kn ({raffica}) {dir_vento}")

    return "\n".join(out)

# ============================
# REPORT
# ============================

def genera_report(lat, lon):
    current, hourly = get_weather(lat, lon)

    wave_h, wave_p, wave_dir = get_marine(lat, lon)

    wind = current["windspeed"]
    wind_dir = current["winddirection"]

    vento_tipo = vento_adriatico(wind_dir)

    # ONDE
    if wave_h is not None:
        idx = sea_state_index(wave_h, wave_p)

        onde_txt = f"""
🌊 ONDE (Adriatico)
Altezza: {wave_h:.2f} m
Periodo: {wave_p:.1f} s
Direzione: {direzione(wave_dir)}

📊 STATO DEL MARE
{sea_state_label(idx)}
"""
    else:
        onde_txt = "\n🌊 ONDE: non disponibili (fuori Adriatico)\n"

    return f"""
📅 {datetime.now().strftime('%d %B %Y')}

📍 METEO GENERALE
🌬️ Vento: {kmh_to_kn(wind)} kn da {direzione(wind_dir)}
📌 {vento_tipo}
🌙 Fase lunare: {moon_phase()}
{onde_txt}

🕒 FORECAST 12H
{format_forecast(hourly)}
"""

# ============================
# TELEGRAM
# ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("📍 Posizione", request_location=True)]]

    await update.message.reply_text(
        "Invia la posizione 📍",
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