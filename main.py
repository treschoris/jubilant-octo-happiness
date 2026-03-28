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

# ==================== BCRA LOOKUP WITH RETRIES ====================
def normalize_identificacion(ident: str) -> str:
    cleaned = re.sub(r"\D", "", ident)
    if len(cleaned) not in (8, 11):
        raise ValueError("El DNI o CUIT debe tener 8 u 11 dígitos")
    return cleaned

async def get_bcra_data(identificacion: str) -> Dict:
    print(f"🔍 Consultando BCRA para: {identificacion}")
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                deudas_resp = await client.get(
                    f"https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas/{identificacion}"
                )
                deudas_resp.raise_for_status()
                deudas = deudas_resp.json()

                cheques = {}
                try:
                    cheques_resp = await client.get(
                        f"https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas/ChequesRechazados/{identificacion}"
                    )
                    if cheques_resp.status_code == 200:
                        cheques = cheques_resp.json()
                except Exception:
                    pass

            print(f"✅ BCRA success for {identificacion} (attempt {attempt+1})")
            return {
                "status": "success",
                "identificacion": identificacion,
                "deudas": deudas,
                "cheques": cheques,
                "timestamp": datetime.utcnow().isoformat()
            }

        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            print(f"BCRA error (attempt {attempt+1}/{max_retries+1}): {e}")
            if attempt == max_retries:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404:
                    return {"status": "no_history", "message": "No existe historial crediticio aún"}
                return {"status": "bcra_down", "message": "Sistema del BCRA temporalmente inestable"}
            await asyncio.sleep(1.5)

        except Exception as e:
            print(f"BCRA unexpected error: {str(e)}")
            if attempt == max_retries:
                return {"status": "error", "message": str(e)}
            await asyncio.sleep(1.5)

    return {"status": "error", "message": "Error desconocido"}

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
        return {"response": "El DNI o CUIT debe tener 8 u 11 dígitos. Probá de nuevo sin puntos ni guiones."}

    bcra_data = await get_bcra_data(ident)

    situacion = 0
    if bcra_data.get("status") == "success":
        try:
            periodos = bcra_data["deudas"].get("results", {}).get("periodos", [])
            if periodos:
                entidades = periodos[0].get("entidades", [])
                if entidades:
                    situacion = int(entidades[0].get("situacion", 0))
        except Exception:
            pass

    # ==================== BOGA RESPONSES ====================
    if bcra_data.get("status") == "success":
        if situacion == 1:
            response_text = f"""🇦🇷 ¡Excelente noticia, che! 

Tu situación en el BCRA es **1** — estás completamente al día y todo en orden. 👍

Esto significa que pagás todo puntualmente y no tenés ninguna mora reportada.

**Consejos de tu boga:**
1. Mantené los pagos al día.
2. Revisá mensualmente.
3. Si querés crecer, podés pedir mejores tasas.

¿Querés que te avise gratis el mes que viene?"""

        elif situacion in (2, 3):
            response_text = f"""🇦🇷 Che, tu situación actual es **{situacion}**.

Tenés alguna mora, pero no es crítica.

**Próximos pasos:**
1. Ver qué entidad te reportó.
2. Negociar o regularizar.
3. Pedir levantamiento una vez pagada.

¿Querés un plan personalizado?"""

        else:  # 4 or 5
            response_text = f"""🇦🇷 Mirá, tu situación es **{situacion}** — la cosa está complicada.

No te asustes, pero hay que actuar rápido.

¿Querés que te arme un plan concreto?"""

    elif bcra_data.get("status") == "no_history":
        response_text = """Tranca, no tenés deudas reportadas (Situación 0). 

¿Querés tips para construir historial?"""

    else:
        response_text = """El sistema del BCRA está temporalmente inestable (pasa bastante seguido). 
Ya me estoy ocupando — probá de nuevo en 10-15 segundos."""

    return {
        "response": response_text,
        "situacion": situacion,
        "bcra_status": bcra_data.get("status"),
        "identificacion": ident
    }
