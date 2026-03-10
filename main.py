# main.py
import os
from typing import Optional
from fastapi.responses import PlainTextResponse, Response
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, condecimal
import httpx
from dotenv import load_dotenv
from datetime import datetime
from dateutil.parser import parse as parse_date

# ================================
#  CATEGORIZACIÓN AUTOMÁTICA
# ================================
CATEGORIZACION_SUPERMERCADOS = ["MERC", "ALIMER", "CARREFO", "LIDL", "DIA", "ALCAMPO"]
CATEGORIZACION_GASOLINA = ["REPSOL", "CEPSA", "SHELL", "BP"]
CATEGORIZACION_RESTAURANTES = ["BAR", "REST", "CAFETER", "PIZZA", "BURGER", "MC", "KFC"]

def categorizar(nombre: str) -> str:
    if not nombre:
        return "Otros"
    nombre = nombre.upper()

    for w in CATEGORIZACION_SUPERMERCADOS:
        if w in nombre:
            return "Supermercado"

    for w in CATEGORIZACION_GASOLINA:
        if w in nombre:
            return "Gasolina"

    for w in CATEGORIZACION_RESTAURANTES:
        if w in nombre:
            return "Restauración"

    return "Otros"


# ================================
#   ENV / CONFIG
# ================================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "gastos")
SHORTCUT_API_KEY = os.getenv("SHORTCUT_API_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not SHORTCUT_API_KEY:
    print("⚠️ Faltan variables de entorno necesarias.")


# ================================
#  FASTAPI
# ================================
app = FastAPI(title="Gastos API", version="1.0.0")


class GastoIn(BaseModel):
    nombre_comercio: str = Field(..., min_length=1)
    valor: condecimal(gt=0)
    external_id: Optional[str] = None
    reparto: Optional[int] = Field(None, description="1, 2, 3… (None/0 = todo para mí)")


# ================================
#  LISTAR GASTOS
# ================================
@app.get("/gastos")
async def listar_gastos(limit: int = Query(50), offset: int = Query(0)):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    params = {"select": "*", "order": "created_at.desc", "limit": limit, "offset": offset}

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers, params=params)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()


# ================================
#  HEALTHCHECK (GET + HEAD)
# ================================
@app.get("/health")
async def health():
    return PlainTextResponse("OK")

@app.head("/health")
async def health_head():
    return Response(status_code=200)


# ================================
#  WEBHOOK DEL ATAJO
# ================================
@app.post("/webhook/gasto")
async def webhook_gasto(
    gasto: GastoIn,
    x_api_key: str = Header(default="", alias="X-API-Key")
):
    if x_api_key != SHORTCUT_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    amount = float(gasto.valor)

    reparto = gasto.reparto if gasto.reparto is not None else 1
    try:
        reparto_int = int(reparto)
    except:
        reparto_int = 1

    mi_parte = amount if reparto_int <= 0 else amount / reparto_int

    nombre = gasto.nombre_comercio.strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="nombre_comercio vacío")

    payload = {"nombre_comercio": nombre, "valor": amount, "mi_parte": mi_parte}
    if gasto.external_id:
        payload["external_id"] = gasto.external_id.strip()

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    data = r.json()
    return {"ok": True, "inserted": data[0]}


# ================================
#  PANEL HTML
# ================================
templates = Jinja2Templates(directory="templates")

@app.get("/panel")
async def panel(request: Request):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    params = {"select": "*", "order": "created_at.desc"}

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers, params=params)

    gastos = r.json()

    def my_amount(g):
        return float(g.get("mi_parte") or g.get("valor") or 0)

    total = sum(my_amount(g) for g in gastos)

    return templates.TemplateResponse("panel.html", {"request": request, "gastos": gastos, "total": f"{total:.2f}"})


# ================================
#  DASHBOARD DATA (GRÁFICOS)
# ================================
@app.get("/dashboard-data")
async def dashboard_data():
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    params = {"select": "*", "order": "created_at.desc"}

    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers, params=params)

    gastos = r.json()

    # Categoría automática
    for g in gastos:
        g["categoria"] = categorizar(g["nombre_comercio"])

    # Fecha actual
    now = datetime.utcnow()
    this_month = now.month
    this_year = now.year

    def my_amount(g):
        return float(g.get("mi_parte") or g.get("valor") or 0)

    # Total del mes (tu parte)
    total_mes = 0.0
    for g in gastos:
        if not g.get("created_at"):
            continue
        dt = parse_date(g["created_at"])  # <-- Robustísimo
        if dt.month == this_month and dt.year == this_year:
            total_mes += my_amount(g)

    # Totales por categoría
    cat_totals = {}
    for g in gastos:
        cat = g["categoria"]
        cat_totals[cat] = cat_totals.get(cat, 0) + my_amount(g)

    return {
        "gastos": gastos,
        "total_mes": round(total_mes, 2),
        "categorias": {k: round(v, 2) for k, v in cat_totals.items()},
    }


# ================================
#  BORRAR
# ================================
@app.post("/gastos/delete/{gasto_id}")
async def borrar_gasto(gasto_id: int):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{gasto_id}"
    headers = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Prefer": "return=minimal"}

    async with httpx.AsyncClient() as client:
        r = await client.delete(url, headers=headers)

    return {"ok": True}


# ================================
#  EDITAR GASTO
# ================================
class GastoEdit(BaseModel):
    nombre_comercio: str
    valor: float
    mi_parte: Optional[float] = None

@app.post("/gastos/edit/{gasto_id}")
async def editar_gasto(gasto_id: int, gasto: GastoEdit):
    payload = {
        "nombre_comercio": gasto.nombre_comercio.strip(),
        "valor": float(gasto.valor),
        "mi_parte": float(gasto.mi_parte) if gasto.mi_parte is not None else None
    }

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{gasto_id}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    async with httpx.AsyncClient() as client:
        r = await client.patch(url, json=payload, headers=headers)

    updated = r.json()[0]
    updated["categoria"] = categorizar(updated["nombre_comercio"])
    return updated
