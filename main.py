import os
import re
import asyncio
from datetime import datetime
from typing import Dict

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
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

# ==================== BCRA LOOKUP ====================
def normalize_identificacion(ident: str) -> str:
    cleaned = re.sub(r"\D", "", ident)
    if len(cleaned) not in (8, 11):
        raise ValueError("El DNI o CUIT debe tener 8 o 11 dígitos")
    return cleaned

async def get_bcra_data(identificacion: str) -> Dict:
    print(f"🔍 Consultando BCRA para: {identificacion}")
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
            return {"status": "no_history", "message": "No existe historial crediticio aún"}
        return {"status": "error", "message": f"HTTP {e.response.status_code}"}
    except httpx.TimeoutException:
        print("BCRA timeout")
        return {"status": "bcra_down", "message": "Timeout - BCRA lento"}
    except Exception as e:
        print(f"BCRA error: {str(e)}")
        return {"status": "error", "message": str(e)}

# ==================== SAVE TO SUPABASE ====================
async def save_report(ident: str, bcra_data: Dict, response_text: str, situacion: int, status: str = "success"):
    try:
        supabase.table("bcra_reports").upsert({
            "identificacion": ident,
            "full_data": bcra_data,
            "response_text": response_text,
            "situacion": situacion,
            "status": status,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        print(f"✅ Saved report for {ident} — status: {status}")
    except Exception as e:
        print(f"Supabase save error: {e}")

# ==================== BACKGROUND PROCESSOR ====================
async def process_bcra_lookup(ident: str):
    situacion = 0
    response_text = ""

    for attempt in range(5):
        bcra_data = await get_bcra_data(ident)

        if bcra_data.get("status") == "success":
            try:
                periodos = bcra_data["deudas"].get("results", {}).get("periodos", [])
                if periodos:
                    entidades = periodos[0].get("entidades", [])
                    if entidades:
                        situacion = int(entidades[0].get("situacion", 0))
            except Exception:
                pass

            if situacion == 1:
                response_text = """🇦🇷 ¡Excelente noticia, che! 

Tu situación en el BCRA es **1** — estás completamente al día y todo en orden. 👍

Esto significa que pagás todo puntualmente y no tenés ninguna mora reportada. Los bancos te ven como un buen cliente.

**Consejos de tu boga para mantenerte en esta zona:**
1. Seguís así: mantené los pagos al día.
2. Revisá mensualmente (el BCRA se actualiza todos los meses).
3. Si querés crecer tu crédito, podés pedir tarjetas o préstamos con mejores tasas.

¿Querés que te avise gratis el mes que viene cuando el BCRA actualice tu situación?"""
            elif situacion in (2
