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
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
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

# ==================== BCRA LOOKUP (more robust) ====================
def normalize_identificacion(ident: str) -> str:
    cleaned = re.sub(r"\D", "", ident)
    if len(cleaned) not in (8, 11):
        raise ValueError("El DNI o CUIT debe tener 8 o 11 dígitos")
    return cleaned

async def get_bcra_data(identificacion: str) -> Dict:
    print(f"🔍 Consultando BCRA para: {identificacion}")
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            # 1. Main Deudas endpoint (most important)
            deudas_resp = await client.get(
                f"https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas/{identificacion}"
            )
            deudas_resp.raise_for_status()
            deudas = deudas_resp.json()

            # 2. Cheques endpoint (optional - often fails)
            cheques = {}
            try:
                cheques_resp = await client.get(
                    f"https://api.bcra.gob.ar/centraldedeudores/v1.0/ChequesRechazados/{identificacion}"
                )
                if cheques_resp.status_code == 200:
                    cheques = cheques_resp.json()
            except Exception:
                pass  # ignore if this one fails

        print(f"✅ BCRA success for {identificacion}")
        return {
            "status": "success",
            "identificacion": identificacion,
            "deudas": deudas,
            "cheques": cheques,
            "timestamp": datetime.utcnow().isoformat()
        }

    except httpx.HTTPStatusError as e:
        print(f"BCRA HTTP error {e.response.status_code} for {identificacion}")
        if e.response.status_code == 404:
            return {"status": "no_history", "message": "No existe historial crediticio aún"}
        return {"status": "error", "message": f"HTTP {e.response.status_code}"}
    except httpx.TimeoutException:
        print("BCRA timeout")
        return {"status": "bcra_down", "message": "Timeout - BCRA lento"}
    except Exception as e:
        print(f"BCRA connection error: {str(e)}")
        return {"status": "error", "message": str(e)}

# ==================== REQUEST MODEL ====================
class ChatRequest(BaseModel):
    identificacion: str
    channel: str = "web"

# ==================== CHAT ENDPOINT ====================
@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        ident = normalize_identificacion(req.identificacion)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    bcra_data = await get_bcra_data(ident)

    # Safe situation extraction
    if bcra_data.get("status") == "success":
        try:
            situacion = bcra_data["deudas"]["results"]["periodos"][0]["entidades"][0]["situacion"]
        except Exception:
            situacion = 0
    else:
        situacion = 0

    # ==================== BOGA RESPONSES ====================
    if bcra_data.get("status") == "success":
        response_text = f"""🇦🇷 Che, ya consulté tu situación en el BCRA.

Tu situación actual es **{situacion}**.

Mirá, esto significa que { "estás al día" if situacion == 1 else "tenés alguna mora" if situacion <= 3 else "la cosa está complicada" }.

**Próximos pasos:**
1. Ver qué entidad te reportó
2. Negociar o pagar
3. Pedir baja una vez saldada

¿Querés el plan completo personalizado?"""

    elif bcra_data.get("status") == "no_history":
        response_text = """Tranca, no tenés deudas reportadas (Situación 0). 
Sos un fantasma para los bancos. 
¿Querés tips para construir historial positivo?"""

    elif bcra_data.get("status") == "bcra_down":
        response_text = """Uy, el BCRA está lento o en mantenimiento. 
Ya anoté tu consulta. Probá de nuevo en 5-10 minutos, che."""

    else:
        response_text = """Hubo un problemita técnico del lado del BCRA. 
El sistema está un poco inestable hoy. Probá de nuevo en unos minutos, ya estoy arriba del tema."""

    return {
        "response": response_text,
        "situacion": situacion,
        "bcra_status": bcra_data.get("status"),
        "identificacion": ident
    }

@app.get("/")
async def root():
    return {"app": "miboga", "status": "running", "message": "Listo para probar /chat"}
