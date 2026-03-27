import os
import re
from datetime import datetime
from typing import Dict

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client, Client

# ==================== CONFIG ====================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")      # Use your anon/public key for now
APP_URL = os.getenv("APP_URL", "https://jubilant-octo-happiness.onrender.com")  # ← your current Render URL

app = FastAPI(title="miboga - Tu boga digital para el BCRA")

supabase: Client = None

@app.on_event("startup")
def startup():
    global supabase
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase connected - miboga ready")
    else:
        print("⚠️  Supabase env vars missing - DB disabled for now")

# ==================== BCRA SAFE LOOKUP (prueba de fuego) ====================
def normalize_identificacion(ident: str) -> str:
    cleaned = re.sub(r"\D", "", ident)
    if len(cleaned) != 11 and len(cleaned) != 8:  # allow both CUIT (11) and DNI (8)
        raise ValueError("El DNI o CUIT debe tener 8 o 11 dígitos")
    return cleaned

async def get_bcra_data(identificacion: str) -> Dict:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            deudas_resp = await client.get(
                f"https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas/{identificacion}"
            )
            deudas_resp.raise_for_status()
            deudas = deudas_resp.json()

            cheques_resp = await client.get(
                f"https://api.bcra.gob.ar/centraldedeudores/v1.0/ChequesRechazados/{identificacion}"
            )
            cheques = cheques_resp.json() if cheques_resp.status_code == 200 else {}

        return {
            "status": "success",
            "identificacion": identificacion,
            "deudas": deudas,
            "cheques": cheques,
            "timestamp": datetime.utcnow().isoformat()
        }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"status": "no_history", "message": "No existe historial crediticio aún"}
        raise
    except httpx.TimeoutException:
        return {"status": "bcra_down", "message": "El BCRA está lento o en mantenimiento"}
    except Exception as e:
        print(f"BCRA error: {e}")
        return {"status": "error", "message": "No pudimos conectar con el BCRA"}

# ==================== REQUEST MODEL ====================
class ChatRequest(BaseModel):
    identificacion: str        # DNI or CUIT
    channel: str = "web"       # "web" or "whatsapp" (future)

# ==================== MAIN CHAT ENDPOINT ====================
@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        ident = normalize_identificacion(req.identificacion)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    bcra_data = await get_bcra_data(ident)

    # Extract situation safely
    if bcra_data.get("status") == "success":
        try:
            situacion = bcra_data["deudas"]["results"]["periodos"][0]["entidades"][0]["situacion"]
        except (KeyError, IndexError, TypeError):
            situacion = 0
    else:
        situacion = 0

    # ==================== BOGA CRIOLLO RESPONSES ====================
    if bcra_data.get("status") == "success":
        response_text = f"""🇦🇷 Che, ya consulté tu situación en el BCRA.

Tu situación actual es **{situacion}**.

Mirá, esto significa que { "estás al día y todo en orden" if situacion == 1 else "tenés alguna mora reportada" if situacion <= 3 else "la cosa está complicada y hay que actuar rápido" }.

**Próximos pasos que te recomiendo:**
1. Ver exactamente qué banco o financiera te reportó.
2. Negociar o pagar la deuda.
3. Pedir el levantamiento una vez saldada.

¿Querés que te arme el plan completo y personalizado para salir o mejorar tu situación?"""

    elif bcra_data.get("status") == "no_history":
        response_text = """Tranca, no tenés deudas reportadas (Situación 0). 
Sos un fantasma para los bancos todavía. 
Esto es bueno, pero si querés pedir crédito en el futuro te conviene empezar a construir historial positivo.

¿Querés tips rápidos para armar tu historial crediticio?"""

    elif bcra_data.get("status") == "bcra_down":
        response_text = """Uy, el BCRA está tomando un café y no responde ahora. 
Ya anoté tu consulta. En unos minutos vuelvo a intentar y te aviso por acá."""

    else:
        response_text = """Hubo un problemita técnico del lado del BCRA. 
Probá de nuevo en un rato, che. Ya estoy arriba del tema."""

    return {
        "response": response_text,
        "situacion": situacion,
        "bcra_status": bcra_data.get("status"),
        "identificacion": ident,
        "app_url": APP_URL
    }

@app.get("/")
async def root():
    return {
        "app": "miboga - Tu boga digital para el BCRA",
        "status": "running",
        "version": "0.2.0 (miboga core)",
        "chat_endpoint": f"{APP_URL}/chat",
        "message": "Listo para usar. Probá el endpoint /chat con tu DNI o CUIT"
    }
