"""Aplicación web: tablero, unidades, cartera, cotizador y asistente.

    uv run uvicorn flotilla.web:app --reload

DEMO_MODE=1 regenera la base al arrancar con la fecha de hoy como corte, la
vuelve a generar cada madrugada y limita las preguntas al asistente por
visitante y por día (cada pregunta cuesta tokens).
"""
import math
import os
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from flotilla import analitica, asistente, consultas, db, herramientas
from flotilla import finanzas as fin
from flotilla.cli import generar_base

RAIZ = Path(__file__).resolve().parent.parent
DEMO = os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes")
LIMITE_VISITANTE = int(os.environ.get("ASISTENTE_LIMITE_VISITANTE", "15"))
LIMITE_GLOBAL = int(os.environ.get("ASISTENTE_LIMITE_DIARIO", "300"))

@asynccontextmanager
async def _ciclo_de_vida(_app):
    if DEMO or not db.db_path().exists():
        regenerar()
    if DEMO:
        threading.Thread(target=_ciclo_nocturno, daemon=True).start()
    yield


app = FastAPI(title="Flotilla", docs_url=None, redoc_url=None, lifespan=_ciclo_de_vida)
app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")
plantillas = Jinja2Templates(directory=RAIZ / "templates")


# ─── Formato ─────────────────────────────────────────────────────────────────

def dinero(x, decimales=0):
    if x is None:
        return "-"
    signo = "-" if x < 0 else ""
    return f"{signo}${abs(x):,.{decimales}f}"


def miles(x):
    """$1.2 M / $845 k para indicadores grandes."""
    a = abs(x)
    signo = "-" if x < 0 else ""
    if a >= 1_000_000:
        return f"{signo}${a / 1_000_000:,.1f} M"
    if a >= 10_000:
        return f"{signo}${a / 1_000:,.0f} k"
    return dinero(x)


def pct(x, decimales=1):
    return "-" if x is None else f"{x * 100:.{decimales}f}%"


MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha_larga(iso):
    d = date.fromisoformat(iso) if isinstance(iso, str) else iso
    return f"{d.day} {MESES[d.month - 1]} {d.year}"


def mes_corto(ym):
    y, m = ym.split("-")
    return f"{MESES[int(m) - 1]} {y[2:]}"


plantillas.env.filters.update(dinero=dinero, miles=miles, pct=pct, fecha=fecha_larga, mes=mes_corto)


# ─── Base y caché ────────────────────────────────────────────────────────────

_version = {"n": 0}     # cambia cuando se regenera la base: invalida la caché


def _conexion():
    return consultas.conexion_lectura()


@lru_cache(maxsize=8)
def _datos(version):
    conn = _conexion()
    try:
        return {
            "resumen": analitica.resumen(conn),
            "unidades": analitica.rentabilidad_unidades(conn, 12),
            "categorias": analitica.rentabilidad_por(conn, "categoria", 12),
            "vehiculos": analitica.rentabilidad_por(conn, "vehiculo", 12),
            "clientes": analitica.rentabilidad_por(conn, "cliente", 12),
            "cartera": analitica.cartera_vencida(conn),
            "serie": analitica.serie_mensual(conn, 18),
        }
    finally:
        conn.close()


def datos():
    return _datos(_version["n"])


def regenerar():
    info = generar_base(corte=date.today())
    _version["n"] += 1
    _uso.clear()
    return info


def _ciclo_nocturno():
    while True:
        ahora = datetime.now()
        siguiente = datetime.combine(ahora.date() + timedelta(days=1), datetime.min.time()).replace(hour=3)
        time.sleep(max(60, (siguiente - ahora).total_seconds()))
        try:
            regenerar()
        except Exception as e:   # nunca tumbar el servidor por la regeneración
            print(f"[demo] error al regenerar: {e}", flush=True)




# ─── Límite de uso del asistente ─────────────────────────────────────────────

_uso = defaultdict(int)
_uso_lock = threading.Lock()


def _visitante(request: Request):
    reenviado = request.headers.get("x-forwarded-for", "")
    return (reenviado.split(",")[0].strip() or (request.client.host if request.client else "?"))


def _consumir(request):
    hoy = date.today().isoformat()
    with _uso_lock:
        if _uso[("global", hoy)] >= LIMITE_GLOBAL:
            raise HTTPException(429, "El asistente llegó a su límite de hoy en este demo. Vuelve mañana.")
        clave = (_visitante(request), hoy)
        if _uso[clave] >= LIMITE_VISITANTE:
            raise HTTPException(429, f"Llegaste a las {LIMITE_VISITANTE} preguntas de hoy en este demo.")
        _uso[clave] += 1
        _uso[("global", hoy)] += 1


# ─── Páginas ─────────────────────────────────────────────────────────────────

def _pagina(request, nombre, activa, **ctx):
    d = datos()
    return plantillas.TemplateResponse(request, nombre, {
        "activa": activa, "empresa": d["resumen"]["empresa"], "corte": d["resumen"]["corte"],
        "demo": DEMO, "asistente_activo": bool(os.environ.get("OPENROUTER_API_KEY")), **ctx})


