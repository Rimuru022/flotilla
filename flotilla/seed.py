"""Arrendadora ficticia con 4 años de historia, para el demo y las pruebas.

Simulación mes a mes y determinista (misma fecha de corte + semilla = misma
base). Las rentas se calculan con `finanzas.renta_mensual`, igual que en una
cotización real, así que el demo ejercita las mismas fórmulas que el cotizador.

Todo es inventado: empresas, RFC, placas y montos.
"""
import random
from datetime import date, timedelta

from flotilla import db
from flotilla import finanzas as fin

EMPRESA = "Arrendadora Demo del Centro"
TASA_FONDEO = 0.115          # costo anual del dinero con que se compran las unidades
HISTORIA_MESES = 48

# marca, modelo, categoría, precio de lista sin IVA, mantenimiento base por servicio
CATALOGO = [
    ("Nissan", "Versa Sense", "Sedán", 285_000, 3_400),
    ("Chevrolet", "Aveo LS", "Sedán", 255_000, 3_200),
    ("Volkswagen", "Virtus", "Sedán", 335_000, 3_900),
    ("Toyota", "Corolla", "Sedán", 410_000, 4_100),
    ("Nissan", "Kicks", "SUV", 385_000, 4_300),
    ("Kia", "Seltos", "SUV", 430_000, 4_500),
    ("Mazda", "CX-5", "SUV", 565_000, 5_200),
    ("Toyota", "RAV4", "SUV", 625_000, 5_400),
    ("Nissan", "NP300", "Pickup", 405_000, 5_600),
    ("Toyota", "Hilux", "Pickup", 565_000, 6_100),
    ("Ford", "Ranger", "Pickup", 615_000, 6_300),
    ("Chevrolet", "Tornado Van", "Van", 295_000, 4_800),
    ("Nissan", "Urvan", "Van", 655_000, 7_400),
    ("Toyota", "Hiace", "Van", 785_000, 7_900),
]
# Demanda por categoría (pesos relativos al elegir qué comprar)
PESO_CATEGORIA = {"Sedán": 4, "SUV": 3, "Pickup": 4, "Van": 2}

SECTORES = {
    "Logística": ["Pickup", "Van"], "Farmacéutica": ["Sedán", "SUV"], "Construcción": ["Pickup"],
    "Alimentos y bebidas": ["Van", "Pickup", "Sedán"], "Telecomunicaciones": ["Pickup", "Sedán"],
    "Seguridad privada": ["Sedán", "Pickup"], "Retail": ["Sedán", "Van"], "Servicios médicos": ["SUV", "Van"],
    "Agroindustria": ["Pickup"], "Consultoría": ["Sedán", "SUV"],
}
NOMBRES = [
    "Logística Express del Bajío", "Distribuidora Sierra Madre", "Farmacias Del Valle Norte", "Constructora Puente Alto",
    "Alimentos La Huerta Fina", "Telecom Enlace Total", "Grupo Vigía Seguridad", "Comercializadora Punto Azul",
    "Clínicas Salud Integral", "Agroexportadora Campo Verde", "Consultores Asociados Lumbre", "Transportes Ruta 57",
    "Laboratorios Céfiro", "Edificaciones Cantera", "Bebidas Manantial Claro", "Redes y Fibra Oriente",
    "Protección Ejecutiva Atlas", "Tiendas Mercado Vivo", "Hospital San Rafael Demo", "Granos del Pacífico",
    "Asesoría Fiscal Norte", "Paquetería Veloz MX", "Distribuidora Médica Alfa", "Obras Civiles Roca Firme",
    "Panificadora El Trigal", "Conectividad Andina", "Custodia y Valores Sigma", "Abarrotes La Esquina Grande",
    "Laboratorio Clínico Bios", "Invernaderos Altiplano", "Grupo Consultor Meridiano", "Fletes y Mudanzas Centauro",
    "Farmacéutica Nova Salud", "Pavimentos del Norte", "Lácteos Pradera Alta", "Instalaciones Eléctricas Volta",
    "Vigilancia Integral Escudo", "Boutiques Moda Urbana", "Ambulancias Pronto Auxilio", "Hortalizas Santa Fe Demo",
    "Despacho Jurídico Balanza", "Cadena de Frío Polar", "Material Eléctrico Faro", "Purificadora Agua Viva",
]
CIUDADES = ["Ciudad de México", "Monterrey", "Guadalajara", "Querétaro", "Puebla", "León", "Toluca", "San Luis Potosí"]
LETRAS = "ABCDEFGHJKLMNPRSTUVWXYZ"

