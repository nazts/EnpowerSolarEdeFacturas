"""El historial de Edenorte es la única fuente de la referencia de pago.

La Ref. Pago **no se calcula ni se deduce** (no es NIC + sufijo fijo): cada
factura trae la suya en el enlace `/factpdf/{ref}` de la fila del historial, y
cuando el portal emite una nueva aparece una fila nueva con su propia
referencia. Este archivo fija ese contrato.
"""
from app.edenorte import EdenorteClient

HTML = """
<table>
  <thead><tr><th>Cliente</th><th>Tipo Factura</th><th>Fecha Emisión</th><th></th></tr></thead>
  <tbody>
    <tr>
      <td>JORGE RAMON DE LA CRUZ TAVAREZ</td>
      <td>Recibos de consumos</td>
      <td>16 Oct 2026</td>
      <td><a target="_blank" href="/factpdf/6997885045"><i class="fa"></i> Ver factura</a></td>
    </tr>
    <tr>
      <td>JORGE RAMON DE LA CRUZ TAVAREZ</td>
      <td>Recibos de consumos</td>
      <td>16 Sep 2026</td>
      <td><a target="_blank" href="/factpdf/6997885044">Ver factura</a></td>
    </tr>
    <tr>
      <td>JORGE RAMON DE LA CRUZ TAVAREZ</td>
      <td>Irregularidades intencionales</td>
      <td>28 Aug 2026</td>
      <td><a href="/factpdf/6997885042">Ver factura</a></td>
    </tr>
    <tr>
      <td>JORGE RAMON DE LA CRUZ TAVAREZ</td>
      <td>Recibos de consumos</td>
      <td>16 Jun 2026</td>
      <td><a href="https://ofv.edenorte.com.do/factpdf/6997885039">Ver factura</a></td>
    </tr>
  </tbody>
</table>
"""


class _Respuesta:
    status_code = 200

    def __init__(self, texto):
        self.text = texto


class _Http:
    """Captura la URL que se pide y devuelve el HTML del historial."""

    def __init__(self, texto=HTML):
        self.texto = texto
        self.urls = []

    def get(self, url, timeout=None, **kwargs):
        self.urls.append(url)
        return _Respuesta(self.texto)


def _cliente(texto=HTML):
    c = EdenorteClient(email="x@y.z", password="no-se-usa")
    c._ensure = lambda: None                              # sin login real
    c.http = _Http(texto)                                 # type: ignore[assignment]
    return c


def test_la_referencia_sale_del_enlace_de_cada_fila():
    hist = _cliente().historial("6997885")

    assert [f.ref_pago for f in hist.filas] == [
        "6997885045", "6997885044", "6997885042", "6997885039"]
    assert [f.tipo for f in hist.filas][2] == "Irregularidades intencionales"
    assert [f.emision for f in hist.filas][0] == "16 Oct 2026"


def test_la_url_que_se_pide_es_el_historial_del_nic():
    c = _cliente()
    c.historial("6997885")
    assert c.http.urls == [f"{c.base}/historial/6997885"]   # type: ignore[attr-defined]


def test_una_factura_nueva_trae_su_propia_referencia():
    """Cuando el portal emite otra factura, aparece una fila con un ref nuevo."""
    nueva = """<tr><td>JORGE</td><td>Recibos de consumos</td><td>16 Nov 2026</td>
               <td><a href="/factpdf/6997885047">Ver factura</a></td></tr>"""
    texto = HTML.replace("<tbody>", "<tbody>" + nueva)      # el portal lista lo nuevo arriba

    hist = _cliente(texto).historial("6997885")

    # la nueva va primera y no reemplaza ni renumera a las anteriores
    assert hist.filas[0].ref_pago == "6997885047"
    assert [f.ref_pago for f in hist.filas[1:]] == [
        "6997885045", "6997885044", "6997885042", "6997885039"]


def test_no_se_inventan_referencias_sin_enlace():
    """Una fila sin /factpdf/ no produce factura (no se deduce del NIC)."""
    sin_enlace = """<tr><td>JORGE</td><td>Recibos de consumos</td>
                    <td>16 May 2026</td><td>—</td></tr>"""
    texto = HTML.replace("</tbody>", sin_enlace + "</tbody>")

    hist = _cliente(texto).historial("6997885")

    assert len(hist.filas) == 4
    assert all(f.ref_pago.startswith("6997885") for f in hist.filas)
    assert "6997885" not in [f.ref_pago for f in hist.filas]      # nunca el NIC pelado
