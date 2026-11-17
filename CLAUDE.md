# Flotilla

Profitability, receivables and quoting for a vehicle leasing company (arrendamiento puro), with a Spanish AI assistant and an MCP server.
The UI and all user-facing text are in Spanish; code identifiers are Spanish too, to match the business vocabulary.

## Layout

- `flotilla/finanzas.py`: pure formulas (rent, IRR, depreciation, aging buckets, service fee). No database.
- `flotilla/analitica.py`: every business figure (profitability per unit and group, overdue, summary, monthly series). The only place numbers are computed.
- `flotilla/herramientas.py`: tools exposed to models. Shared by the web assistant and the MCP server.
- `flotilla/consultas.py`: read-only SQL for model-written queries (SQLite authorizer + `mode=ro` + row and work limits).
- `flotilla/asistente.py`: tool-use loop over OpenRouter (OpenAI-compatible). Model from `FLOTILLA_MODELO`.
- `flotilla/mcp_server.py`: stdio MCP server, registered in `.mcp.json`.
- `flotilla/seed.py`: deterministic simulation of a fictitious lessor (4 years, about 140 units). Rents come from the same formula as the quoting tool.
- `flotilla/web.py` + `templates/` + `static/app.css`: FastAPI + Jinja dashboard.

## Rules

- Never compute a business figure outside `analitica.py` / `finanzas.py`. Web, assistant and MCP must give the same answer to the same question.
- Model-written SQL is untrusted input. Any change to `consultas.py` needs a test in `tests/test_consultas.py`, run against both a read-only and a read-write connection.
- Write the failing test first for any formula or bug fix. Formulas are tested against known external values or properties (present value returns the price, IRR returns the agreed rate), not against their own output.
- All data is fictitious. Do not add real company names, RFCs or plates.
- The demo database is rebuilt daily with today as the cutoff date, so tests and copy must not depend on a fixed date except through `seed.generar(conn, corte)`.
- No em dash character anywhere.

## Commands

```bash
uv sync
uv run pytest -q
uv run python -m flotilla.cli generar           # demo database in data/flotilla.db
uv run uvicorn flotilla.web:app --reload        # http://localhost:8000
uv run python -m flotilla.cli preguntar "..."   # needs OPENROUTER_API_KEY
```

For business questions about the data, use the `analisis-flota` skill and the `flotilla` MCP tools.
