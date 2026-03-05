# main.py
import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, condecimal
import httpx
from dotenv import load_dotenv


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
    return {"ok": True}


@app.post("/webhook/gasto")
async def webhook_gasto(
    gasto: GastoIn,
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    # 1) Autenticación simple del Atajo
    if x_api_key != SHORTCUT_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 2) Normalización mínima
    nombre = gasto.nombre_comercio.strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="nombre_comercio vacío")

    payload = {
        "nombre_comercio": nombre,
        "valor": float(gasto.valor),
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


