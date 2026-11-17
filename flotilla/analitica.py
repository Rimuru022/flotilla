"""Indicadores de negocio sobre la base. Es la única capa que calcula cifras:
la web, el asistente y el servidor MCP llaman a estas funciones, así que una
misma pregunta da la misma respuesta en los tres lados.

Rentabilidad de una unidad en una ventana de meses:

    ingreso devengado  (rentas cuyo vencimiento cae en la ventana)
  + resultado por venta (precio de venta - valor en libros, si se vendió)
  - gastos operativos  (mantenimiento, seguro, GPS, tenencia, siniestros...)
  - depreciación       (línea recta del costo al residual, por mes de tenencia)
  - costo de fondeo    (valor en libros x tasa de fondeo / 12, por mes)
  = utilidad económica

Rendimiento anualizado = utilidad / valor en libros promedio x 12 / meses.
La ventana son meses calendario cerrados antes de la fecha de corte.
Montos sin IVA.
"""
from datetime import date, timedelta

from flotilla import db
from flotilla import finanzas as fin


def fecha_corte(conn):
    valor = db.parametro(conn, "fecha_corte")
    return date.fromisoformat(valor) if valor else date.today()


def _ventana(corte, meses):
    """Los últimos `meses` meses calendario cerrados antes del corte: un mes a
    medias mezclaría depreciación de 30 días con rentas de 17. Devuelve
    (desde, hasta) inclusivos."""
    primero = date(corte.year, corte.month, 1)
    return fin.sumar_meses(primero, -meses), primero - timedelta(days=1)


def _meses_de_tenencia(compra, baja, desde, hasta):
    """Primeros de mes en la ventana durante los cuales la unidad era nuestra."""
    m = date(desde.year, desde.month, 1)
    out = []
    while m <= hasta:
        fin_mes = fin.sumar_meses(m, 1)
        if compra < fin_mes and (baja is None or baja >= m):
            out.append(m)
        m = fin_mes
    return out


def rentabilidad_unidades(conn, meses=12, corte=None):
    corte = corte or fecha_corte(conn)
    desde, hasta = _ventana(corte, meses)
    tasa_fondeo = float(db.parametro(conn, "tasa_fondeo_anual", "0.115"))

    ingresos = {r["unidad_id"]: r["total"] for r in conn.execute(
        """SELECT ct.unidad_id, SUM(co.monto) AS total FROM cobranza co
           JOIN contratos ct ON ct.id = co.contrato_id
           WHERE co.vencimiento BETWEEN ? AND ? GROUP BY ct.unidad_id""",
        (desde.isoformat(), hasta.isoformat()))}
    gastos = {}
    for r in conn.execute(
            "SELECT unidad_id, tipo, SUM(monto) AS total FROM gastos WHERE fecha BETWEEN ? AND ? GROUP BY 1, 2",
            (desde.isoformat(), hasta.isoformat())):
        gastos.setdefault(r["unidad_id"], {})[r["tipo"]] = r["total"]
    clientes = {r["unidad_id"]: r["nombre"] for r in conn.execute(
        """SELECT ct.unidad_id, cl.nombre FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id
           WHERE ct.estado = 'vigente'""")}

    filas = []
    for u in conn.execute("SELECT * FROM unidades ORDER BY id"):
        compra = date.fromisoformat(u["fecha_compra"])
        baja = date.fromisoformat(u["fecha_baja"]) if u["fecha_baja"] else None
        tenencia = _meses_de_tenencia(compra, baja, desde, hasta)
        if not tenencia:
            continue
        dep_mes = fin.depreciacion_mensual(u["costo"], u["residual"], u["vida_meses"])
        libros = [fin.valor_en_libros(u["costo"], u["residual"], u["vida_meses"], fin.meses_entre(compra, m))
                  for m in tenencia]
        depreciacion = sum(dep_mes for m in tenencia
                           if fin.meses_entre(compra, m) < u["vida_meses"])
        fondeo = sum(v * tasa_fondeo / 12 for v in libros)
        venta = 0.0
        if baja and desde <= baja <= hasta and u["precio_venta"] is not None:
            venta = u["precio_venta"] - fin.valor_en_libros(
                u["costo"], u["residual"], u["vida_meses"], fin.meses_entre(compra, baja))
        g = gastos.get(u["id"], {})
        gasto_total = sum(g.values())
        ingreso = ingresos.get(u["id"], 0.0)
        utilidad = ingreso + venta - gasto_total - depreciacion - fondeo
        libros_prom = sum(libros) / len(libros)
        filas.append({
            "unidad_id": u["id"], "placa": u["placa"], "vehiculo": f"{u['marca']} {u['modelo']} {u['anio']}",
            "categoria": u["categoria"], "estado": u["estado"], "cliente": clientes.get(u["id"]),
            "meses": len(tenencia), "ingreso": ingreso, "resultado_venta": venta,
            "mantenimiento": g.get("mantenimiento", 0.0), "siniestros": g.get("siniestro", 0.0),
            "otros_gastos": gasto_total - g.get("mantenimiento", 0.0) - g.get("siniestro", 0.0),
            "gastos": gasto_total, "depreciacion": depreciacion, "costo_fondeo": fondeo,
            "utilidad": utilidad, "valor_libros_promedio": libros_prom,
            "rendimiento_anual": utilidad / libros_prom * 12 / len(tenencia) if libros_prom else 0.0,
        })
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "meses": meses, "unidades": filas}


