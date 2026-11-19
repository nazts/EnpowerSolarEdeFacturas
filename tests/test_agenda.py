"""Reloj interno: la corrida de las 07:00 no puede depender del planificador."""
import datetime as dt

from app import agenda, dashboard, db


def _anotar(mensaje: str, nivel: str = "info") -> None:
    with db.sesion() as conn:
        db.log_evento(conn, nivel, mensaje)


def test_antes_de_las_siete_no_corre(db_path):
    assert agenda.debe_correr(dt.datetime(2026, 11, 18, 6, 59)) is False


def test_pasadas_las_siete_corre_si_no_se_hizo(db_path):
    assert agenda.debe_correr(dt.datetime(2026, 11, 18, 7, 5)) is True


def test_si_ya_corrio_no_repite(db_path):
    _anotar(agenda.MENSAJE)
    assert agenda.intentos_de_hoy() == 1
    assert agenda.debe_correr(dt.datetime.now()) is False


def test_reintenta_si_paso_el_tiempo(db_path):
    _anotar(agenda.MENSAJE)
    ahora = dt.datetime.now() + dt.timedelta(seconds=agenda.ESPERA + 60)
    assert agenda.debe_correr(ahora) is True


def test_no_pasa_de_los_reintentos(db_path):
    for _ in range(agenda.REINTENTOS):
        _anotar(agenda.MENSAJE)
    ahora = dt.datetime.now() + dt.timedelta(days=0, seconds=agenda.ESPERA * 5)
    assert agenda.debe_correr(ahora) is False


def test_no_arranca_dentro_de_las_pruebas():
    """En pytest el hilo no debe encenderse: lanzaría corridas reales."""
    assert agenda.arrancar() is False


# ------------------------------------------------------- búsqueda en la ficha
def _contrato(nic: str = "7000001") -> None:
    with db.sesion() as conn:
        db.upsert_contrato(conn, {
            "nic": nic, "razon_social": "CLIENTE DE PRUEBA", "proveedor": "edenorte",
            "dia_corte": 10, "activo": 1,
        })


def test_cliente_sin_facturas_hay_que_buscar(db_path):
    _contrato("7000001")
    with db.sesion() as conn:
        assert dashboard.conviene_buscar(conn, "7000001") is True


def test_cliente_con_facturas_y_busqueda_reciente_no(db_path):
    _contrato("7000002")
    with db.sesion() as conn:
        db.guardar_factura(conn, {"nic": "7000002", "ref_pago": "7000002001",
                                  "corte": "2026-11-10", "kwh": 100,
                                  "facturado": 1000.0, "estado": "descargada"})
    _anotar("Historico revisado NIC 7000002")
    with db.sesion() as conn:
        assert dashboard.conviene_buscar(conn, "7000002") is False


def test_si_la_ultima_busqueda_es_vieja_vuelve_a_buscar(db_path):
    _contrato("7000003")
    with db.sesion() as conn:
        db.guardar_factura(conn, {"nic": "7000003", "ref_pago": "7000003001",
                                  "corte": "2026-11-10", "kwh": 100,
                                  "facturado": 1000.0, "estado": "descargada"})
        db.log_evento(conn, "info", "Historico revisado NIC 7000003")
        # se envejece el evento a mano
        conn.execute("UPDATE eventos SET fecha = ? WHERE mensaje = ?",
                     ("2026-11-18 01:00:00", "Historico revisado NIC 7000003"))
        conn.commit()
    with db.sesion() as conn:
        assert dashboard.conviene_buscar(conn, "7000003", minutos=60) is True
