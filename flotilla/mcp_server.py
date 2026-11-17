"""Servidor MCP (stdio): expone las herramientas de la flota a Claude Code o
Claude Desktop. Mismas funciones que usa el asistente web, sobre una conexión
de solo lectura.

Uso con Claude Code: el `.mcp.json` del repositorio ya lo registra.
Manual: `uv run python -m flotilla.mcp_server`
"""
import json
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from flotilla import consultas, db, herramientas

servidor = MCPServer(
    name="flotilla",
    instructions=(
        "Datos financieros de una arrendadora de vehículos (arrendamiento puro). Montos en MXN sin IVA. "
        "Usa resumen_flota para el panorama, rentabilidad y cartera_vencida para el detalle, cotizar para "
        "rentas nuevas, y consultar_sql (después de esquema) solo para lo que no cubran las demás."
    ),
)
_SOLO_LECTURA = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
_conn = None


def _conexion():
    global _conn
    if _conn is None:
        if not db.db_path().exists():
            raise RuntimeError(f"No existe la base {db.db_path()}. Corre: uv run python -m flotilla.cli generar")
        _conn = consultas.conexion_lectura()
    return _conn


def _llamar(nombre, **args):
    return json.loads(herramientas.ejecutar(_conexion(), nombre, {k: v for k, v in args.items() if v is not None}))


@servidor.tool(annotations=_SOLO_LECTURA)
def resumen_flota() -> dict:
    """Indicadores generales al corte: unidades por estado, utilización, renta vigente, ingreso y
    utilidad de 12 meses, cartera vencida por rango de antigüedad."""
    return _llamar("resumen_flota")


@servidor.tool(annotations=_SOLO_LECTURA)
def rentabilidad(agrupar_por: Literal["unidad", "categoria", "vehiculo", "cliente"] = "unidad",
                 meses: int = 12, orden: Literal["peores", "mejores"] = "peores", limite: int = 15) -> dict:
    """Rentabilidad económica (ingreso devengado + resultado por venta - gastos - depreciación - costo de
    fondeo) en los últimos `meses`, por unidad, categoría, vehículo o cliente."""
    return _llamar("rentabilidad", agrupar_por=agrupar_por, meses=meses, orden=orden, limite=limite)


@servidor.tool(annotations=_SOLO_LECTURA)
def cartera_vencida(limite: int = 20) -> dict:
    """Saldos vencidos al corte por cliente y por antigüedad (1-30, 31-60, 61-90, 90+ días)."""
    return _llamar("cartera_vencida", limite=limite)


@servidor.tool(annotations=_SOLO_LECTURA)
def cotizar(precio: float, plazo_meses: int, tasa_anual: float, residual_pct: float,
            comision_apertura_pct: float = 0.0, mantenimiento_por_servicio: float | None = None) -> dict:
    """Cotiza un arrendamiento puro. tasa_anual y residual_pct como fracción (0.185 = 18.5%).
    Con mantenimiento_por_servicio suma la cuota de servicios a la renta."""
    return _llamar("cotizar", precio=precio, plazo_meses=plazo_meses, tasa_anual=tasa_anual,
                   residual_pct=residual_pct, comision_apertura_pct=comision_apertura_pct,
                   mantenimiento_por_servicio=mantenimiento_por_servicio)


@servidor.tool(annotations=_SOLO_LECTURA)
def esquema() -> dict:
    """Tablas y columnas de la base, para escribir SQL."""
    return _llamar("esquema")


@servidor.tool(annotations=_SOLO_LECTURA)
def consultar_sql(sql: str) -> dict:
    """Una sentencia SELECT de solo lectura (SQLite), máximo 200 filas. Cualquier escritura, PRAGMA o
    ATTACH se rechaza."""
    return _llamar("consultar_sql", sql=sql)


def main():
    servidor.run("stdio")


if __name__ == "__main__":
    main()
