---
name: analisis-flota
description: Answer business questions about the leasing fleet (profitability per unit, model or client, overdue receivables, utilization, quotes for new leases) using the flotilla MCP tools, and write the answer as a short Spanish memo for the CEO. Use whenever the user asks about the fleet, clients, cartera, rentas, cotizaciones, or "qué conviene" decisions in this repo.
---

# Análisis de flota

The user is the CEO of a vehicle leasing company (arrendamiento puro).
They want a decision, not a data dump.
Answer in Spanish.

## Get the numbers from the tools, never from your head

The `flotilla` MCP server (registered in `.mcp.json`) exposes the same functions the web dashboard uses.
Every figure in your answer must come from one of these calls.
If a number cannot be obtained, say so.

| Question | Tool |
| --- | --- |
| Overall picture, utilization, total overdue | `resumen_flota` |
| Which units, models, categories or clients make or lose money | `rentabilidad` with `agrupar_por` = `unidad`, `vehiculo`, `categoria` or `cliente` |
| Who owes what and how old it is | `cartera_vencida` |
| Rent for a new deal | `cotizar` (rates and residual as fractions: 0.185, 0.30) |
| Anything else (contracts ending soon, history of one client, expense detail) | `esquema`, then `consultar_sql` |

If the MCP server is not connected, run the same functions through the CLI or Python instead (`uv run python -m flotilla.cli resumen`, or import `flotilla.analitica`).
Do not compute profitability in ad hoc SQL: it would drift from the dashboard.
The definitions live in `flotilla/analitica.py`.

## Definitions the CEO will ask about

- Profitability is economic: accrued rent + gain on sale - operating expenses - depreciation - funding cost.
- The window is the last N closed calendar months before the cutoff date (default 12).
- Amounts are MXN before IVA unless you say otherwise.
- Overdue = rent with balance and due date before the cutoff, bucketed 1-30, 31-60, 61-90, 90+ days.
- A `cancelado` contract was rescinded for non-payment; its balance stays in overdue.

## Write the answer like this

1. One sentence with the direct answer and its key number.
2. Two to five bullets with the evidence: the figures, the period, and the cause when the data shows it (a claim, idle months, an old unit with high maintenance).
3. A concrete recommendation with what to do this week or this quarter.

Keep tables to 8 rows or fewer.
Format money as $1,234,567 and percentages with one decimal.
Always state the cutoff date or period.
Never use the em dash character.

## Before you deliver a number-heavy analysis

If the answer will be used for a decision (buy or sell units, change rates, take legal action on a client), hand the draft to the `auditor-cifras` subagent.
It recomputes each figure with an independent query and reports mismatches.
Fix what it finds before answering.
