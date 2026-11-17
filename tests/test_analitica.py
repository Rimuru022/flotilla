"""Indicadores contra un caso pequeño calculado a mano."""
import sqlite3
from datetime import date

import pytest

from flotilla import analitica, db

CORTE = date(2026, 11, 17)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init(c)
    c.executemany("INSERT INTO parametros VALUES (?, ?)", [
        ("fecha_corte", CORTE.isoformat()), ("tasa_fondeo_anual", "0.12"), ("empresa", "Prueba")])
    c.execute("INSERT INTO clientes VALUES (1, 'Cliente Uno', 'AAA010101AAA', 'Logística', 'Puebla', '2025-01-01')")
    c.execute("INSERT INTO clientes VALUES (2, 'Cliente Dos', 'BBB010101BBB', 'Retail', 'Puebla', '2025-01-01')")
    # Unidad 1: costo 360,000, residual 120,000 a 60 meses -> depreciación 4,000/mes.
    c.execute("""INSERT INTO unidades VALUES (1, 'AAA-111-A', 'Nissan', 'NP300', 2026, 'Pickup', 360000,
                 '2026-01-01', 60, 120000, 'arrendada', NULL, NULL)""")
    # Unidad 2: disponible, sin contrato, comprada antes de la ventana.
    c.execute("""INSERT INTO unidades VALUES (2, 'BBB-222-B', 'Kia', 'Seltos', 2025, 'SUV', 300000,
                 '2025-06-10', 60, 90000, 'disponible', NULL, NULL)""")
    c.execute("""INSERT INTO contratos VALUES (1, 'AR-1', 1, 1, '2026-01-01', 36, 10000, 2500, 0.18, 200000,
                 'vigente', '2029-01-01')""")
    c.execute("""INSERT INTO contratos VALUES (2, 'AR-2', 2, 2, '2025-07-01', 36, 9000, 2000, 0.18, 150000,
                 'cancelado', '2025-10-20')""")
    for k in range(11):  # enero a noviembre 2026
        venc = date(2026, k + 1, 1).isoformat()
        pagado = 0 if k >= 9 else 10000        # octubre y noviembre sin pagar
        c.execute("INSERT INTO cobranza (contrato_id, periodo, vencimiento, monto, pagado) VALUES (1, ?, ?, 10000, ?)",
                  (k + 1, venc, pagado))
    # Contrato rescindido: 3 rentas sin pagar de 2025 (más de 90 días).
    for k, venc in enumerate(["2025-08-01", "2025-09-01", "2025-10-01"]):
        c.execute("INSERT INTO cobranza (contrato_id, periodo, vencimiento, monto, pagado) VALUES (2, ?, ?, 9000, 0)",
                  (k + 3, venc))
    c.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto) VALUES (1, '2026-05-10', 'mantenimiento', 5000)")
    c.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto) VALUES (1, '2026-03-10', 'seguro', 1000)")
    c.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto) VALUES (1, '2025-06-10', 'seguro', 99999)")  # fuera de ventana
    c.commit()
    return c


def test_rentabilidad_de_una_unidad_a_mano(conn):
    r = analitica.rentabilidad_unidades(conn, meses=12)
    # Meses cerrados: noviembre 2025 a octubre 2026 (noviembre 2026 va a medias y no cuenta).
    assert (r["desde"], r["hasta"]) == ("2025-11-01", "2026-10-31")
    u = next(f for f in r["unidades"] if f["placa"] == "AAA-111-A")
    assert u["meses"] == 10                       # comprada en enero 2026
    assert u["ingreso"] == pytest.approx(100_000)  # devengado, se haya cobrado o no
    assert u["gastos"] == pytest.approx(6_000)     # el seguro de 2025 queda fuera
    assert u["mantenimiento"] == pytest.approx(5_000)
    assert u["depreciacion"] == pytest.approx(40_000)
    # Fondeo: 1% mensual sobre el valor en libros de cada mes (360,000 - 4,000 k, k = 0..9).
    assert u["costo_fondeo"] == pytest.approx(34_200)
    assert u["utilidad"] == pytest.approx(100_000 - 6_000 - 40_000 - 34_200)
    assert u["cliente"] == "Cliente Uno"


def test_unidad_ociosa_solo_cuesta(conn):
    u = next(f for f in analitica.rentabilidad_unidades(conn)["unidades"] if f["placa"] == "BBB-222-B")
    assert u["meses"] == 12 and u["ingreso"] == 0
    assert u["depreciacion"] == pytest.approx(12 * 3_500)
    assert u["utilidad"] < 0


def test_venta_de_unidad_genera_resultado(conn):
    # Se vende en octubre 2026 a 280,000; libros a 16 meses = 300,000 - 3,500 x 16 = 244,000.
    conn.execute("UPDATE unidades SET estado='vendida', fecha_baja='2026-10-10', precio_venta=280000 WHERE id=2")
    u = next(f for f in analitica.rentabilidad_unidades(conn)["unidades"] if f["placa"] == "BBB-222-B")
    assert u["resultado_venta"] == pytest.approx(280_000 - 244_000)
    assert u["meses"] == 12


def test_cartera_vencida_por_rango(conn):
    c = analitica.cartera_vencida(conn)
    assert c["total"] == pytest.approx(20_000 + 27_000)
    assert c["por_rango"]["1-30"] == pytest.approx(10_000)   # 1 nov: 16 días
    assert c["por_rango"]["31-60"] == pytest.approx(10_000)  # 1 oct: 47 días
    assert c["por_rango"]["90+"] == pytest.approx(27_000)
    primero = c["clientes"][0]
    assert primero["cliente"] == "Cliente Dos" and primero["rescindidos"] == ["AR-2"]
    assert primero["dias_max"] == (CORTE - date(2025, 8, 1)).days


def test_agrupar_por_categoria_y_rechazar_campos_raros(conn):
    g = analitica.rentabilidad_por(conn, "categoria")
    assert {x["grupo"] for x in g["grupos"]} == {"Pickup", "SUV"}
    pickup = next(x for x in g["grupos"] if x["grupo"] == "Pickup")
    assert pickup["margen"] == pytest.approx((100_000 - 6_000 - 40_000 - 34_200) / 100_000)
    with pytest.raises(ValueError):
        analitica.rentabilidad_por(conn, "placa; DROP TABLE unidades")


def test_resumen(conn):
    r = analitica.resumen(conn)
    assert r["unidades_en_flota"] == 2 and r["utilizacion"] == pytest.approx(0.5)
    assert r["contratos_vigentes"] == 1 and r["renta_mensual_vigente"] == pytest.approx(10_000)
    assert r["cartera_vencida"] == pytest.approx(47_000)


def test_serie_mensual_cuadra_con_el_acumulado(conn):
    serie = analitica.serie_mensual(conn, meses=12)
    assert [s["mes"] for s in serie][:2] == ["2025-11", "2025-12"] and serie[-1]["mes"] == "2026-10"
    total = analitica.rentabilidad_unidades(conn, meses=12)["unidades"]
    for campo in ("ingreso", "gastos", "depreciacion", "costo_fondeo", "utilidad"):
        assert sum(s[campo] for s in serie) == pytest.approx(sum(u[campo] for u in total)), campo
    assert serie[1]["cobrado"] == 0            # nadie registró fecha_pago en el caso a mano
