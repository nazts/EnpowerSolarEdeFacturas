"""Tipo de proyecto: libre, editable después de creado.

El dueño pidió poder cargar datos del proyecto (tipo) sin que sean fijos:
se elige de una lista de sugerencias pero se puede escribir cualquier cosa, y
se cambia desde la ficha del cliente sin borrar el proyecto.
"""
from app import db


def _con(db_path):
    return db.sesion()


def test_crear_proyecto_guarda_el_tipo(db_path):
    with db.sesion() as c:
        db.upsert_contrato(c, "8322303", razon_social="LUZ MARIA PORTES CASTRO")
        db.crear_proyecto(c, {"nic": "8322303", "nombre": "Sede", "tipo": "Comercial"})
        fila = db.listar_proyectos(c, "8322303")[0]
    assert fila["tipo"] == "Comercial"


def test_el_tipo_se_puede_cambiar_y_es_libre(db_path):
    with db.sesion() as c:
        db.upsert_contrato(c, "8322303", razon_social="X")
        pid = db.crear_proyecto(c, {"nic": "8322303", "nombre": "Sede", "tipo": "Residencial"})
        nic = db.cambiar_tipo_proyecto(c, pid, "Industrial con respaldo")
        fila = db.listar_proyectos(c, "8322303")[0]
    assert nic == "8322303"
    assert fila["tipo"] == "Industrial con respaldo"


def test_cambiar_el_tipo_de_un_proyecto_que_no_existe_no_rompe(db_path):
    with db.sesion() as c:
        assert db.cambiar_tipo_proyecto(c, 9999, "Comercial") == ""


def test_la_ficha_tiene_el_campo_y_la_lista_de_sugerencias():
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "app" / "templates"
            / "cliente_detalle.html").read_text(encoding="utf-8")
    assert 'id="pr_tipo"' in html                       # al crear
    assert 'action="/proyectos/{{ p.id }}/tipo"' in html  # editable después
    assert 'id="tiposProyecto"' in html and "Comercial" in html
