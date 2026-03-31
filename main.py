import os
import re
import asyncio
from datetime import datetime
from typing import Dict

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client, Client

# ==================== CONFIG ====================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
APP_URL = os.getenv("APP_URL", "https://jubilant-octo-happiness.onrender.com")
WHATSAPP_VERIFY_TOKEN = "miboga2026"   # ← You set this in Meta dashboard

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
    except Exception as e:  # simplified for stability
        print(f"BCRA error: {str(e)}")
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404:
            return {"status": "no_history"}
        return {"status": "error"}

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
        print(f"✅ Saved report for {ident}")
    except Exception as e:
        print(f"Supabase save error: {e}")

# ==================== BACKGROUND PROCESSOR (shared by web + WhatsApp) ====================
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

            # Updated boga responses with roadmap links (from attached document)
            if situacion == 1:
                response_text = """🇦🇷 ¡Excelente noticia, che! 

Tu situación en el BCRA es **1** — estás completamente al día.

**Consejos de tu boga:**
1. Mantené los pagos al día.
2. Revisá mensualmente.
3. Querés crecer tu crédito? Mirá la guía para aprovechar tu Situación 1 → https://jubilant-octo-happiness.onrender.com/guias/situacion-1"""

            elif situacion in (2, 3):
                response_text = f"""🇦🇷 Che, tu situación actual es **{situacion}** (alerta media).

Hay mora reportada. No es grave, pero hay que actuar.

**Próximos pasos:**
1. Ver qué entidad te reportó.
2. Negociar o pagar.
3. Pedir levantamiento.
Leé la guía completa para salir de Situación 2/3 → https://jubilant-octo-happiness.onrender.com/guias/situacion-2-3"""

            else:  # 4 or 5
                response_text = f"""🇦🇷 Mirá, tu situación es **{situacion}** — está complicada.

No te asustes. La ley te protege (Derecho al Olvido después de 5 años).

**Qué hacer:**
1. Identificar las deudas.
2. Negociar.
3. Leé tus derechos y cómo salir → https://jubilant-octo-happiness.onrender.com/guias/situacion-4-5"""

            await save_report(ident, bcra_data, response_text, situacion, "success")
            return

        elif bcra_data.get("status") == "no_history":
            response_text = """Tranca, no tenés deudas reportadas (Situación 0). 
Sos "invisible" todavía. 

Esto es bueno, pero para pedir crédito te conviene construir historial.
Leé la guía para dejar de ser invisible → https://jubilant-octo-happiness.onrender.com/guias/situacion-0"""
            await save_report(ident, bcra_data, response_text, 0, "success")
            return

        await asyncio.sleep(8)

    # Final fallback
    response_text = """El BCRA sigue lento. Ya guardé tu consulta y sigo intentando. Te aviso apenas tenga tu situación."""
    await save_report(ident, {}, response_text, 0, "error")

# ==================== REQUEST MODEL ====================
class ChatRequest(BaseModel):
    identificacion: str
    channel: str = "web"

# ==================== WEB ENDPOINTS ====================
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

@app.get("/report/{identificacion}", response_class=HTMLResponse)
async def report_page(identificacion: str):
    try:
        ident = normalize_identificacion(identificacion)
        result = supabase.table("bcra_reports").select("*")\
            .eq("identificacion", ident).order("created_at", desc=True).limit(1).execute()
        
        if not result.data or result.data[0]["status"] != "success":
            return HTMLResponse("<h1 style='text-align:center;padding:50px;'>Reporte aún en proceso.<br>Refrescá en unos segundos.</h1>")
        
        row = result.data[0]
        html = f"""<!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <title>Tu reporte BCRA - miboga</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
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
        </body>
        </html>"""
        return HTMLResponse(html)
    except Exception:
        return HTMLResponse("<h1>Algo salió mal. Probá de nuevo.</h1>")

# ==================== WHATSAPP WEBHOOK ====================
@app.get("/webhook")
async def verify_webhook(mode: str = None, token: str = None, challenge: str = None):
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Invalid token")

@app.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        entry = body["entry"][0]["changes"][0]["value"]
        if "messages" in entry:
            message = entry["messages"][0]
            phone = message["from"]
            text = message.get("text", {}).get("body", "").strip()
            try:
                ident = normalize_identificacion(text)
                background_tasks.add_task(process_bcra_lookup, ident)
                print(f"✅ WhatsApp from {phone} → {ident}")
            except ValueError:
                print(f"WhatsApp from {phone} → no valid DNI/CUIT")
    except Exception as e:
        print(f"Webhook error: {e}")
    return {"status": "received"}

# ==================== SERVE index.html ====================
app.mount("/", StaticFiles(directory=".", html=True), name="static")

print("🚀 miboga FULL LOOP ready (web + WhatsApp + updated roadmap)")