@app.get("/", response_class=HTMLResponse)
def tablero(request: Request):
    d = datos()
    peores = sorted(d["unidades"]["unidades"], key=lambda u: u["utilidad"])[:8]
    serie = d["serie"]
    bruto = max(max(s["ingreso"] for s in serie), max(s["ingreso"] - s["utilidad"] for s in serie)) or 1
    paso = _paso_redondo(bruto / 4)
    modelos = sorted(d["vehiculos"]["grupos"], key=lambda g: g["margen"])[:4]
    return _pagina(request, "tablero.html", "tablero", r=d["resumen"], categorias=d["categorias"],
                   peores=peores, serie=serie, tope=paso * math.ceil(bruto / paso), paso=paso,
                   cartera=d["cartera"], ventana=d["unidades"], modelos=modelos)


def _paso_redondo(x):
    """Paso de eje 'bonito': 1, 2, 2.5 o 5 por una potencia de 10."""
    base = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if m * base >= x:
            return m * base


@app.get("/unidades", response_class=HTMLResponse)
def unidades(request: Request):
    d = datos()
    return _pagina(request, "unidades.html", "unidades", ventana=d["unidades"],
                   filas=sorted(d["unidades"]["unidades"], key=lambda u: u["utilidad"]),
                   vehiculos=d["vehiculos"]["grupos"], clientes=d["clientes"]["grupos"])


@app.get("/cartera", response_class=HTMLResponse)
def cartera(request: Request):
    d = datos()
    return _pagina(request, "cartera.html", "cartera", cartera=d["cartera"], r=d["resumen"])


@app.get("/cotizador", response_class=HTMLResponse)
def cotizador(request: Request):
    return _pagina(request, "cotizador.html", "cotizador",
                   catalogo=[{"vehiculo": f"{m} {mo}", "categoria": c, "precio": p, "mtto": mt}
                             for m, mo, c, p, mt in _catalogo()])


@app.get("/asistente", response_class=HTMLResponse)
def asistente_pagina(request: Request):
    return _pagina(request, "asistente.html", "asistente", limite=LIMITE_VISITANTE)


@app.get("/como-funciona", response_class=HTMLResponse)
def como_funciona(request: Request):
    return _pagina(request, "como_funciona.html", "como")


def _catalogo():
    from flotilla.seed import CATALOGO
    return CATALOGO


# ─── API ─────────────────────────────────────────────────────────────────────

class Cotizacion(BaseModel):
    precio: float = Field(gt=0, le=20_000_000)
    plazo_meses: int = Field(ge=6, le=72)
    tasa_anual: float = Field(ge=0, le=1)
    residual_pct: float = Field(ge=0, lt=1)
    comision_apertura_pct: float = Field(default=0, ge=0, le=0.1)
    mantenimiento_por_servicio: float | None = Field(default=None, ge=0, le=100_000)


@app.post("/api/cotizar")
def api_cotizar(c: Cotizacion):
    servicios = fin.cuota_servicios(c.precio, c.mantenimiento_por_servicio) if c.mantenimiento_por_servicio else 0.0
    try:
        r = fin.cotizar(c.precio, c.plazo_meses, c.tasa_anual, c.residual_pct, c.comision_apertura_pct,
                        servicios_mensuales=servicios)
    except ValueError as e:
        raise HTTPException(422, str(e))
    # Tabla de amortización del valor (saldo por recuperar al inicio de cada año).
    i = c.tasa_anual / 12
    saldo, tabla = c.precio, []
    for k in range(1, c.plazo_meses + 1):
        saldo = (saldo - r["renta_financiera"]) * (1 + i)
        if k % 12 == 0 or k == c.plazo_meses:
            tabla.append({"mes": k, "saldo": saldo})
    r["saldo_por_anio"] = tabla
    return r


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=2, max_length=1000)
    historial: list[dict] = Field(default_factory=list, max_length=12)


@app.post("/api/preguntar")
def api_preguntar(p: Pregunta, request: Request):
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise HTTPException(503, "El asistente no está configurado en este servidor.")
    _consumir(request)
    conn = _conexion()
    try:
        return asistente.preguntar(conn, p.pregunta, p.historial)
    except asistente.AsistenteNoDisponible as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # error del proveedor: mensaje claro, sin traza
        print(f"[asistente] {type(e).__name__}: {e}", flush=True)
        raise HTTPException(502, "El modelo no respondió. Intenta de nuevo en un momento.")
    finally:
        conn.close()


@app.get("/api/herramientas/{nombre}")
def api_herramienta(nombre: str):
    """Las mismas herramientas que ve el modelo, para inspeccionarlas en el navegador."""
    if nombre not in herramientas.POR_NOMBRE or nombre in ("consultar_sql", "cotizar"):
        raise HTTPException(404)
    conn = _conexion()
    try:
        import json
        return JSONResponse(json.loads(herramientas.ejecutar(conn, nombre, {})))
    finally:
        conn.close()


@app.get("/salud")
def salud():
    return {"ok": True, "corte": datos()["resumen"]["corte"], "demo": DEMO}
