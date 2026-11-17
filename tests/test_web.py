"""La aplicación web de punta a punta con TestClient, sobre una base temporal."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cliente(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("web") / "flotilla.db"
    os.environ["FLOTILLA_DB"] = str(ruta)
    from flotilla import web
    with TestClient(web.app) as c:     # el arranque genera la base porque no existe
        yield c
    os.environ.pop("FLOTILLA_DB", None)


@pytest.mark.parametrize("ruta", ["/", "/unidades", "/cartera", "/cotizador", "/asistente", "/como-funciona"])
def test_paginas(cliente, ruta):
    r = cliente.get(ruta)
    assert r.status_code == 200
    assert "Flotilla" in r.text
    assert "—" not in r.text


def test_tablero_muestra_los_indicadores(cliente):
    r = cliente.get("/").text
    for texto in ("Utilización", "Renta mensual vigente", "Cartera vencida", "Unidades que más dinero pierden"):
        assert texto in r


def test_cotizar(cliente):
    r = cliente.post("/api/cotizar", json={"precio": 450000, "plazo_meses": 36, "tasa_anual": 0.185,
                                          "residual_pct": 0.3, "mantenimiento_por_servicio": 4000})
    assert r.status_code == 200
    j = r.json()
    assert j["tir_anual_nominal"] == pytest.approx(0.185, abs=1e-6)
    assert j["saldo_por_anio"][-1]["mes"] == 36
    # Al final del plazo el saldo por recuperar es el residual.
    assert j["saldo_por_anio"][-1]["saldo"] == pytest.approx(j["residual"], rel=1e-6)


@pytest.mark.parametrize("cuerpo", [
    {"precio": -1, "plazo_meses": 36, "tasa_anual": 0.18, "residual_pct": 0.3},
    {"precio": 1000, "plazo_meses": 2, "tasa_anual": 0.18, "residual_pct": 0.3},
    {"precio": 1000, "plazo_meses": 36, "tasa_anual": 0.18, "residual_pct": 1.0},
    {"precio": 1000, "plazo_meses": 36, "tasa_anual": 5, "residual_pct": 0.3},
])
def test_cotizar_valida(cliente, cuerpo):
    assert cliente.post("/api/cotizar", json=cuerpo).status_code == 422


def test_asistente_sin_clave(cliente, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert cliente.post("/api/preguntar", json={"pregunta": "hola"}).status_code == 503
    assert "no está configurado" in cliente.get("/asistente").text


def test_limite_por_visitante(cliente, monkeypatch):
    from flotilla import asistente, web
    monkeypatch.setenv("OPENROUTER_API_KEY", "prueba")
    monkeypatch.setattr(web, "LIMITE_VISITANTE", 3)
    monkeypatch.setattr(asistente, "preguntar",
                        lambda conn, p, h=None: {"respuesta": "ok", "pasos": [], "modelo": "x", "segundos": 0})
    web._uso.clear()
    codigos = [cliente.post("/api/preguntar", json={"pregunta": "hola"}).status_code for _ in range(4)]
    assert codigos == [200, 200, 200, 429]
    # Otro visitante (detrás del proxy) sí puede.
    r = cliente.post("/api/preguntar", json={"pregunta": "hola"}, headers={"X-Forwarded-For": "10.0.0.9"})
    assert r.status_code == 200


def test_error_del_proveedor_no_filtra_detalles(cliente, monkeypatch):
    from flotilla import asistente, web
    monkeypatch.setenv("OPENROUTER_API_KEY", "prueba")

    def falla(*a, **k):
        raise RuntimeError("401 clave sk-or-secreta inválida")
    monkeypatch.setattr(asistente, "preguntar", falla)
    web._uso.clear()
    r = cliente.post("/api/preguntar", json={"pregunta": "hola"})
    assert r.status_code == 502 and "sk-or" not in r.text


def test_herramientas_de_solo_consulta(cliente):
    assert cliente.get("/api/herramientas/resumen_flota").json()["unidades_en_flota"] > 0
    assert cliente.get("/api/herramientas/consultar_sql").status_code == 404
    assert cliente.get("/api/herramientas/no_existe").status_code == 404


def test_servidor_mcp_por_stdio(cliente):
    """Arranca el servidor MCP como lo haría Claude Code y llama dos herramientas."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def correr():
        params = StdioServerParameters(command=sys.executable, args=["-m", "flotilla.mcp_server"],
                                       cwd=str(RAIZ), env={**os.environ, "PYTHONPATH": str(RAIZ)})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                nombres = {t.name for t in (await s.list_tools()).tools}
                ok = await s.call_tool("rentabilidad", {"agrupar_por": "categoria", "limite": 2})
                mal = await s.call_tool("consultar_sql", {"sql": "DELETE FROM unidades"})
                return nombres, json.loads(ok.content[0].text), json.loads(mal.content[0].text)

    nombres, ok, mal = asyncio.run(correr())
    assert nombres == {"resumen_flota", "rentabilidad", "cartera_vencida", "cotizar", "esquema", "consultar_sql"}
    assert ok["agrupado_por"] == "categoria" and len(ok["filas"]) == 2
    assert "Solo se permiten" in mal["error"]