# Clientes que dejan de pagar: índice en NOMBRES -> hace cuántos meses. Fijo para
# que el demo siempre cuente la misma historia: dos rescindidos y dos en riesgo.
MOROSOS_DEMO = {1: 15, 9: 9, 21: 4, 36: 2}

# Perfil de pago: (probabilidad, retraso típico en días al pagar)
PERFILES = {"puntual": 0.64, "ocasional": 0.25, "moroso": 0.11}


def _rfc(rng, alta):
    return ("".join(rng.choice(LETRAS) for _ in range(3)) + alta.strftime("%y%m%d")
            + "".join(rng.choice(LETRAS + "0123456789") for _ in range(3)))


def _placa(rng, usadas):
    while True:
        p = f"{''.join(rng.choice(LETRAS) for _ in range(3))}-{rng.randint(100, 999)}-{rng.choice(LETRAS)}"
        if p not in usadas:
            usadas.add(p)
            return p


def _retraso(rng, perfil):
    """Días entre el vencimiento y el pago, según el perfil del cliente."""
    if perfil == "puntual":
        return rng.randint(-3, 4)
    if perfil == "ocasional":
        return rng.randint(-2, 6) if rng.random() < 0.72 else rng.randint(12, 48)
    return rng.randint(18, 75) if rng.random() < 0.8 else rng.randint(80, 130)


