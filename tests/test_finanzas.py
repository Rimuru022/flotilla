"""Núcleo financiero. Cada fórmula se prueba contra un valor externo conocido
o contra una propiedad que no depende de la implementación (por ejemplo: el
valor presente de las rentas más el residual debe regresar al precio)."""
from datetime import date

import pytest

from flotilla import finanzas as f


# ─── Renta de arrendamiento ──────────────────────────────────────────────────


def test_pago_vencido_coincide_con_valor_de_libro():
    # Préstamo clásico: $100,000 al 12% anual, 12 meses, pago vencido.
    # PMT(1%, 12, -100000) = 8,884.88 (cualquier calculadora financiera).
    r = f.renta_mensual(100_000, plazo_meses=12, tasa_anual=0.12, residual=0, anticipado=False)
    assert r == pytest.approx(8884.88, abs=0.01)


def test_tasa_cero_es_reparto_lineal():
    assert f.renta_mensual(120_000, 12, 0.0, residual=24_000) == pytest.approx(8000.0)


@pytest.mark.parametrize("anticipado", [True, False])
@pytest.mark.parametrize("precio,plazo,tasa,residual", [
    (450_000, 36, 0.165, 135_000),
    (780_000, 48, 0.14, 156_000),
    (310_000, 24, 0.19, 124_000),
])
def test_valor_presente_de_rentas_y_residual_regresa_al_precio(precio, plazo, tasa, residual, anticipado):
    renta = f.renta_mensual(precio, plazo, tasa, residual, anticipado=anticipado)
    i = tasa / 12
    vp = sum(renta / (1 + i) ** (k if not anticipado else k - 1) for k in range(1, plazo + 1))
    vp += residual / (1 + i) ** plazo
    assert vp == pytest.approx(precio, rel=1e-9)


def test_pago_anticipado_es_menor_que_vencido():
    a = f.renta_mensual(450_000, 36, 0.16, 135_000, anticipado=True)
    v = f.renta_mensual(450_000, 36, 0.16, 135_000, anticipado=False)
    assert a < v
    assert a == pytest.approx(v / (1 + 0.16 / 12))


def test_mas_residual_baja_la_renta():
    bajo = f.renta_mensual(450_000, 36, 0.16, 90_000)
    alto = f.renta_mensual(450_000, 36, 0.16, 180_000)
    assert alto < bajo


@pytest.mark.parametrize("kwargs", [
    {"precio": 0}, {"precio": -1}, {"plazo_meses": 0}, {"tasa_anual": -0.01},
    {"residual": -1}, {"residual": 500_000},
])
def test_entradas_invalidas_se_rechazan(kwargs):
    base = {"precio": 450_000, "plazo_meses": 36, "tasa_anual": 0.16, "residual": 100_000}
    base.update(kwargs)
    with pytest.raises(ValueError):
        f.renta_mensual(**base)


def test_cotizacion_desglosa_iva_y_totales():
    c = f.cotizar(precio=450_000, plazo_meses=36, tasa_anual=0.165, residual_pct=0.30, comision_apertura_pct=0.02)
    assert c["residual"] == pytest.approx(135_000)
    assert c["iva_renta"] == pytest.approx(c["renta_sin_iva"] * 0.16)
    assert c["renta_con_iva"] == pytest.approx(c["renta_sin_iva"] * 1.16)
    assert c["comision_apertura"] == pytest.approx(9_000)
    assert c["total_rentas_sin_iva"] == pytest.approx(c["renta_sin_iva"] * 36)
    # La TIR del flujo del arrendador (precio contra rentas + residual) es la tasa pactada.
    assert c["tir_anual_nominal"] == pytest.approx(0.165, abs=1e-6)


def test_cuota_de_servicios_se_suma_a_la_renta_y_no_cambia_la_tir():
    sin = f.cotizar(450_000, 36, 0.165, 0.30)
    con = f.cotizar(450_000, 36, 0.165, 0.30, servicios_mensuales=3_000)
    assert con["renta_sin_iva"] == pytest.approx(sin["renta_sin_iva"] + 3_000)
    assert con["renta_financiera"] == pytest.approx(sin["renta_sin_iva"])
    assert con["iva_renta"] == pytest.approx(con["renta_sin_iva"] * 0.16)
    assert con["tir_anual_nominal"] == pytest.approx(0.165, abs=1e-6)


def test_cuota_de_servicios_cubre_el_costo_mas_margen():
    # $450,000: seguro 1,275 + GPS 390 + tenencia 225 + verificación 120 + mantenimiento 4,000/4 = 3,010
    assert f.cuota_servicios(450_000, 4_000, margen=0) == pytest.approx(3_010)
    assert f.cuota_servicios(450_000, 4_000) == pytest.approx(3_010 * 1.25)
    with pytest.raises(ValueError):
        f.cuota_servicios(0, 4_000)


# ─── TIR ─────────────────────────────────────────────────────────────────────


def test_tir_de_flujos_conocidos():
    # -1000 hoy, +1100 en un periodo: 10%.
    assert f.tir([-1000, 1100]) == pytest.approx(0.10, abs=1e-9)
    # Préstamo del valor de libro: 1% mensual.
    flujos = [-100_000] + [8884.878867834] * 12
    assert f.tir(flujos) == pytest.approx(0.01, abs=1e-9)


def test_tir_sin_cambio_de_signo_no_existe():
    assert f.tir([100, 200, 300]) is None
    assert f.tir([-100, -200]) is None


# ─── Depreciación y valor en libros ──────────────────────────────────────────


def test_depreciacion_lineal_hasta_el_residual():
    assert f.depreciacion_mensual(480_000, residual=120_000, vida_meses=36) == pytest.approx(10_000)
    assert f.valor_en_libros(480_000, 120_000, 36, meses_transcurridos=12) == pytest.approx(360_000)
    # Nunca baja del residual aunque pase la vida útil.
    assert f.valor_en_libros(480_000, 120_000, 36, meses_transcurridos=60) == pytest.approx(120_000)


# ─── Antigüedad de cartera ───────────────────────────────────────────────────


def test_rango_de_antiguedad():
    corte = date(2026, 11, 17)
    assert f.rango_antiguedad(date(2026, 11, 17), corte) is None       # vence hoy: al corriente
    assert f.rango_antiguedad(date(2026, 11, 20), corte) is None       # aún no vence
    assert f.rango_antiguedad(date(2026, 11, 16), corte) == "1-30"
    assert f.rango_antiguedad(date(2026, 10, 18), corte) == "1-30"     # 30 días
    assert f.rango_antiguedad(date(2026, 10, 17), corte) == "31-60"    # 31 días
    assert f.rango_antiguedad(date(2026, 9, 17), corte) == "61-90"
    assert f.rango_antiguedad(date(2026, 8, 19), corte) == "61-90"     # 90 días
    assert f.rango_antiguedad(date(2026, 8, 18), corte) == "90+"       # 91 días


def test_meses_entre():
    assert f.meses_entre(date(2026, 1, 31), date(2026, 2, 28)) == 0
    assert f.meses_entre(date(2026, 1, 15), date(2026, 2, 15)) == 1
    assert f.meses_entre(date(2025, 11, 1), date(2026, 11, 17)) == 12
