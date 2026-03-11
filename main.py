# main.py
import os
from typing import Optional
from fastapi.responses import PlainTextResponse, Response
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, condecimal
import httpx
from dotenv import load_dotenv
from datetime import datetime, date
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

app = FastAPI(title="Gastos API", version="1.0.0")

class GastoIn(BaseModel):
    nombre_comercio: str = Field(..., min_length=1)
    valor: condecimal(gt=0)
    external_id: Optional[str] = None
    reparto: Optional[int] = None

class GastoAdd(BaseModel):
    nombre_comercio: str
    valor: float
    mi_parte: Optional[float] = None
    categoria: Optional[str] = None

class GastoEdit(BaseModel):
    nombre_comercio: str
    valor: float
    mi_parte: Optional[float] = None
    categoria: Optional[str] = None


# ================================
#  HEALTHCHECK
# ================================
@app.get("/health")
async def health():
    return PlainTextResponse("OK")

@app.head("/health")
async def health_head():
    return Response(status_code=200)

# ================================
#  WEBHOOK (ATAJO)
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

    payload = {
        "nombre_comercio": nombre,
        "valor": amount,
        "mi_parte": mi_parte,
        "categoria": None
    }

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)

    return {"ok": True, "inserted": r.json()[0]}


# ================================
#  AÑADIR GASTO MANUAL
# ================================
@app.post("/gastos/add")
async def add_gasto(gasto: GastoAdd):
    nombre = gasto.nombre_comercio.strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="nombre_comercio vacío")

    amount = float(gasto.valor)
    mi_parte = float(gasto.mi_parte) if gasto.mi_parte is not None else amount

    payload = {
        "nombre_comercio": nombre,
        "valor": amount,
        "mi_parte": mi_parte,
        "categoria": gasto.categoria
    }

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)

    return {"ok": True, "inserted": r.json()[0]}


# ================================
#  PANEL HTML
# ================================
templates = Jinja2Templates(directory="templates")

@app.get("/panel")
async def panel(request: Request):
    return templates.TemplateResponse("panel.html", {"request": request})


# ================================
#  DASHBOARD-DATA con FILTROS
# ================================
@app.get("/dashboard-data")
async def dashboard_data(
    categoria: Optional[str] = None,
    comercio: Optional[str] = None,
    desde: Optional[str] = None,
    hasta: Optional[str] = None
):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
    }

    params = {"select": "*", "order": "created_at.desc"}

    filters = []

    if categoria:
        filters.append(f"categoria=eq.{categoria}")

    if comercio:
        filters.append(f"nombre_comercio=ilike.*{comercio}*")

    if desde:
        filters.append(f"created_at=gte.{desde}")

    if hasta:
        filters.append(f"created_at=lte.{hasta}")

    if filters:
        params["and"] = ",".join(filters)

    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers, params=params)

    gastos = r.json()

    # Categoría automática si falta
    for g in gastos:
        if not g.get("categoria"):
            g["categoria"] = categorizar(g["nombre_comercio"])

    def my_amount(g):
        return float(g.get("mi_parte") or g.get("valor") or 0)

    now = datetime.utcnow()
    this_month = now.month
    this_year = now.year

    def parse_dt(s: str):
        return parse_date(s)

    # Total filtrado del mes
    total_mes = 0.0
    for g in gastos:
        dt = parse_dt(g["created_at"])
        if dt.month == this_month and dt.year == this_year:
            total_mes += my_amount(g)

    # Suma por categoría
    cat_totals = {}
    for g in gastos:
        cat = g["categoria"]
        cat_totals[cat] = cat_totals.get(cat, 0) + my_amount(g)

    # DATOS PARA GRÁFICA MENSUAL
    mensual = {m: 0 for m in range(1, 13)}
    for g in gastos:
        dt = parse_dt(g["created_at"])
        mensual[dt.month] += my_amount(g)

    # DATOS PARA GRÁFICA DIARIA
    diario = {}
    for g in gastos:
        dt = parse_dt(g["created_at"])
        day = dt.day
        diario[day] = diario.get(day, 0) + my_amount(g)

    return {
        "gastos": gastos,
        "total_mes": round(total_mes, 2),
        "categorias": {k: round(v, 2) for k, v in cat_totals.items()},
        "mensual": mensual,
        "diario": diario
    }


# ================================
#  BORRAR
# ================================
@app.post("/gastos/delete/{gasto_id}")
async def borrar_gasto(gasto_id: int):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{gasto_id}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Prefer": "return=minimal"
    }

    async with httpx.AsyncClient() as client:
        await client.delete(url, headers=headers)

    return {"ok": True}


# ================================
#  EDITAR GASTO
# ================================
@app.post("/gastos/edit/{gasto_id}")
async def editar_gasto(gasto_id: int, gasto: GastoEdit):
    payload = {
        "nombre_comercio": gasto.nombre_comercio.strip(),
        "valor": float(gasto.valor),
        "mi_parte": float(gasto.mi_parte) if gasto.mi_parte is not None else None,
        "categoria": gasto.categoria
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

    return r.json()[0]