def generar(conn, corte=None, semilla=2026):
    corte = corte or date.today()
    rng = random.Random(f"{semilla}:{corte.isoformat()}")
    db.init(conn)
    inicio = fin.sumar_meses(date(corte.year, corte.month, 1), -HISTORIA_MESES)

    conn.executemany("INSERT INTO parametros (clave, valor) VALUES (?, ?)", [
        ("empresa", EMPRESA), ("tasa_fondeo_anual", str(TASA_FONDEO)), ("fecha_corte", corte.isoformat()),
        ("iva", str(fin.IVA)),
    ])

    # ── Clientes ──
    clientes = []
    for i, nombre in enumerate(NOMBRES):
        sector = list(SECTORES)[i % len(SECTORES)]
        problema = MOROSOS_DEMO.get(i)
        alta = fin.sumar_meses(inicio, rng.randint(0, 10 if problema else HISTORIA_MESES - 4))
        perfil = "moroso" if problema else rng.choices(list(PERFILES), weights=PERFILES.values())[0]
        sufijo = rng.choice(["S.A. de C.V.", "S. de R.L. de C.V.", "S.A.P.I. de C.V."])
        cur = conn.execute("INSERT INTO clientes (nombre, rfc, sector, ciudad, alta) VALUES (?, ?, ?, ?, ?)",
                           (f"{nombre} {sufijo}", _rfc(rng, alta), sector, rng.choice(CIUDADES), alta.isoformat()))
        clientes.append({"id": cur.lastrowid, "sector": sector, "alta": alta, "perfil": perfil,
                         "paciencia": rng.randint(3, 5), "activos": 0,
                         "tope": 3 if problema else rng.choice([4, 6, 8, 12]),
                         "deja_de_pagar": fin.sumar_meses(corte, -problema) if problema else None})

    placas = set()
    unidades = []       # dicts con estado de simulación
    contratos = []
    folio = 1000

    def comprar(mes, n):
        for _ in range(n):
            pesos = [PESO_CATEGORIA[c[2]] for c in CATALOGO]
            marca, modelo, cat, lista, mtto = rng.choices(CATALOGO, weights=pesos)[0]
            costo = round(lista * rng.uniform(0.9, 0.97) * (1 + 0.004 * fin.meses_entre(inicio, mes)), -2)
            u = {"marca": marca, "modelo": modelo, "categoria": cat, "costo": costo, "mtto": mtto,
                 "compra": min(mes.replace(day=rng.randint(2, 26)), corte), "vida": 60, "residual": round(costo * 0.32, -2),
                 "anio": mes.year + (1 if mes.month >= 9 else 0), "placa": _placa(rng, placas),
                 "contrato": None, "libre_desde": None, "taller_hasta": None, "vendida": None}
            u["libre_desde"] = u["compra"]
            unidades.append(u)

    def renta_para(u, mes, plazo, tasa):
        """El arrendador recupera el valor en libros actual menos el valor
        esperado al terminar el plazo (más el rendimiento)."""
        transcurridos = fin.meses_entre(u["compra"], mes)
        base = fin.valor_en_libros(u["costo"], u["residual"], u["vida"], transcurridos)
        res_final = fin.valor_en_libros(u["costo"], u["residual"], u["vida"], transcurridos + plazo)
        residual = max(res_final, u["costo"] * 0.22)
        residual = min(residual, base * 0.9)
        return base, residual, fin.renta_mensual(base, plazo, tasa, residual, anticipado=True)

    mes = inicio
    while mes <= corte:
        # El primer año se arma la flota base; después crece al ritmo de la demanda.
        if fin.meses_entre(inicio, mes) < 12:
            comprar(mes, rng.randint(3, 5))
        else:
            comprar(mes, rng.choice([1, 2, 2, 3, 3, 4]))
        for u in unidades:
            if u["vendida"]:
                continue
            c = u["contrato"]
            # Fin de contrato (pactado) o cancelación por falta de pago.
            if c and c["estado"] == "vigente":
                cli = c["cliente"]
                # Tras 3 a 5 meses sin pago se rescinde y se recupera la unidad;
                # el saldo queda como cartera vencida en cobranza judicial.
                if cli["deja_de_pagar"] and mes >= fin.sumar_meses(cli["deja_de_pagar"], cli["paciencia"]):
                    c["estado"], c["fin"] = "cancelado", min(mes.replace(day=rng.randint(5, 25)), corte)
                    cli["activos"] -= 1
                    u["contrato"], u["taller_hasta"] = None, fin.sumar_meses(mes, rng.choice([1, 2]))
                    u["libre_desde"] = c["fin"]
                    continue
                if c["fin"] <= mes:
                    c["estado"] = "terminado"
                    cli["activos"] -= 1
                    u["contrato"], u["libre_desde"] = None, c["fin"]
                    if rng.random() < 0.3:     # reacondicionamiento antes de volver a colocarse
                        u["taller_hasta"] = fin.sumar_meses(mes, rng.choice([1, 1, 2]))
                continue
            if u["taller_hasta"] and mes < u["taller_hasta"]:
                continue
            u["taller_hasta"] = None
            edad = fin.meses_entre(u["compra"], mes)
            # Unidades viejas que vuelven libres se venden.
            if edad >= 30 and rng.random() < 0.35:
                valor = fin.valor_en_libros(u["costo"], u["residual"], u["vida"], edad)
                u["vendida"] = (min(mes.replace(day=rng.randint(3, 27)), corte), round(valor * rng.uniform(0.94, 1.1), -2))
                continue
            # ¿Se coloca este mes? Demanda moderada: siempre hay algo de flota ociosa.
            if rng.random() < 0.58:
                candidatos = [cl for cl in clientes if cl["alta"] <= mes
                              and u["categoria"] in SECTORES[cl["sector"]]
                              and not (cl["deja_de_pagar"] and mes >= fin.sumar_meses(cl["deja_de_pagar"], -1))
                              and cl["activos"] < cl["tope"]]
                if not candidatos:
                    continue
                cli = rng.choice(candidatos)
                plazo = rng.choice([24, 36, 36, 36, 48])
                riesgo = {"puntual": 0.0, "ocasional": 0.012, "moroso": 0.02}[cli["perfil"]]
                tasa = round(rng.uniform(0.178, 0.205) + riesgo, 4)
                inicio_c = max(mes.replace(day=rng.randint(1, 26)), u["compra"])
                if inicio_c > corte:
                    continue
                _base, residual, financiera = renta_para(u, inicio_c, plazo, tasa)
                servicios = round(fin.cuota_servicios(u["costo"], u["mtto"]), 2)
                folio += 1
                c = {"folio": f"AR-{inicio_c.year}-{folio}", "unidad": u, "cliente": cli, "inicio": inicio_c,
                     "plazo": plazo, "renta": round(financiera + servicios, 2), "servicios": servicios, "tasa": tasa, "residual": round(residual, 2),
                     "estado": "vigente", "fin": fin.sumar_meses(inicio_c, plazo)}
                contratos.append(c)
                u["contrato"] = c
                cli["activos"] += 1
        mes = fin.sumar_meses(mes, 1)

    # Siempre hay al menos una unidad en taller al corte (la última que se devolvió
    # entra a reacondicionamiento) para que el tablero muestre los cuatro estados.
    libres = [u for u in unidades if not u["vendida"] and not u["contrato"]]
    if libres and not any(u["taller_hasta"] and u["taller_hasta"] > corte for u in libres) and len(libres) >= 2:
        max(libres, key=lambda u: u["libre_desde"])["taller_hasta"] = fin.sumar_meses(corte, 1)

    # ── Escribir unidades ──
    for u in unidades:
        if u["vendida"]:
            estado = "vendida"
        elif u["contrato"] and u["contrato"]["estado"] == "vigente":
            estado = "arrendada"
        elif u["taller_hasta"] and u["taller_hasta"] > corte:
            estado = "taller"
        else:
            estado = "disponible"
        cur = conn.execute(
            """INSERT INTO unidades (placa, marca, modelo, anio, categoria, costo, fecha_compra, vida_meses, residual,
                                     estado, fecha_baja, precio_venta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (u["placa"], u["marca"], u["modelo"], u["anio"], u["categoria"], u["costo"], u["compra"].isoformat(),
             u["vida"], u["residual"], estado, u["vendida"][0].isoformat() if u["vendida"] else None,
             u["vendida"][1] if u["vendida"] else None))
        u["id"] = cur.lastrowid

    # ── Contratos y cobranza ──
    for c in contratos:
        cur = conn.execute(
            """INSERT INTO contratos (folio, unidad_id, cliente_id, inicio, plazo_meses, renta_mensual, cuota_servicios, tasa_anual,
                                      residual, estado, fin) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (c["folio"], c["unidad"]["id"], c["cliente"]["id"], c["inicio"].isoformat(), c["plazo"], c["renta"], c["servicios"],
             c["tasa"], c["residual"], c["estado"], c["fin"].isoformat()))
        cid = cur.lastrowid
        cli = c["cliente"]
        for k in range(c["plazo"]):
            venc = fin.sumar_meses(c["inicio"], k)
            if venc > corte or venc >= c["fin"]:
                break
            if cli["deja_de_pagar"] and venc >= cli["deja_de_pagar"]:
                pago = None
            else:
                pago = venc + timedelta(days=_retraso(rng, cli["perfil"]))
            if pago and pago <= corte:
                conn.execute("INSERT INTO cobranza (contrato_id, periodo, vencimiento, monto, pagado, fecha_pago) "
                             "VALUES (?,?,?,?,?,?)", (cid, k + 1, venc.isoformat(), c["renta"], c["renta"], pago.isoformat()))
            else:
                conn.execute("INSERT INTO cobranza (contrato_id, periodo, vencimiento, monto, pagado) VALUES (?,?,?,?,0)",
                             (cid, k + 1, venc.isoformat(), c["renta"]))

    # ── Gastos ──
    for u in unidades:
        hasta = u["vendida"][0] if u["vendida"] else corte
        m = u["compra"]
        n = 0
        prox_mtto = fin.MESES_ENTRE_SERVICIOS
        while m <= hasta:
            def dia():
                return min(m.replace(day=rng.randint(1, 28)), hasta).isoformat()
            conn.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto, descripcion) VALUES (?,?,?,?,?)",
                         (u["id"], m.isoformat(), "seguro", round(u["costo"] * fin.SEGURO_ANUAL_PCT / 12, 2), "Póliza amplia (prorrata mensual)"))
            conn.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto, descripcion) VALUES (?,?,?,?,?)",
                         (u["id"], m.isoformat(), "gps", fin.GPS_MENSUAL, "Rastreo satelital"))
            if n and n % prox_mtto == 0:
                monto = round(u["mtto"] * rng.uniform(0.8, 1.25) * (1 + 0.005 * n), 2)
                conn.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto, descripcion) VALUES (?,?,?,?,?)",
                             (u["id"], dia(), "mantenimiento", monto, f"Servicio {n // prox_mtto} ({n} meses)"))
            if n and n % 12 == 0:
                conn.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto, descripcion) VALUES (?,?,?,?,?)",
                             (u["id"], dia(), "tenencia", round(u["costo"] * fin.TENENCIA_ANUAL_PCT, 2), "Refrendo y derechos vehiculares"))
            if n and n % 6 == 0:
                conn.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto, descripcion) VALUES (?,?,?,?,?)",
                             (u["id"], dia(), "verificacion", fin.VERIFICACION_SEMESTRAL, "Verificación vehicular"))
            if rng.random() < 0.006:
                monto = round(rng.uniform(9_000, 38_000), 2)
                conn.execute("INSERT INTO gastos (unidad_id, fecha, tipo, monto, descripcion) VALUES (?,?,?,?,?)",
                             (u["id"], dia(), "siniestro", monto, "Deducible y gastos no cubiertos por siniestro"))
            n += 1
            m = fin.sumar_meses(u["compra"], n)
    conn.commit()
    return {
        "unidades": len(unidades), "contratos": len(contratos), "clientes": len(clientes),
        "corte": corte.isoformat(),
    }
