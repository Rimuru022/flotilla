"""SQL de solo lectura para el asistente.

Un modelo de lenguaje escribe el SQL, así que se trata como entrada no
confiable. No se intenta "limpiar" el texto con expresiones regulares: SQLite
mismo decide qué puede hacer la sentencia, en tres capas independientes.

1. Conexión abierta en modo `ro`: el archivo no se puede modificar.
2. Authorizer: SQLite consulta cada acción al compilar la sentencia y solo
   se permite leer (SELECT, lectura de columnas, funciones, CTE). Escrituras,
   PRAGMA, ATTACH y demás se niegan antes de ejecutar nada.
3. Límites: una sola sentencia, tope de filas y tope de trabajo (progress
   handler), para que un CTE recursivo infinito no cuelgue el servidor.
"""
import sqlite3

from flotilla import db

MAX_FILAS = 200
MAX_PASOS = 20_000_000          # instrucciones de la VM de SQLite (~1 s)

_PERMITIDAS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}
_FUNCIONES_PROHIBIDAS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class ConsultaRechazada(ValueError):
    pass


def _autorizar(accion, arg1, arg2, _db_name, _origen):
    if accion not in _PERMITIDAS:
        return sqlite3.SQLITE_DENY
    if accion == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _FUNCIONES_PROHIBIDAS:
        return sqlite3.SQLITE_DENY
    if accion == sqlite3.SQLITE_READ and (arg1 or "").startswith("sqlite_"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def conexion_lectura(path=None):
    conn = db.connect(path, readonly=True)
    conn.set_authorizer(_autorizar)
    return conn


def ejecutar(conn, sql, max_filas=MAX_FILAS):
    """Ejecuta un SELECT y regresa {columnas, filas, truncado}. `conn` debe
    venir de `conexion_lectura` (las pruebas verifican que el authorizer basta
    aun con una conexión de escritura)."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise ConsultaRechazada("La consulta está vacía.")
    if not sqlite3.complete_statement(sql + ";"):
        raise ConsultaRechazada("La consulta está incompleta.")

    pasos = {"n": 0}

    def vigilar():
        pasos["n"] += 1
        return 1 if pasos["n"] * 1000 > MAX_PASOS else 0

    conn.set_authorizer(_autorizar)
    conn.set_progress_handler(vigilar, 1000)
    try:
        cur = conn.execute(sql)
        filas = cur.fetchmany(max_filas + 1)
    except sqlite3.DatabaseError as e:
        msg = str(e)
        if "not authorized" in msg:
            raise ConsultaRechazada("Solo se permiten consultas de lectura (SELECT).") from e
        if "interrupted" in msg:
            raise ConsultaRechazada("La consulta tardó demasiado; agrega filtros o un LIMIT.") from e
        if "one statement at a time" in msg:
            raise ConsultaRechazada("Envía una sola sentencia SELECT.") from e
        raise ConsultaRechazada(f"Error de SQL: {msg}") from e
    except sqlite3.ProgrammingError as e:
        raise ConsultaRechazada("Envía una sola sentencia SELECT.") from e
    finally:
        conn.set_progress_handler(None, 0)
    columnas = [d[0] for d in cur.description or []]
    truncado = len(filas) > max_filas
    return {"columnas": columnas, "filas": [list(f) for f in filas[:max_filas]], "truncado": truncado}


ESQUEMA = """Tablas (SQLite; fechas TEXT 'AAAA-MM-DD'; montos en MXN sin IVA):

parametros(clave, valor)  -- 'empresa', 'fecha_corte', 'tasa_fondeo_anual', 'iva'
clientes(id, nombre, rfc, sector, ciudad, alta)
unidades(id, placa, marca, modelo, anio, categoria [Sedán|SUV|Pickup|Van], costo, fecha_compra,
         vida_meses, residual, estado [arrendada|disponible|taller|vendida], fecha_baja, precio_venta)
contratos(id, folio, unidad_id -> unidades, cliente_id -> clientes, inicio, plazo_meses,
          renta_mensual [incluye cuota_servicios], cuota_servicios, tasa_anual, residual,
          estado [vigente|terminado|cancelado], fin [fin pactado o fecha de rescisión])
cobranza(id, contrato_id -> contratos, periodo, vencimiento, monto, pagado, fecha_pago)
         -- una renta por mes; saldo = monto - pagado; vencida si pagado < monto y vencimiento < fecha_corte
gastos(id, unidad_id -> unidades, fecha, tipo [mantenimiento|seguro|gps|siniestro|tenencia|verificacion],
       monto, descripcion)
"""
