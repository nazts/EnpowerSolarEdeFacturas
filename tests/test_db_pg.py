"""Pruebas de la capa de compatibilidad con Postgres (sin servidor)."""
from app.pg import _a_postgres, es_postgres, limpiar_url


def test_traduce_marcadores():
    assert _a_postgres("SELECT * FROM t WHERE a = ? AND b = ?") == \
        "SELECT * FROM t WHERE a = %s AND b = %s"
    assert _a_postgres("INSERT INTO t (a) VALUES (?)") == "INSERT INTO t (a) VALUES (%s)"


def test_escapa_porcentajes():
    """psycopg interpreta cualquier '%' como marcador: hay que duplicarlo."""
    assert _a_postgres("SELECT * FROM t WHERE nombre LIKE ? ") == \
        "SELECT * FROM t WHERE nombre LIKE %s "
    assert _a_postgres("SELECT '50%' AS x WHERE a = ?") == "SELECT '50%%' AS x WHERE a = %s"


def test_no_toca_interrogantes_entre_comillas():
    assert _a_postgres("SELECT 'que?', ? ") == "SELECT 'que?', %s "


def test_detecta_url_de_postgres():
    assert es_postgres("postgresql://u:p@host:6543/db")
    assert es_postgres("postgres://u:p@host:6543/db")
    assert not es_postgres("")
    assert not es_postgres("sqlite:///data/x.db")


def test_limpia_comillas_y_esquema():
    assert limpiar_url('"postgres://u:p@h/db"') == "postgresql://u:p@h/db"
    assert limpiar_url(" postgresql://u:p@h/db ") == "postgresql://u:p@h/db"


def test_requirements_trae_el_driver_y_el_pool():
    """Sin psycopg_pool una instalacion limpia falla al conectar a Supabase."""
    from pathlib import Path

    requisitos = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(
        encoding="utf-8").lower()
    assert "psycopg" in requisitos
    assert "psycopg_pool" in requisitos, "falta psycopg_pool en requirements.txt"