def rentabilidad_por(conn, campo, meses=12, corte=None):
    """Agrupa la rentabilidad por 'categoria', 'vehiculo' o 'cliente'."""
    if campo not in ("categoria", "vehiculo", "cliente"):
        raise ValueError("Agrupación no válida: usa categoria, vehiculo o cliente.")
    base = rentabilidad_unidades(conn, meses, corte)
    grupos = {}
    for f in base["unidades"]:
        clave = f[campo] or "(sin contrato vigente)"
        g = grupos.setdefault(clave, {"grupo": clave, "unidades": 0, "ingreso": 0.0, "gastos": 0.0,
                                      "depreciacion": 0.0, "costo_fondeo": 0.0, "resultado_venta": 0.0,
                                      "utilidad": 0.0, "_libros_mes": 0.0})
        g["unidades"] += 1
        for k in ("ingreso", "gastos", "depreciacion", "costo_fondeo", "resultado_venta", "utilidad"):
            g[k] += f[k]
        g["_libros_mes"] += f["valor_libros_promedio"] * f["meses"]
    out = []
    for g in grupos.values():
        libros_mes = g.pop("_libros_mes")
        g["margen"] = g["utilidad"] / g["ingreso"] if g["ingreso"] else 0.0
        g["rendimiento_anual"] = g["utilidad"] * 12 / libros_mes if libros_mes else 0.0
        out.append(g)
    out.sort(key=lambda g: g["utilidad"], reverse=True)
    return {"desde": base["desde"], "hasta": base["hasta"], "agrupado_por": campo, "grupos": out}


def cartera_vencida(conn, corte=None):
    """Saldos vencidos al corte por cliente y por rango de antigüedad."""
    corte = corte or fecha_corte(conn)
    clientes = {}
    totales = {r: 0.0 for r in fin.RANGOS}
    for r in conn.execute(
            """SELECT cl.id AS cliente_id, cl.nombre, co.vencimiento, co.monto - co.pagado AS saldo,
                      ct.folio, ct.estado AS estado_contrato
               FROM cobranza co JOIN contratos ct ON ct.id = co.contrato_id
               JOIN clientes cl ON cl.id = ct.cliente_id
               WHERE co.pagado < co.monto AND co.vencimiento < ?""", (corte.isoformat(),)):
        rango = fin.rango_antiguedad(date.fromisoformat(r["vencimiento"]), corte)
        if rango is None:
            continue
        c = clientes.setdefault(r["cliente_id"], {
            "cliente_id": r["cliente_id"], "cliente": r["nombre"], "total": 0.0, "rentas": 0,
            "contratos": set(), "rescindidos": set(), "mas_antiguo": r["vencimiento"],
            **{k: 0.0 for k in fin.RANGOS}})
        c[rango] += r["saldo"]
        c["total"] += r["saldo"]
        c["rentas"] += 1
        c["contratos"].add(r["folio"])
        if r["estado_contrato"] == "cancelado":
            c["rescindidos"].add(r["folio"])
        c["mas_antiguo"] = min(c["mas_antiguo"], r["vencimiento"])
        totales[rango] += r["saldo"]
    filas = []
    for c in clientes.values():
        c["contratos"], c["rescindidos"] = sorted(c["contratos"]), sorted(c["rescindidos"])
        c["dias_max"] = (corte - date.fromisoformat(c["mas_antiguo"])).days
        filas.append(c)
    filas.sort(key=lambda c: c["total"], reverse=True)
    return {"corte": corte.isoformat(), "total": sum(totales.values()), "por_rango": totales, "clientes": filas}


