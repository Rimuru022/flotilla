# Flotilla

Rentabilidad por unidad, cartera vencida y cotizador para una arrendadora de vehículos (arrendamiento puro).
Incluye un asistente en español que contesta preguntas de negocio con datos y un servidor MCP para usarlo desde Claude Code.

**Demo en vivo:** `https://<dominio-del-demo>` (empresa y cifras ficticias; se regeneran cada noche).

## Qué resuelve

Un director de una arrendadora necesita contestar tres preguntas cada semana:

1. **¿Qué deja dinero y qué no?** Utilidad económica por unidad, modelo, categoría y cliente: ingreso devengado + resultado por venta - gastos - depreciación - costo de fondeo.
2. **¿Quién nos debe?** Cartera vencida por cliente en rangos de 1-30, 31-60, 61-90 y más de 90 días, con contratos rescindidos.
3. **¿Cuánto cobro en una operación nueva?** Renta anticipada con residual, cuota de servicios, IVA, comisión y TIR.

Y hacer cualquier otra pregunta en lenguaje natural: "¿qué clientes debo llamar esta semana?", "¿conviene seguir comprando pickups?".

## Cómo está hecho

```
Tablero / cartera / cotizador     Asistente web (OpenRouter)     Servidor MCP (Claude Code)
              \                            |                            /
               \                    herramientas.py  <-----------------
                \                          |
                 '----------->  analitica.py + finanzas.py  ---->  SQLite
```

- **Una sola capa de cálculo.** `analitica.py` y `finanzas.py` calculan todas las cifras.
  La web, el asistente y el servidor MCP solo las llaman, así que la misma pregunta da el mismo número en los tres lados.
- **El modelo no hace cuentas.** El asistente decide qué herramienta llamar y redacta.
  Cada cifra sale de una herramienta, y la interfaz muestra debajo de cada respuesta qué consultó.
- **SQL de un modelo, tratado como entrada no confiable.** Cuando ninguna herramienta cubre la pregunta, el modelo puede escribir SQL.
  No se filtra con expresiones regulares.
  SQLite autoriza cada acción al compilar (solo lectura), la conexión se abre en `mode=ro` y hay tope de filas y de tiempo.
- **Modelo configurable.** OpenRouter con un modelo económico por defecto (`deepseek/deepseek-v4-flash`).
  Para usar Claude basta cambiar `FLOTILLA_MODELO`.
- **Datos simulados pero coherentes.** `seed.py` simula 4 años de una arrendadora: compras, colocaciones, fin de contratos, rescisiones por falta de pago, ventas de unidades viejas, siniestros y mantenimiento.
  Las rentas salen de la misma fórmula del cotizador.

## Hecho con Claude Code

El repositorio incluye lo que se usó para construirlo y lo que deja listo para seguir trabajando con IA:

| Pieza | Para qué |
| --- | --- |
| `CLAUDE.md` | Reglas del proyecto: cifras solo en `analitica.py`, prueba primero, datos ficticios. |
| `.mcp.json` | Registra el servidor MCP `flotilla`: Claude Code consulta la flota directamente. |
| `.claude/skills/analisis-flota` | Cómo contestar preguntas de negocio: qué herramienta usar, definiciones, formato de memo para dirección. |
| `.claude/agents/auditor-cifras.md` | Subagente que revisa un análisis antes de entregarlo y recalcula cada cifra con SQL independiente. |

Para probarlo: abre el repositorio con `claude`, acepta el servidor MCP y pregunta, por ejemplo, "¿qué unidades conviene vender este trimestre?".

## Cómo se verificó

La suite tiene 104 pruebas (`uv run pytest -q`):

- **Fórmulas contra valores externos o propiedades.** El pago de un préstamo de libro coincide con cualquier calculadora financiera.
  El valor presente de las rentas más el del residual regresa al precio, y la TIR del flujo regresa la tasa pactada.
- **Indicadores contra un caso calculado a mano.** Una unidad con depreciación, fondeo, gastos fuera de la ventana y una venta.
  También se prueba que la serie mensual cuadra con el acumulado.
- **Seguridad del SQL.** `DELETE`, `DROP`, `PRAGMA`, `ATTACH`, `load_extension`, `sqlite_master`, varias sentencias y un CTE recursivo infinito se rechazan.
  Las pruebas corren contra una conexión de solo lectura y también contra una de escritura, para que el authorizer baste por sí mismo.
- **Invariantes de los datos.** Es determinista, no hay contratos traslapados, nada ocurre después del corte, la renta sale de la fórmula del cotizador y los cuatro estados de unidad siempre están presentes.
- **Asistente con un modelo falso.** Ejecuta herramientas, devuelve errores al modelo para que corrija, respeta el tope de rondas y no deja que el historial cambie el prompt de sistema.
- **Web y MCP de punta a punta.** Las páginas, la validación del cotizador y los límites de uso se prueban con `TestClient`.
  El servidor MCP se arranca por stdio, igual que lo hace Claude Code.

Errores reales que encontraron las pruebas en el camino:

- Gastos y contratos fechados después del corte durante el mes en curso.
- Un margen de -40% en toda la flota: la renta simulada cubría la parte financiera pero no la cuota de servicios que se cobra en arrendamiento puro.
- Una ventana de "12 meses" que incluía el mes en curso a medias: mezclaba depreciación de 30 días con rentas de 17.

## Correrlo

```bash
uv sync
cp .env.example .env                              # opcional: OPENROUTER_API_KEY para el asistente
uv run python -m flotilla.cli generar             # base de demostración en data/flotilla.db
uv run uvicorn flotilla.web:app --reload          # http://localhost:8000
uv run python -m flotilla.cli preguntar "¿Qué clientes debo llamar esta semana?"
```

Con Docker (modo demo: regenera la base cada noche):

```bash
docker build -t flotilla .
docker run -p 8000:8000 -e OPENROUTER_API_KEY=... flotilla
```

## Supuestos

- Montos en MXN sin IVA; IVA de 16% solo en la cotización.
- Renta anticipada; tasa nominal anual con capitalización mensual.
- Depreciación en línea recta a 60 meses hasta 32% del costo; fondeo a 11.5% anual sobre el valor en libros.
- La ventana de rentabilidad son meses calendario cerrados antes del corte.
- Los supuestos de costo operativo (seguro, GPS, tenencia, verificación, mantenimiento) están en `finanzas.py` y los usan tanto la cuota de servicios como la simulación.
