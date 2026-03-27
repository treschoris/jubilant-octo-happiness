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
APP_URL = os.getenv("APP_URL", "https://jubilant-octo-happiness.onrender.com")  # Update if your Render URL changed

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

# ==================== BCRA LOOKUP (robust version) ====================
def normalize_identificacion(ident: str) -> str:
    cleaned = re.sub(r"\D", "", ident)
    if len(cleaned) not in (8, 11):
        raise ValueError("El DNI o CUIT debe tener 8 o 11 dígitos")
    return cleaned

async def get_bcra_data(identificacion: str) -> Dict:
    print(f"🔍 Consultando BCRA para: {identificacion}")
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            # Main Deudas endpoint (most reliable)
            deudas_resp = await client.get(
                f"https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas/{identificacion}"
            )
            deudas_resp.raise_for_status()
            deudas = deudas_resp.json()

            # ChequesRechazados - optional, often flaky
            cheques = {}
            try:
                cheques_resp = await client.get(
                    f"https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas/ChequesRechazados/{identificacion}"
                )
                if cheques_resp.status_code == 200:
                    cheques = cheques_resp.json()
            except Exception as e:
                print(f"Cheques endpoint skipped: {e}")

        print(f"✅ BCRA success for {identificacion}")
        return {
            "status": "success",
            "identificacion": identificacion,
            "deudas": deudas,
            "cheques": cheques,
            "timestamp": datetime.utcnow().isoformat()
        }

    except httpx.HTTPStatusError as e:
        print(f"BCRA HTTP {e.response.status_code} for {identificacion}")
        if e.response.status_code == 404:
            return {"status": "no_history", "message": "No existe historial crediticio aún (Situación 0)"}
        return {"status": "error", "message": f"HTTP {e.response.status_code}"}
    except httpx.TimeoutException:
        print("BCRA timeout")
        return {"status": "bcra_down", "message": "Timeout - BCRA lento o en mantenimiento"}
    except Exception as e:
        print(f"BCRA error: {str(e)}")
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

    # Safe situation extraction (real structure: results -> periodos -> entidades -> situacion)
    situacion = 0
    if bcra_data.get("status") == "success":
        try:
            periodos = bcra_data["deudas"].get("results", {}).get("periodos", [])
            if periodos:
                entidades = periodos[0].get("entidades", [])
                if entidades:
                    situacion = entidades[0].get("situacion", 0)
        except Exception:
            pass  # fallback to 0

    # ==================== BOGA CRIOLLO RESPONSES ====================
    if bcra_data.get("status") == "success":
        status_desc = {
            1: "estás al día y todo en orden",
            2: "tenés seguimiento especial (riesgo bajo)",
            3: "tenés problemas (riesgo medio)",
            4: "estás en insolvencia (alto riesgo)",
            5: "la deuda es irrecuperable"
        }.get(situacion, "la situación requiere atención")

        response_text = f"""🇦🇷 Che, ya consulté tu situación en el BCRA.

Tu situación actual es **{situacion}** — {status_desc}.

**Qué significa esto (simple):**
- Situación 1: Todo bien, pagás al día.
- 2-3: Hay moras, los bancos miran con lupa.
- 4-5: Hay que actuar rápido (negociar o pagar).

**Próximos pasos recomendados:**
1. Ver exactamente qué entidad te reportó.
2. Negociar/refinanciar la deuda.
3. Pedir el levantamiento una vez saldada (tienen 10 días hábiles).

¿Querés que te arme el plan completo y personalizado para mejorar o salir?"""

    elif bcra_data.get("status") == "no_history":
        response_text = """Tranca, no tenés deudas reportadas (Situación 0). 
Sos un "fantasma" para los bancos todavía. 
Esto es bueno a corto plazo, pero si querés pedir crédito pronto, te conviene empezar a construir historial positivo (tarjetas prepagas, billeteras virtuales, etc.).

¿Querés tips rápidos para armar historial?"""

    elif bcra_data.get("status") == "bcra_down":
        response_text = """Uy, el BCRA está lento o en mantenimiento ahora mismo. 
Ya anoté tu consulta. Probá de nuevo en 5-10 minutos, che. Estoy arriba del tema."""

    else:
        response_text = """Hubo un problemita técnico del lado del BCRA (el sistema público es medio inestable). 
Probá de nuevo en unos minutos. Si sigue fallando, avisame y vemos alternativas."""

    return {
        "response": response_text,
        "situacion": situacion,
        "bcra_status": bcra_data.get("status"),
        "identificacion": ident
    }

@app.get("/")
async def root():
    return {
        "app": "miboga - Tu boga digital para el BCRA",
        "status": "running",
        "version": "0.3.0 (robust BCRA)",
        "chat_endpoint": f"{APP_URL}/chat",
        "message": "Probá POST /chat con tu DNI o CUIT (8 o 11 dígitos)"
    }
