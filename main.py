import os
import re
import asyncio
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

# ==================== BCRA LOOKUP WITH RETRIES ====================
def normalize_identificacion(ident: str) -> str:
    cleaned = re.sub(r"\D", "", ident)
    if len(cleaned) not in (8, 11):
        raise ValueError("El DNI o CUIT debe tener 8 o 11 dígitos")
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
            await asyncio.sleep(1.5)  # short backoff

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

# ==================== MAIN CHAT ENDPOINT ====================
@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        ident = normalize_identificacion(req.identificacion)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    bcra_data = await get_bcra_data(ident)

    # Safe situation extraction
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

    # ==================== SITUATION-BASED BOGA RESPONSES ====================
    if bcra_data.get("status") == "success":
        if situacion == 1:
            response_text = f"""🇦🇷 ¡Excelente noticia, che! 

Tu situación en el BCRA es **1** — estás completamente al día y todo en orden. 👍

Esto significa que pagás todo puntualmente y no tenés ninguna mora reportada. Los bancos te ven como un buen cliente.

**Consejos de tu boga para mantenerte en esta zona:**
1. Seguís así: mantené los pagos al día.
2. Revisá mensualmente (el BCRA se actualiza todos los meses).
3. Si querés crecer tu crédito, podés pedir tarjetas o préstamos con mejores tasas.

¿Querés que te avise gratis el mes que viene cuando el BCRA actualice tu situación? (Solo un mensaje por mes)

O decime y te doy tips para seguir fortaleciendo tu historial crediticio."""

        elif situacion in (2, 3):
            response_text = f"""🇦🇷 Che, tu situación actual es **{situacion}**.

Esto significa que tenés alguna mora reportada, pero todavía no es crítica. Los bancos la miran con atención.

**Próximos pasos recomendados:**
1. Ver exactamente qué entidad (banco o financiera) te reportó.
2. Negociar o regularizar esa deuda lo antes posible.
3. Una vez pagada, pedir el levantamiento (tienen 10 días hábiles para actualizar).

¿Querés que te arme un plan simple y personalizado para mejorar esto rápido?"""

        else:  # 4 or 5
            response_text = f"""🇦🇷 Mirá, tu situación es **{situacion}** — la cosa está complicada.

Esto suele significar atrasos importantes o deuda en alto riesgo. No te asustes, pero hay que actuar.

**Qué te recomiendo hacer:**
1. Identificar exactamente qué deudas te tienen en esta situación.
2. Negociar con la entidad (muchas aceptan quitas o planes).
3. Una vez acordado el pago, pedir el certificado de libre deuda.

¿Querés que analice los detalles y te arme un plan concreto para salir de esta? Estoy para ayudarte."""

    elif bcra_data.get("status") == "no_history":
        response_text = """Tranca, no tenés deudas reportadas (Situación 0). 
Sos un "fantasma" para el sistema financiero todavía. 

Esto es neutro: bueno porque no tenés problemas, pero puede complicarte si querés pedir crédito pronto.

¿Querés tips rápidos para empezar a construir un buen historial crediticio?"""

    elif bcra_data.get("status") == "bcra_down":
        response_text = """El sistema del BCRA está temporalmente inestable (pasa bastante seguido). 
Ya me estoy ocupando — probá de nuevo en 10-15 segundos con el botón de abajo."""

    else:
        response_text = """El sistema del BCRA está temporalmente inestable (pasa bastante seguido). 
Ya me estoy ocupando — probá de nuevo en 10-15 segundos."""

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
        "version": "0.5.0 (retries + transparent errors)",
        "message": "Probá POST /chat con tu DNI o CUIT"
    }
