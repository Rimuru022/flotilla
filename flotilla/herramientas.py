"""Herramientas que puede usar un modelo: las mismas para el asistente web y
para el servidor MCP (Claude Code / Claude Desktop). Cada una devuelve datos
ya calculados por `analitica` o `finanzas`; el modelo solo redacta."""
import json

from flotilla import analitica, consultas
from flotilla import finanzas as fin


def _r(x, nd=2):
    """Redondea recursivamente para no mandar 15 decimales al modelo."""
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _r(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [_r(v, nd) for v in x]
    return x


def resumen_flota(conn):
    return _r(analitica.resumen(conn), 4)


def rentabilidad(conn, agrupar_por="unidad", meses=12, orden="peores", limite=15):
    if not 1 <= int(meses) <= 48:
        raise ValueError("meses debe estar entre 1 y 48.")
    limite = max(1, min(int(limite), 200))
    if agrupar_por == "unidad":
        datos = analitica.rentabilidad_unidades(conn, int(meses))
        filas = datos["unidades"]
    else:
        datos = analitica.rentabilidad_por(conn, agrupar_por, int(meses))
        filas = datos["grupos"]
    if orden not in ("peores", "mejores"):
        raise ValueError("orden debe ser 'peores' o 'mejores'.")
    filas = sorted(filas, key=lambda f: f["utilidad"], reverse=(orden == "mejores"))
    return _r({"desde": datos["desde"], "hasta": datos["hasta"], "agrupado_por": agrupar_por,
               "total_filas": len(filas), "filas": filas[:limite]}, 4)


def cartera_vencida(conn, limite=20):
    datos = analitica.cartera_vencida(conn)
    datos["clientes"] = datos["clientes"][: max(1, min(int(limite), 200))]
    return _r(datos)


def cotizar(conn, precio, plazo_meses, tasa_anual, residual_pct, comision_apertura_pct=0.0,
            mantenimiento_por_servicio=None):
    servicios = 0.0
    if mantenimiento_por_servicio is not None:
        servicios = fin.cuota_servicios(float(precio), float(mantenimiento_por_servicio))
    return _r(fin.cotizar(float(precio), int(plazo_meses), float(tasa_anual), float(residual_pct),
                          float(comision_apertura_pct), servicios_mensuales=servicios), 4)


def consultar_sql(conn, sql):
    return _r(consultas.ejecutar(conn, sql))


def esquema(conn):
    return {"esquema": consultas.ESQUEMA}


# Definiciones en formato JSON Schema (sirven tal cual para OpenAI/OpenRouter y MCP).
DEFINICIONES = [
    {
        "nombre": "resumen_flota",
        "descripcion": "Indicadores generales al corte: unidades por estado, utilización, contratos vigentes, "
                       "renta mensual vigente, ingreso y utilidad de 12 meses, cartera vencida por rango.",
        "parametros": {"type": "object", "properties": {}},
        "fn": resumen_flota,
    },
    {
        "nombre": "rentabilidad",
        "descripcion": "Rentabilidad económica (ingreso devengado + resultado por venta - gastos - depreciación "
                       "- costo de fondeo) por unidad, categoría, vehículo (marca modelo año) o cliente.",
        "parametros": {"type": "object", "properties": {
            "agrupar_por": {"type": "string", "enum": ["unidad", "categoria", "vehiculo", "cliente"]},
            "meses": {"type": "integer", "minimum": 1, "maximum": 48, "description": "Ventana hacia atrás desde el corte. Por defecto 12."},
            "orden": {"type": "string", "enum": ["peores", "mejores"]},
            "limite": {"type": "integer", "minimum": 1, "maximum": 200},
        }},
        "fn": rentabilidad,
    },
    {
        "nombre": "cartera_vencida",
        "descripcion": "Saldos vencidos al corte por cliente y por antigüedad (1-30, 31-60, 61-90, 90+ días), "
                       "con contratos rescindidos.",
        "parametros": {"type": "object", "properties": {"limite": {"type": "integer", "minimum": 1, "maximum": 200}}},
        "fn": cartera_vencida,
    },
    {
        "nombre": "cotizar",
        "descripcion": "Cotiza un arrendamiento puro: renta mensual con y sin IVA, residual, comisión y TIR. "
                       "Si se da el mantenimiento por servicio, suma la cuota de servicios (seguro, GPS, "
                       "mantenimiento, tenencia, verificación + margen).",
        "parametros": {"type": "object", "required": ["precio", "plazo_meses", "tasa_anual", "residual_pct"], "properties": {
            "precio": {"type": "number", "description": "Precio de la unidad sin IVA, MXN."},
            "plazo_meses": {"type": "integer", "minimum": 1, "maximum": 72},
            "tasa_anual": {"type": "number", "description": "Tasa anual como fracción: 0.185 = 18.5%."},
            "residual_pct": {"type": "number", "description": "Residual como fracción del precio: 0.30 = 30%."},
            "comision_apertura_pct": {"type": "number"},
            "mantenimiento_por_servicio": {"type": "number", "description": "Costo de un servicio de mantenimiento, MXN."},
        }},
        "fn": cotizar,
    },
    {
        "nombre": "consultar_sql",
        "descripcion": "Consulta SQL de solo lectura (SQLite) para preguntas que las otras herramientas no "
                       "cubren. Llama primero a `esquema`. Máximo 200 filas.",
        "parametros": {"type": "object", "required": ["sql"], "properties": {"sql": {"type": "string"}}},
        "fn": consultar_sql,
    },
    {
        "nombre": "esquema",
        "descripcion": "Describe las tablas y columnas de la base para escribir SQL.",
        "parametros": {"type": "object", "properties": {}},
        "fn": esquema,
    },
]
POR_NOMBRE = {d["nombre"]: d for d in DEFINICIONES}


def ejecutar(conn, nombre, argumentos):
    """Ejecuta una herramienta y SIEMPRE regresa texto JSON: un error también
    es una respuesta útil para el modelo (puede corregir y reintentar)."""
    d = POR_NOMBRE.get(nombre)
    if d is None:
        return json.dumps({"error": f"Herramienta desconocida: {nombre}"}, ensure_ascii=False)
    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos or "{}")
        except json.JSONDecodeError:
            return json.dumps({"error": "Los argumentos no son JSON válido."}, ensure_ascii=False)
    try:
        resultado = d["fn"](conn, **(argumentos or {}))
    except TypeError as e:
        return json.dumps({"error": f"Argumentos inválidos: {e}"}, ensure_ascii=False)
    except ValueError as e:     # incluye ConsultaRechazada
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps(resultado, ensure_ascii=False, default=str)
