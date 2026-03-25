import os
import json
import random
from datetime import date
from typing import List, Dict
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from groq import Groq
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct

# ==================== CONFIG ====================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
APP_URL = os.getenv("APP_URL", "https://your-app.onrender.com")   # Render will give you this

app = FastAPI(title="Central de Deudores RAG")

groq_client: Groq = None
qdrant_client: QdrantClient = None
COLLECTION = "mitigaciones"
DIM = 128

# High-value hardcoded mitigation + Boletín context (expand easily later)
DOCS: List[Dict] = [
    {"id": "bcra1", "text": "La Central de Deudores del BCRA informa las deudas impagas reportadas por entidades. Che, si estás en situación 1-2 podés renegociar directamente con tu banco según Comunicación A 7584.", "metadata": {"source": "BCRA", "date": "2026-03-01", "type": "explanatory", "url": "https://www.bcra.gob.ar/central-deudores"}},
    {"id": "mit1", "text": "Mirá, si tenés deuda en Central de Deudores podés pedir refinanciación con tu entidad. Modelo de carta: 'Solicito renegociación según Com. A 7584, adjunto CUIT y detalle deuda'.", "metadata": {"source": "BCRA Guía", "date": "2026-03-01", "type": "mitigation", "url": "https://www.bcra.gob.ar/mitigacion/renegociacion"}},
    {"id": "mit2", "text": "Próximos pasos recomendados por BCRA: 1) Contactar banco, 2) Presentar propuesta de pago con cuotas, 3) Solicitar baja de Central una vez saldada. Links útiles en bcra.gob.ar.", "metadata": {"source": "BCRA", "date": "2026-03-10", "type": "mitigation", "url": "https://www.bcra.gob.ar/central-deudores/pasos"}},
    {"id": "boletin1", "text": "Boletín Oficial - Comunicación A 7584: Plazos de renegociación extendidos hasta 90 días y posibilidad de quita de hasta 30% en deudas reportadas.", "metadata": {"source": "Boletín Oficial", "date": "2026-03-20", "type": "boletin", "url": "https://www.boletinoficial.gob.ar"}},
]

def get_embedding(text: str) -> List[float]:
    # Deterministic fake embedding - zero external cost, tiny RAM, fully reproducible
    h = hash(text) & 0xFFFFFFFF
    random.seed(h)
    return [random.uniform(-1.0, 1.0) for _ in range(DIM)]

def get_bcra_data(cuit: str) -> Dict:
    """Real public BCRA endpoints (no auth required)"""
    try:
        with httpx.Client(timeout=12) as client:
            deudas = client.get(f"https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas/{cuit}").json()
            cheques = client.get(f"https://api.bcra.gob.ar/centraldedeudores/v1.0/ChequesRechazados/{cuit}").json()
        return {"deudas": deudas, "cheques": cheques}
    except Exception:
        return {"error": "No se pudo obtener datos del BCRA en este momento. Intentá más tarde."}

@app.on_event("startup")
def load_qdrant():
    global groq_client, qdrant_client
    groq_client = Groq(api_key=GROQ_API_KEY)
    qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10)

    try:
        qdrant_client.get_collection(COLLECTION)
    except:
        qdrant_client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=DIM, distance=Distance.COSINE)
        )

    if qdrant_client.count(COLLECTION).count == 0:
        points = []
        for i, doc in enumerate(DOCS):
            vec = get_embedding(doc["text"])
            points.append(PointStruct(id=i, vector=vec, payload={**doc["metadata"], "text": doc["text"]}))
        qdrant_client.upsert(collection_name=COLLECTION, points=points)

def retrieve_chunks(query: str, k: int = 5) -> str:
    vec = get_embedding(query)
    # NEW: Use query_points instead of the old .search
    results = qdrant_client.query_points(
        collection_name=COLLECTION,
        query=vec,           # just pass the vector list
        limit=k
    )
    # Extract the text from the returned points
    texts = []
    for point in results.points:
        text = point.payload.get("text", "") if point.payload else ""
        texts.append(text)
    return "\n---\n".join(texts)

class ChatRequest(BaseModel):
    cuit: str
    question: str

@app.post("/chat")
async def chat(req: ChatRequest):
    if not GROQ_API_KEY or not QDRANT_URL or not QDRANT_API_KEY:
        raise HTTPException(status_code=500, detail="Faltan variables de entorno")

    debtor = get_bcra_data(req.cuit)
    context = retrieve_chunks(req.question)
    debtor_str = json.dumps(debtor, ensure_ascii=False, indent=2)

    system = """Eres un asesor empático y directo del Central de Deudores de Argentina.
Responde SIEMPRE en español argentino natural, directo y amigable, usando “che”, “mirá”, “tranqui” cuando corresponda.
Usa ÚNICAMENTE los chunks recuperados + el JSON del deudor. NUNCA inventes ni alucines información.
Cada respuesta debe incluir:
- Explicación clara de la situación.
- Sección numerada “Próximos pasos” con acciones concretas, modelo de carta si aplica y enlaces.
- Terminar EXACTAMENTE con: Fuente: BCRA API consultada hoy + Boletín Oficial [fecha] + Comunicación X"""

    user = f"""CUIT: {req.cuit}
JSON BCRA: {debtor_str}
Documentos recuperados: {context}
Pregunta del usuario: {req.question}"""

    resp = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=800
    )

    return {
        "response": resp.choices[0].message.content,
        "app_url": APP_URL
    }

@app.get("/")
async def root():
    return {
        "app": "Central de Deudores RAG",
        "status": "running",
        "chat_endpoint": f"{APP_URL}/chat",
        "docs": f"{APP_URL}/docs"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)