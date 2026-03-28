import os
import re
import asyncio
from datetime import datetime
from typing import Dict

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import create_client, Client

# ==================== CONFIG ====================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")          # from Meta
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")        # from Meta
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "miboga_verify")  # you choose this

APP_URL = os.getenv("APP_URL", "https://jubilant-octo-happiness.onrender.com")

app = FastAPI(title="miboga - Tu boga digital para el BCRA")

supabase: Client = None

@app.on_event("startup")
def startup():
    global supabase
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase connected - miboga ready")
    else:
        print("⚠️ Supabase env vars missing")

# Serve landing page at root
@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# ==================== BCRA + SAVE FUNCTIONS (unchanged) ====================
def normalize_identificacion(ident: str) -> str:
    cleaned = re.sub(r"\D", "", ident)
    if len(cleaned) not in (8, 11):
        raise ValueError("El DNI o CUIT debe tener 8 u 11 dígitos")
    return cleaned

async def get_bcra_data(identificacion: str) -> Dict:
    # (same robust function as before - omitted for brevity, copy from your previous working version)
    # ... [keep your existing get_bcra_data function here] ...
    pass  # ← replace this line with your full get_bcra_data function from the previous version

async def save_consultation(identificacion: str, situacion: int):
    if not supabase: return
    try:
        data = {
            "identificacion": identificacion,
            "last_situation": situacion,
            "last_check_at": datetime.utcnow().isoformat()
        }
        supabase.table("users").upsert(data, on_conflict="identificacion").execute()
        print(f"✅ Saved consultation for {identificacion} → {situacion}")
    except Exception as e:
        print(f"Supabase save error: {e}")

# ==================== WHATSAPP WEBHOOK ====================
@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta verification step"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ WhatsApp webhook verified")
        return challenge
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def whatsapp_webhook(request: Request):
    """Receive messages from WhatsApp"""
    data = await request.json()
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" in entry:
            message = entry["messages"][0]
            from_number = message["from"]
            text = message.get("text", {}).get("body", "")

            print(f"📨 WhatsApp from {from_number}: {text}")

            # Simple reply with buttons (first contact)
            await send_welcome_buttons(from_number)

    except Exception as e:
        print(f"WhatsApp webhook error: {e}")
    return {"status": "ok"}

async def send_welcome_buttons(to_number: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "🇦🇷 Bienvenido a miboga, tu boga digital para el BCRA.\n¿Qué querés hacer?"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "btn_consultar", "title": "Consultar mi situación"}},
                    {"type": "reply", "reply": {"id": "btn_bcra", "title": "¿Qué es el BCRA?"}},
                    {"type": "reply", "reply": {"id": "btn_salir", "title": "Cómo salir del Veraz"}}
                ]
            }
        }
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, headers=headers)

# Keep your existing /chat endpoint (the one with boga responses) unchanged below...
# (paste your full /chat function from the previous working version here)