def resumen(conn, corte=None):
    corte = corte or fecha_corte(conn)
    estados = {r["estado"]: r["n"] for r in conn.execute("SELECT estado, COUNT(*) AS n FROM unidades GROUP BY 1")}
    en_flota = sum(n for e, n in estados.items() if e != "vendida")
    renta_vigente = conn.execute(
        "SELECT COALESCE(SUM(renta_mensual), 0) AS t, COUNT(*) AS n FROM contratos WHERE estado = 'vigente'").fetchone()
    por_vencer = conn.execute(
        "SELECT COUNT(*) FROM contratos WHERE estado = 'vigente' AND fin BETWEEN ? AND ?",
        (corte.isoformat(), fin.sumar_meses(corte, 3).isoformat())).fetchone()[0]
    rent = rentabilidad_unidades(conn, 12, corte)
    ingreso = sum(f["ingreso"] for f in rent["unidades"])
    utilidad = sum(f["utilidad"] for f in rent["unidades"])
    cartera = cartera_vencida(conn, corte)
    facturado_12m = conn.execute("SELECT COALESCE(SUM(monto), 0) FROM cobranza WHERE vencimiento BETWEEN ? AND ?",
                                 (rent["desde"], rent["hasta"])).fetchone()[0]
    return {
        "empresa": db.parametro(conn, "empresa", ""),
        "corte": corte.isoformat(),
        "unidades": estados, "unidades_en_flota": en_flota,
        "utilizacion": estados.get("arrendada", 0) / en_flota if en_flota else 0.0,
        "contratos_vigentes": renta_vigente["n"], "renta_mensual_vigente": renta_vigente["t"],
        "contratos_por_vencer_90d": por_vencer,
        "ventana_desde": rent["desde"], "ventana_hasta": rent["hasta"],
        "ingreso_12m": ingreso, "utilidad_12m": utilidad, "margen_12m": utilidad / ingreso if ingreso else 0.0,
        "cartera_vencida": cartera["total"], "cartera_por_rango": cartera["por_rango"],
        "cartera_vs_facturado_12m": cartera["total"] / facturado_12m if facturado_12m else 0.0,
    }


def serie_mensual(conn, meses=24, corte=None):
    """Ingreso, costos y utilidad de cada uno de los últimos `meses` meses
    cerrados. Usa la misma función que la rentabilidad acumulada, mes a mes,
    así que la suma de la serie cuadra con el acumulado."""
    corte = corte or fecha_corte(conn)
    primero = date(corte.year, corte.month, 1)
    salida = []
    for k in range(meses, 0, -1):
        inicio_mes = fin.sumar_meses(primero, -k)
        siguiente = fin.sumar_meses(inicio_mes, 1)
        r = rentabilidad_unidades(conn, 1, siguiente)["unidades"]
        cobrado = conn.execute("SELECT COALESCE(SUM(pagado), 0) FROM cobranza WHERE fecha_pago >= ? AND fecha_pago < ?",
                               (inicio_mes.isoformat(), siguiente.isoformat())).fetchone()[0]
        fila = {"mes": inicio_mes.strftime("%Y-%m"), "cobrado": cobrado}
        for campo in ("ingreso", "resultado_venta", "gastos", "depreciacion", "costo_fondeo", "utilidad"):
            fila[campo] = sum(u[campo] for u in r)
        salida.append(fila)
    return salida
