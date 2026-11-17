"""Invariantes del simulador: los datos del demo tienen que ser coherentes,
porque el asistente responde sobre ellos y cualquier incoherencia se nota."""
import sqlite3
from datetime import date

import pytest

from flotilla import finanzas as fin
from flotilla import seed

CORTE = date(2026, 11, 17)


def _base(corte=CORTE, semilla=2026):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    seed.generar(c, corte, semilla)
    return c


@pytest.fixture(scope="module")
def conn():
    return _base()


def _dump(c):
    return [tuple(r) for t in ("unidades", "contratos", "cobranza", "gastos", "clientes")
            for r in c.execute(f"SELECT * FROM {t} ORDER BY id")]


def test_es_determinista():
    assert _dump(_base()) == _dump(_base())
    assert _dump(_base()) != _dump(_base(semilla=7))


def test_una_unidad_arrendada_tiene_exactamente_un_contrato_vigente(conn):
    for u in conn.execute("SELECT id, estado FROM unidades"):
        vigentes = conn.execute("SELECT COUNT(*) FROM contratos WHERE unidad_id=? AND estado='vigente'",
                                (u["id"],)).fetchone()[0]
        assert vigentes == (1 if u["estado"] == "arrendada" else 0), dict(u)


def test_contratos_no_se_traslapan_en_la_misma_unidad(conn):
    por_unidad = {}
    for c in conn.execute("SELECT unidad_id, inicio, fin FROM contratos ORDER BY inicio"):
        por_unidad.setdefault(c["unidad_id"], []).append((c["inicio"], c["fin"]))
    for tramos in por_unidad.values():
        for (_, fin_a), (ini_b, _) in zip(tramos, tramos[1:]):
            assert fin_a <= ini_b


def test_nada_ocurre_despues_del_corte(conn):
    corte = CORTE.isoformat()
    assert conn.execute("SELECT COUNT(*) FROM cobranza WHERE vencimiento > ? OR fecha_pago > ?",
                        (corte, corte)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM gastos WHERE fecha > ?", (corte,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM contratos WHERE inicio > ?", (corte,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM unidades WHERE fecha_compra > ? OR fecha_baja > ?",
                        (corte, corte)).fetchone()[0] == 0


def test_cobranza_coherente(conn):
    malos = conn.execute("""SELECT COUNT(*) FROM cobranza
                            WHERE pagado < 0 OR pagado > monto OR (pagado = monto) != (fecha_pago IS NOT NULL)""")
    assert malos.fetchone()[0] == 0
    # Una renta por mes desde el inicio y sin huecos.
    for c in conn.execute("SELECT id, inicio FROM contratos"):
        periodos = [r[0] for r in conn.execute("SELECT periodo FROM cobranza WHERE contrato_id=? ORDER BY periodo", (c["id"],))]
        assert periodos == list(range(1, len(periodos) + 1))


def test_la_renta_sale_de_la_formula_del_cotizador(conn):
    for c in conn.execute("""SELECT ct.*, u.costo, u.residual AS u_res, u.vida_meses, u.fecha_compra
                             FROM contratos ct JOIN unidades u ON u.id = ct.unidad_id"""):
        meses = fin.meses_entre(date.fromisoformat(c["fecha_compra"]), date.fromisoformat(c["inicio"]))
        base = fin.valor_en_libros(c["costo"], c["u_res"], c["vida_meses"], meses)
        financiera = fin.renta_mensual(base, c["plazo_meses"], c["tasa_anual"], c["residual"])
        assert c["renta_mensual"] == pytest.approx(financiera + c["cuota_servicios"], abs=0.01)


def test_unidades_vendidas_tienen_fecha_y_precio(conn):
    for u in conn.execute("SELECT * FROM unidades"):
        vendida = u["estado"] == "vendida"
        assert (u["fecha_baja"] is not None) == vendida
        assert (u["precio_venta"] is not None) == vendida


def test_la_historia_del_demo_esta_presente(conn):
    """Lo que el recorrido del demo da por hecho."""
    estados = dict(conn.execute("SELECT estado, COUNT(*) FROM unidades GROUP BY 1").fetchall())
    assert set(estados) == {"arrendada", "disponible", "taller", "vendida"}
    contratos = dict(conn.execute("SELECT estado, COUNT(*) FROM contratos GROUP BY 1").fetchall())
    assert contratos.get("cancelado", 0) >= 1 and contratos.get("terminado", 0) >= 10
    vencido_90 = conn.execute("""SELECT COUNT(*) FROM cobranza WHERE pagado < monto AND vencimiento < ?""",
                              (fin.sumar_meses(CORTE, -3).isoformat(),)).fetchone()[0]
    assert vencido_90 > 0
