# main.py
import os
from typing import Optional
from fastapi.responses import PlainTextResponse
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, condecimal
import httpx
from dotenv import load_dotenv
from datetime import datetime

# Categorización automática según nombre del comercio
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

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "gastos")
SHORTCUT_API_KEY = os.getenv("SHORTCUT_API_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not SHORTCUT_API_KEY:
    # En producción mejor loguear y salir; aquí lo dejamos explícito.
    print("⚠️ Faltan variables de entorno (SUPABASE_URL / SUPABASE_SERVICE_KEY / SHORTCUT_API_KEY).")

app = FastAPI(title="Gastos API", version="1.0.0")


class GastoIn(BaseModel):
    nombre_comercio: str = Field(..., min_length=1, description="Nombre del comercio")
    valor: condecimal(gt=0) = Field(..., description="Importe > 0")
    external_id: Optional[str] = Field(None, description="ID externo opcional para evitar duplicados")
    reparto: Optional[int] = Field(None, description="Número de personas: 1, 2, 3… (None o 0 = todo para mí)")


@app.get("/gastos")
async def listar_gastos(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }

    params = {
        "select": "*",
        "order": "created_at.desc",
        "limit": limit,
        "offset": offset,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers, params=params)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()


@app.get("/health")
async def health():
    return PlainTextResponse("OK", status_code=200)


@app.post("/webhook/gasto")
async def webhook_gasto(
    gasto: GastoIn,
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    # 1) Autenticación simple del Atajo
    if x_api_key != SHORTCUT_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 2) Normalización mínima
    
   
    # Calcular "amount" como float
    amount = float(gasto.valor)

    # reparto puede venir None, 0, 1, 2, 3...
    reparto = gasto.reparto if gasto.reparto is not None else 1

    try:
        reparto_int = int(reparto)
    except Exception:
        reparto_int = 1

    # Cálculo correcto de mi_parte
    # 0 o negativo → todo para ti
    if reparto_int <= 0:
        mi_parte = amount
    else:
        mi_parte = amount / reparto_int

    nombre = gasto.nombre_comercio.strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="nombre_comercio vacío")

    payload = {
        "nombre_comercio": nombre,
        "valor": amount,
        "mi_parte": mi_parte
    }
    if gasto.external_id:
        payload["external_id"] = gasto.external_id.strip()

    # 3) Inserción en Supabase vía REST (PostgREST)
    # Supabase expone /rest/v1/ como API autogenerada 
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        # PostgREST: devolver el registro insertado 
        "Prefer": "return=representation",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers=headers)

    if r.status_code >= 400:
        # Pasamos el error tal cual para depurar rápido
        raise HTTPException(status_code=r.status_code, detail=r.text)

    # r.json() devuelve lista con el registro insertado (cuando return=representation)
    data = r.json()
    inserted = data[0] if isinstance(data, list) and data else data

    return {"ok": True, "inserted": inserted}


# Panel con gastos
templates = Jinja2Templates(directory="templates")

@app.get("/panel")
async def panel(request: Request):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    params = {
        "select": "*",
        "order": "created_at.desc"
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers, params=params)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    gastos = r.json()

    # Calcular total gastado
    total = sum(g["valor"] for g in gastos if g["valor"] is not None)

    return templates.TemplateResponse(
        "panel.html",
        {
            "request": request,
            "gastos": gastos,
            "total": f"{total:.2f}"
        }
    )

@app.get("/dashboard-data")
async def dashboard_data():
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    params = {"select": "*", "order": "created_at.desc"}

    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers, params=params)

    gastos = r.json()

    # Añadimos categoría automática
    for g in gastos:
        g["categoria"] = categorizar(g["nombre_comercio"])

    # Total del mes
       
    now = datetime.utcnow()
    this_month = now.month
    this_year = now.year

    def my_amount(row):
        v = row.get("mi_parte")
        if v is None:
            v = row.get("valor", 0)
        return float(v or 0)

    total_mes = sum(
        my_amount(g)
        for g in gastos
        if g.get("created_at") and datetime.fromisoformat(g["created_at"].replace("Z","")).month == this_month
           and datetime.fromisoformat(g["created_at"].replace("Z","")).year == this_year
    )

    # Totales por categoría (también con mi_parte)
    cat_totals = {}
    for g in gastos:
        cat = g["categoria"]
        cat_totals[cat] = cat_totals.get(cat, 0.0) + my_amount(g)

    return {
        "gastos": gastos,
        "total_mes": round(total_mes, 2),
        "categorias": {k: round(v, 2) for k, v in cat_totals.items()},
    }


@app.post("/gastos/delete/{gasto_id}")
async def borrar_gasto(gasto_id: int):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{gasto_id}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Prefer": "return=minimal"
    }

    async with httpx.AsyncClient() as client:
        r = await client.delete(url, headers=headers)

    return {"ok": True}

class GastoEdit(BaseModel):
    nombre_comercio: str
    valor: float

@app.post("/gastos/edit/{gasto_id}")
async def editar_gasto(gasto_id: int, gasto: GastoEdit):
    payload = {
        "nombre_comercio": gasto.nombre_comercio.strip(),
        "valor": gasto.valor,
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


