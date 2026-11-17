"""SQLite: esquema y conexión. Una sola fuente de verdad para web, MCP y pruebas."""
import os
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "flotilla.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS parametros (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clientes (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    rfc TEXT NOT NULL,
    sector TEXT NOT NULL,
    ciudad TEXT NOT NULL,
    alta TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unidades (
    id INTEGER PRIMARY KEY,
    placa TEXT NOT NULL UNIQUE,
    marca TEXT NOT NULL,
    modelo TEXT NOT NULL,
    anio INTEGER NOT NULL,
    categoria TEXT NOT NULL,
    costo REAL NOT NULL,                 -- precio de compra sin IVA
    fecha_compra TEXT NOT NULL,
    vida_meses INTEGER NOT NULL,         -- vida útil contable
    residual REAL NOT NULL,              -- valor esperado al final de la vida útil
    estado TEXT NOT NULL,                -- arrendada | disponible | taller | vendida
    fecha_baja TEXT,
    precio_venta REAL
);

CREATE TABLE IF NOT EXISTS contratos (
    id INTEGER PRIMARY KEY,
    folio TEXT NOT NULL UNIQUE,
    unidad_id INTEGER NOT NULL REFERENCES unidades(id),
    cliente_id INTEGER NOT NULL REFERENCES clientes(id),
    inicio TEXT NOT NULL,
    plazo_meses INTEGER NOT NULL,
    renta_mensual REAL NOT NULL,         -- sin IVA; incluye la cuota de servicios
    cuota_servicios REAL NOT NULL,       -- parte de la renta que paga seguro, GPS, mantenimiento...
    tasa_anual REAL NOT NULL,
    residual REAL NOT NULL,
    estado TEXT NOT NULL,                -- vigente | terminado | cancelado
    fin TEXT NOT NULL                    -- fin real (pactado, o fecha de cancelación)
);

CREATE TABLE IF NOT EXISTS cobranza (
    id INTEGER PRIMARY KEY,
    contrato_id INTEGER NOT NULL REFERENCES contratos(id),
    periodo INTEGER NOT NULL,
    vencimiento TEXT NOT NULL,
    monto REAL NOT NULL,                 -- renta sin IVA
    pagado REAL NOT NULL DEFAULT 0,
    fecha_pago TEXT,
    UNIQUE(contrato_id, periodo)
);

CREATE TABLE IF NOT EXISTS gastos (
    id INTEGER PRIMARY KEY,
    unidad_id INTEGER NOT NULL REFERENCES unidades(id),
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL,                  -- mantenimiento | seguro | gps | siniestro | tenencia | verificacion
    monto REAL NOT NULL,
    descripcion TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_contratos_unidad ON contratos(unidad_id);
CREATE INDEX IF NOT EXISTS idx_contratos_cliente ON contratos(cliente_id);
CREATE INDEX IF NOT EXISTS idx_cobranza_contrato ON cobranza(contrato_id);
CREATE INDEX IF NOT EXISTS idx_cobranza_venc ON cobranza(vencimiento);
CREATE INDEX IF NOT EXISTS idx_gastos_unidad ON gastos(unidad_id, fecha);
"""


def db_path():
    return Path(os.environ.get("FLOTILLA_DB", DEFAULT_PATH))


def connect(path=None, readonly=False):
    path = Path(path or db_path())
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def parametro(conn, clave, default=None):
    row = conn.execute("SELECT valor FROM parametros WHERE clave=?", (clave,)).fetchone()
    return row["valor"] if row else default
