"""Núcleo financiero de un arrendamiento puro de vehículos.

Funciones puras, sin base de datos, para poder probarlas contra valores
conocidos. Montos en MXN sin IVA salvo que el nombre diga lo contrario.

Convenciones:
- Tasa anual nominal, capitalización mensual: i = tasa_anual / 12.
- Renta anticipada por defecto (se paga al inicio de cada mes), que es lo
  usual en arrendamiento puro; `anticipado=False` da renta vencida.
- El residual es lo que el arrendador espera recuperar al final (venta de la
  unidad u opción de compra), en pesos.
"""
from datetime import date

IVA = 0.16

# Supuestos de costo operativo de una unidad (los usa la cuota de servicios y el
# simulador de datos, para que lo que se cobra y lo que se gasta salgan de lo mismo).
SEGURO_ANUAL_PCT = 0.034        # póliza amplia, % del valor de la unidad
GPS_MENSUAL = 390.0
TENENCIA_ANUAL_PCT = 0.006      # refrendo y derechos
VERIFICACION_SEMESTRAL = 720.0
MESES_ENTRE_SERVICIOS = 4
MARGEN_SERVICIOS = 0.25


def cuota_servicios(costo, mantenimiento_por_servicio, margen=MARGEN_SERVICIOS):
    """Cuota mensual sin IVA por los servicios incluidos en la renta (seguro,
    GPS, mantenimiento, tenencia y verificación) más el margen del arrendador."""
    if costo <= 0 or mantenimiento_por_servicio < 0 or margen < 0:
        raise ValueError("Costo, mantenimiento y margen deben ser positivos.")
    costo_mes = (costo * SEGURO_ANUAL_PCT / 12 + GPS_MENSUAL + costo * TENENCIA_ANUAL_PCT / 12
                 + VERIFICACION_SEMESTRAL / 6 + mantenimiento_por_servicio / MESES_ENTRE_SERVICIOS)
    return costo_mes * (1 + margen)


def renta_mensual(precio, plazo_meses, tasa_anual, residual=0.0, anticipado=True):
    """Renta mensual sin IVA tal que el valor presente de las rentas más el
    del residual sea igual al precio de la unidad."""
    if precio <= 0:
        raise ValueError("El precio debe ser mayor a cero.")
    if plazo_meses < 1:
        raise ValueError("El plazo debe ser de al menos un mes.")
    if tasa_anual < 0:
        raise ValueError("La tasa no puede ser negativa.")
    if residual < 0 or residual >= precio:
        raise ValueError("El residual debe estar entre cero y el precio.")

    i = tasa_anual / 12
    n = plazo_meses
    if i == 0:
        return (precio - residual) / n
    descuento = (1 + i) ** -n
    renta = (precio - residual * descuento) * i / (1 - descuento)
    return renta / (1 + i) if anticipado else renta


def cotizar(precio, plazo_meses, tasa_anual, residual_pct, comision_apertura_pct=0.0, anticipado=True,
            servicios_mensuales=0.0):
    """Cotización completa para un cliente: renta con y sin IVA, residual,
    comisión y la TIR del flujo financiero del arrendador (debe ser la tasa
    pactada; la cuota de servicios se cobra aparte del flujo financiero)."""
    if servicios_mensuales < 0:
        raise ValueError("La cuota de servicios no puede ser negativa.")
    if not 0 <= residual_pct < 1:
        raise ValueError("El residual debe estar entre 0% y menos de 100%.")
    if comision_apertura_pct < 0:
        raise ValueError("La comisión no puede ser negativa.")
    residual = precio * residual_pct
    financiera = renta_mensual(precio, plazo_meses, tasa_anual, residual, anticipado)
    renta = financiera + servicios_mensuales

    # Flujo mensual del arrendador: compra la unidad hoy, cobra rentas, recupera residual.
    flujos = [0.0] * (plazo_meses + 1)
    flujos[0] = -precio
    for k in range(plazo_meses):
        flujos[k if anticipado else k + 1] += financiera
    flujos[plazo_meses] += residual
    tir_m = tir(flujos)

    return {
        "precio": precio,
        "plazo_meses": plazo_meses,
        "tasa_anual": tasa_anual,
        "residual_pct": residual_pct,
        "residual": residual,
        "renta_financiera": financiera,
        "cuota_servicios": servicios_mensuales,
        "renta_sin_iva": renta,
        "iva_renta": renta * IVA,
        "renta_con_iva": renta * (1 + IVA),
        "comision_apertura": precio * comision_apertura_pct,
        "total_rentas_sin_iva": renta * plazo_meses,
        "anticipado": anticipado,
        "tir_mensual": tir_m,
        "tir_anual_nominal": tir_m * 12 if tir_m is not None else None,
    }


def tir(flujos, tol=1e-12, max_iter=500):
    """Tasa interna de retorno por periodo (bisección, robusta). None si los
    flujos no cambian de signo (no hay TIR)."""
    if not (any(x < 0 for x in flujos) and any(x > 0 for x in flujos)):
        return None

    def vpn(r):
        return sum(x / (1 + r) ** t for t, x in enumerate(flujos))

    lo, hi = -0.9999, 1.0
    while vpn(hi) > 0 and hi < 1e6:   # amplía el techo si la tasa es muy alta
        hi *= 2
    f_lo = vpn(lo)
    if f_lo * vpn(hi) > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        f_mid = vpn(mid)
        if abs(f_mid) < tol or (hi - lo) / 2 < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def depreciacion_mensual(costo, residual, vida_meses):
    """Línea recta del costo al residual durante la vida útil."""
    return (costo - residual) / vida_meses


def valor_en_libros(costo, residual, vida_meses, meses_transcurridos):
    meses = min(max(meses_transcurridos, 0), vida_meses)
    return costo - depreciacion_mensual(costo, residual, vida_meses) * meses


RANGOS = ("1-30", "31-60", "61-90", "90+")


def rango_antiguedad(vencimiento, corte):
    """Rango de días de atraso de un saldo al corte. None si está al corriente."""
    dias = (corte - vencimiento).days
    if dias <= 0:
        return None
    if dias <= 30:
        return "1-30"
    if dias <= 60:
        return "31-60"
    if dias <= 90:
        return "61-90"
    return "90+"


def meses_entre(inicio, fin):
    """Meses completos transcurridos de `inicio` a `fin`."""
    meses = (fin.year - inicio.year) * 12 + (fin.month - inicio.month)
    if fin.day < inicio.day:
        meses -= 1
    return max(meses, 0)


def sumar_meses(d, meses):
    """Misma fecha `meses` después; si el día no existe, el último del mes."""
    total = d.month - 1 + meses
    y, m = d.year + total // 12, total % 12 + 1
    for dia in (d.day, 30, 29, 28):
        try:
            return date(y, m, dia)
        except ValueError:
            continue
