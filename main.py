import os
import re
import asyncio
from datetime import datetime
from typing import Dict

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles   # ← NEW LINE
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

            # boga responses (same as before)
            if situacion == 1:
                response_text = f"""🇦🇷 ¡Excelente noticia, che! 

Tu situación en el BCRA es **1** — estás completamente al día y todo en orden. 👍

Esto significa que pagás todo puntualmente y no tenés ninguna mora reportada. Los bancos te ven como un buen cliente.

**Consejos de tu boga para mantenerte en esta zona:**
1. Seguís así: mantené los pagos al día.
2. Revisá mensualmente (el BCRA se actualiza todos los meses).
3. Si querés crecer tu crédito, podés pedir tarjetas o préstamos con mejores tasas.

¿Querés que te avise gratis el mes que viene cuando el BCRA actualice tu situación?"""
            elif situacion in (2, 3):
                response_text = f"""🇦🇷 Che, tu situación actual es **{situacion}**.

Esto significa que tenés alguna mora reportada, pero todavía no es crítica. Los bancos la miran con atención.

**Próximos pasos recomendados:**
1. Ver exactamente qué entidad te reportó.
2. Negociar o regularizar esa deuda lo antes posible.
3. Una vez pagada, pedir el levantamiento (tienen 10 días hábiles)."""
            else:
                response_text = f"""🇦🇷 Mirá, tu situación es **{situacion}** — la cosa está complicada.

Esto suele significar atrasos importantes o deuda en alto riesgo. No te asustes, pero hay que actuar.

**Qué te recomiendo hacer:**
1. Identificar exactamente qué deudas te tienen en esta situación.
2. Negociar con la entidad.
3. Una vez acordado el pago, pedir el certificado de libre deuda."""

            await save_report(ident, bcra_data, response_text, situacion, "success")
            return

        elif bcra_data.get("status") == "no_history":
            response_text = """Tranca, no tenés deudas reportadas (Situación 0). 
Sos un "fantasma" para el sistema financiero todavía. 

¿Querés tips rápidos para construir historial positivo?"""
            await save_report(ident, bcra_data, response_text, 0, "success")
            return

        await asyncio.sleep(8)

    # Fallback after retries
    response_text = """El BCRA sigue lento hoy. Ya guardé tu consulta y sigo intentando en segundo plano. Te aviso apenas tenga tu situación."""
    await save_report(ident, {}, response_text, 0, "error")

# ==================== REQUEST MODEL ====================
class ChatRequest(BaseModel):
    identificacion: str
    channel: str = "web"

# ==================== API ENDPOINTS ====================
@app.post("/chat")
async def chat(req: ChatRequest, background_tasks: BackgroundTasks):
    try:
        ident = normalize_identificacion(req.identificacion)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(process_bcra_lookup, ident)
    return {"status": "processing", "identificacion": ident}

@app.get("/status/{identificacion}")
async def get_status(identificacion: str):
    try:
        ident = normalize_identificacion(identificacion)
        result = supabase.table("bcra_reports").select("*")\
            .eq("identificacion", ident).order("created_at", desc=True).limit(1).execute()
        if not result.data:
            return {"status": "processing"}
        row = result.data[0]
        if row["status"] == "success":
            return {"status": "ready", "response": row["response_text"]}
        return {"status": "processing"}
    except Exception:
        return {"status": "error", "message": "Error interno"}

@app.get("/report/{identificacion}")
async def report_page(identificacion: str):
    try:
        ident = normalize_identificacion(identificacion)
        result = supabase.table("bcra_reports").select("*")\
            .eq("identificacion", ident).order("created_at", desc=True).limit(1).execute()
        if not result.data or result.data[0]["status"] != "success":
            return {"message": "Reporte aún en proceso. Refrescá en unos segundos."}
        row = result.data[0]
        html = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head><meta charset="UTF-8"><title>Tu reporte BCRA - miboga</title>
        <script src="https://cdn.tailwindcss.com"></script></head>
        <body class="bg-slate-50">
        <div class="max-w-3xl mx-auto px-6 py-12">
            <h1 class="text-4xl font-bold text-center mb-8">🇦🇷 Tu reporte BCRA</h1>
            <div class="bg-white rounded-3xl shadow-xl p-8">
                <div class="text-lg leading-relaxed">{row["response_text"]}</div>
                <div class="mt-10 text-sm border-t pt-6">
                    <p class="font-mono">ID: {ident}</p>
                    <p class="mt-4 text-emerald-600">Guardado • {row.get('created_at','')[:10]}</p>
                </div>
            </div>
        </div>
        </body></html>
        """
        return html
    except Exception:
        return "<h1>Algo salió mal. Probá de nuevo.</h1>"

# ==================== SERVE index.html AT ROOT (IMPORTANT) ====================
app.mount("/", StaticFiles(directory=".", html=True), name="static")   # ← THIS RESTORES YOUR WEBSITE

print("🚀 miboga backend with background tasks + static HTML loaded")
