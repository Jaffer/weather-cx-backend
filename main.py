import asyncio
import os
from contextlib import asynccontextmanager
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request

load_dotenv()

# Background task to keep Render free tier awake by pinging its public URL
async def keep_alive_loop():
    # Wait 60 seconds after startup before starting periodic pings
    await asyncio.sleep(60)
    while True:
        url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("KEEP_ALIVE_URL")
        if url:
            ping_url = f"{url.rstrip('/')}/health"
            try:
                res = await asyncio.to_thread(requests.get, ping_url, timeout=10)
                print(f"[Keep-Alive] Pinged {ping_url} - status code: {res.status_code}")
            except Exception as e:
                print(f"[Keep-Alive] Ping failed: {e}")
        else:
            print("[Keep-Alive] No public URL found (set RENDER_EXTERNAL_URL or KEEP_ALIVE_URL).")
        # Ping every 10 minutes (600s) to beat Render's 15-minute inactivity timeout
        await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start keep-alive loop on startup
    task = asyncio.create_task(keep_alive_loop())
    yield
    # Clean up on shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)

# API keys loaded from environment variables
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
OPENWEATHER_KEY = os.getenv("OPENWEATHER_KEY", "")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Weather CX Backend is running"}

@app.get("/health")
@app.get("/ping")
def health_check():
    return {"status": "alive", "message": "Service is active"}



@app.post("/webhook")
async def weather_webhook(request: Request):
    req = await request.json()
    
    # Extract parameters from Dialogflow
    session_info = req.get("sessionInfo", {})
    parameters = session_info.get("parameters", {})
    
    city = parameters.get("city")
    attribute = parameters.get("weather_attribute")
    unit = parameters.get("unit_preference", "celsius") # Defaults to celsius if user doesn't specify
    
    if not city:
        return format_cx_response("I'm sorry, I didn't catch the city name. Could you repeat it?")

    # ==========================================
    # PRIMARY SOURCE: WeatherAPI.com
    # ==========================================
    url_weatherapi = f"https://api.weatherapi.com/v1/current.json?key={WEATHER_API_KEY}&q={city}&aqi=yes"
    response = requests.get(url_weatherapi)
    
    if response.status_code == 200:
        api_response = response.json()
        current = api_response.get("current", {})
        
        if attribute == "air_quality":
            aqi = current.get("air_quality", {}).get("us-epa-index", "unknown")
            reply_text = f"The US EPA Air Quality Index in {city.title()} is level {aqi} (1=Good, 6=Hazardous)."
            
        elif attribute == "temperature":
            temp = current['temp_f'] if unit == "fahrenheit" else current['temp_c']
            unit_label = "°F" if unit == "fahrenheit" else "°C"
            reply_text = f"The current temperature in {city.title()} is {temp}{unit_label}."
            
        elif attribute == "wind":
            speed = current['wind_mph'] if unit == "fahrenheit" else current['wind_kph']
            unit_label = "mph" if unit == "fahrenheit" else "km/h"
            reply_text = f"The wind speed in {city.title()} is currently {speed} {unit_label}."
            
        elif attribute == "humidity":
            reply_text = f"The humidity level in {city.title()} is {current['humidity']}%."
            
        else:
            temp = current['temp_f'] if unit == "fahrenheit" else current['temp_c']
            unit_label = "°F" if unit == "fahrenheit" else "°C"
            condition = current["condition"]["text"]
            reply_text = f"The current weather in {city.title()} is {temp}{unit_label} with {condition.lower()}."
            
        return format_cx_response(reply_text)

    # ==========================================
    # FALLBACK SOURCE: OpenWeatherMap
    # ==========================================
    # If WeatherAPI fails, the code drops down here to automatically try OpenWeather
    openweather_unit = "imperial" if unit == "fahrenheit" else "metric"
    url_openweather = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_KEY}&units={openweather_unit}"
    fallback_response = requests.get(url_openweather)
    
    if fallback_response.status_code == 200:
        fallback_data = fallback_response.json()
        temp = fallback_data["main"]["temp"]
        unit_label = "°F" if unit == "fahrenheit" else "°C"
        condition = fallback_data["weather"][0]["description"]
        reply_text = f"(Fallback Source: OpenWeather) The current weather in {city.title()} is {temp}{unit_label} with {condition.lower()}."
        return format_cx_response(reply_text)

    # If both APIs fail (e.g., fake city name)
    return format_cx_response(f"I'm sorry, I couldn't retrieve the weather data for {city.title()} from any of my sources.")

def format_cx_response(message: str):
    return {
        "fulfillmentResponse": {
            "messages": [{"text": {"text": [message]}}]
        }
    }