"""El SQL lo escribe un modelo: estas pruebas son el contrato de seguridad."""
import sqlite3
from datetime import date

import pytest

from flotilla import consultas, seed


@pytest.fixture(scope="module")
def ruta(tmp_path_factory):
    p = tmp_path_factory.mktemp("db") / "flotilla.db"
    c = sqlite3.connect(p)
    seed.generar(c, date(2026, 11, 17))
    c.close()
    return p


@pytest.fixture
def ro(ruta):
    c = consultas.conexion_lectura(ruta)
    yield c
    c.close()


@pytest.fixture
def rw(ruta):
    """Conexión de escritura: el authorizer debe bastar aun sin modo ro."""
    c = sqlite3.connect(ruta)
    yield c
    c.close()


def test_select_normal(ro):
    r = consultas.ejecutar(ro, "SELECT estado, COUNT(*) AS n FROM unidades GROUP BY estado ORDER BY estado")
    assert r["columnas"] == ["estado", "n"]
    assert {f[0] for f in r["filas"]} == {"arrendada", "disponible", "taller", "vendida"}
    assert r["truncado"] is False


def test_cte_y_punto_y_coma_final(ro):
    r = consultas.ejecutar(ro, """
        WITH vencido AS (SELECT contrato_id, SUM(monto - pagado) s FROM cobranza
                         WHERE pagado < monto GROUP BY 1)
        SELECT COUNT(*) FROM vencido;""")
    assert r["filas"][0][0] > 0


def test_tope_de_filas(ro):
    r = consultas.ejecutar(ro, "SELECT * FROM gastos", max_filas=50)
    assert len(r["filas"]) == 50 and r["truncado"] is True


@pytest.mark.parametrize("sql", [
    "DELETE FROM unidades",
    "UPDATE contratos SET renta_mensual = 0",
    "INSERT INTO clientes (nombre, rfc, sector, ciudad, alta) VALUES ('x','x','x','x','x')",
    "DROP TABLE cobranza",
    "CREATE TABLE t (x)",
    "ALTER TABLE unidades ADD COLUMN x",
    "PRAGMA writable_schema = 1",
    "PRAGMA table_info(unidades)",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "VACUUM",
    "WITH x AS (SELECT 1) DELETE FROM gastos",
    "SELECT load_extension('/tmp/evil')",
    "SELECT * FROM sqlite_master",
    "REPLACE INTO parametros VALUES ('empresa', 'hackeada')",
])
@pytest.mark.parametrize("conexion", ["ro", "rw"])
def test_todo_lo_que_no_es_lectura_se_rechaza(sql, conexion, request, ruta):
    conn = request.getfixturevalue(conexion)
    antes = sqlite3.connect(ruta).execute("SELECT COUNT(*) FROM unidades").fetchone()[0]
    with pytest.raises(consultas.ConsultaRechazada):
        consultas.ejecutar(conn, sql)
    assert sqlite3.connect(ruta).execute("SELECT COUNT(*) FROM unidades").fetchone()[0] == antes


def test_varias_sentencias_se_rechazan(ro):
    with pytest.raises(consultas.ConsultaRechazada):
        consultas.ejecutar(ro, "SELECT 1; DELETE FROM unidades")


def test_cte_recursivo_infinito_se_corta(ro):
    with pytest.raises(consultas.ConsultaRechazada, match="tardó demasiado"):
        consultas.ejecutar(ro, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
                               "SELECT COUNT(*) FROM n")


def test_errores_de_sql_se_explican(ro):
    with pytest.raises(consultas.ConsultaRechazada, match="Error de SQL"):
        consultas.ejecutar(ro, "SELECT columna_que_no_existe FROM unidades")
    with pytest.raises(consultas.ConsultaRechazada):
        consultas.ejecutar(ro, "   ")


def test_la_conexion_sigue_usable_despues_de_un_rechazo(ro):
    with pytest.raises(consultas.ConsultaRechazada):
        consultas.ejecutar(ro, "DROP TABLE unidades")
    assert consultas.ejecutar(ro, "SELECT 1")["filas"] == [[1]]
