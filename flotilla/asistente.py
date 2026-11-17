"""Asistente en español: preguntas de negocio sobre la flota.

Bucle de tool use sobre la API compatible con OpenAI de OpenRouter. El modelo
es configurable (FLOTILLA_MODELO): por defecto uno barato que maneja bien
herramientas; se puede poner un modelo de Anthropic sin tocar código.

Regla central: las cifras salen de las herramientas, no del modelo. El modelo
decide qué preguntar y redacta; la aritmética la hace código probado.
"""
import json
import os
import time

from flotilla import herramientas

MODELO_DEFAULT = "deepseek/deepseek-v4-flash"
MAX_RONDAS = 8

SISTEMA = """Eres el analista financiero de {empresa}, una arrendadora de vehículos (arrendamiento puro).
Respondes al director general en español, claro y directo, como en un correo interno breve.

Reglas:
- Toda cifra debe venir de una herramienta. Nunca inventes ni estimes montos. Si no puedes obtener un dato, dilo.
- Prefiere las herramientas específicas (resumen_flota, rentabilidad, cartera_vencida, cotizar).
  Usa consultar_sql solo si ninguna cubre la pregunta, y llama a `esquema` antes de escribir SQL.
- Si una herramienta regresa error, corrige los argumentos y reintenta.
- Montos en MXN sin IVA salvo que digas lo contrario. Formato $1,234,567. Porcentajes con un decimal.
- Di siempre el periodo o la fecha de corte de las cifras.
- La rentabilidad es económica: ingreso devengado + resultado por venta - gastos - depreciación - costo de fondeo.
- Termina con una recomendación concreta cuando la pregunta lo amerite.
- No uses tablas de más de 8 filas. No uses el carácter "—".
- Sin saludo, sin firma y sin emojis. Empieza directo con la respuesta.
- Si la pregunta pide algo que los datos no contienen (proyecciones, datos de mercado, otra empresa), dilo y ofrece lo más cercano que sí puedes calcular.

Fecha de corte de los datos: {corte}."""


class AsistenteNoDisponible(RuntimeError):
    pass


def _cliente():
    clave = os.environ.get("OPENROUTER_API_KEY", "")
    if not clave:
        raise AsistenteNoDisponible("El asistente no está configurado (falta OPENROUTER_API_KEY).")
    from openai import OpenAI
    # max_retries: el SDK reintenta 429 y 5xx con espera exponencial (los proveedores baratos lo devuelven seguido).
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=clave, timeout=60, max_retries=4,
                  default_headers={"X-Title": "Flotilla demo"})


def _limpiar(texto):
    """El prompt pide no usar raya larga, pero los modelos a veces la usan igual:
    se garantiza en código, no en el prompt."""
    return (texto or "").replace(" — ", " - ").replace("—", "-").strip()


def _tools():
    return [{"type": "function", "function": {"name": d["nombre"], "description": d["descripcion"],
                                              "parameters": d["parametros"]}}
            for d in herramientas.DEFINICIONES]


def preguntar(conn, pregunta, historial=None, cliente=None, modelo=None):
    """Regresa {respuesta, pasos, modelo, segundos}. `pasos` es la traza de
    herramientas usadas, que la interfaz muestra para que se pueda auditar."""
    from flotilla import db
    cliente = cliente or _cliente()
    modelo = modelo or os.environ.get("FLOTILLA_MODELO", MODELO_DEFAULT)
    inicio = time.monotonic()
    mensajes = [{"role": "system", "content": SISTEMA.format(
        empresa=db.parametro(conn, "empresa", "la arrendadora"), corte=db.parametro(conn, "fecha_corte", "hoy"))}]
    for turno in (historial or [])[-6:]:
        if turno.get("role") in ("user", "assistant") and isinstance(turno.get("content"), str):
            mensajes.append({"role": turno["role"], "content": turno["content"][:4000]})
    mensajes.append({"role": "user", "content": pregunta[:2000]})

    pasos = []
    for _ in range(MAX_RONDAS):
        r = cliente.chat.completions.create(model=modelo, messages=mensajes, tools=_tools(), temperature=0.2)
        msg = r.choices[0].message
        llamadas = msg.tool_calls or []
        if not llamadas:
            return {"respuesta": _limpiar(msg.content), "pasos": pasos, "modelo": modelo,
                    "segundos": round(time.monotonic() - inicio, 1)}
        mensajes.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": t.id, "type": "function",
                                         "function": {"name": t.function.name, "arguments": t.function.arguments}}
                                        for t in llamadas]})
        for t in llamadas:
            salida = herramientas.ejecutar(conn, t.function.name, t.function.arguments)
            try:
                args = json.loads(t.function.arguments or "{}")
            except json.JSONDecodeError:
                args = t.function.arguments
            pasos.append({"herramienta": t.function.name, "argumentos": args,
                          "error": json.loads(salida).get("error") if salida.startswith('{"error"') else None})
            mensajes.append({"role": "tool", "tool_call_id": t.id, "content": salida[:30_000]})
    return {"respuesta": "No pude terminar el análisis en un número razonable de pasos. "
                         "Intenta una pregunta más acotada.",
            "pasos": pasos, "modelo": modelo, "segundos": round(time.monotonic() - inicio, 1)}
