"""Cliente de la Oficina Virtual de Edenorte (sin navegador, solo HTTP)."""
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from typing import Iterable

import requests
from requests import Session

from . import config

CSRF_RE = re.compile(r'name="_csrf"\s+value="([^"]+)"')
CSRF_RE_ALT = re.compile(r'value="([^"]+)"\s+name="_csrf"')


class PortalError(RuntimeError):
    """El portal de Edenorte no respondio como se esperaba (falla del sitio)."""


@dataclass
class Contrato:
    nic: str
    titular: str = ""
    direccion: str = ""
    telefono: str = ""


@dataclass
class FacturaRef:
    nic: str
    ref_pago: str
    cliente: str
    tipo: str
    emision: str


@dataclass
class Historial:
    nic: str
    filas: list[FacturaRef] = field(default_factory=list)


def _text(html: str) -> str:
    """Texto plano aproximado de un fragmento HTML (sin bs4)."""
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"[ \t\r\f\v]+", " ", html)


class EdenorteClient:
    def __init__(self, email: str | None = None, password: str | None = None,
                 base_url: str | None = None, timeout: int = 45):
        self.base = (base_url or config.EDENORTE_URL).rstrip("/")
        self.email = email or config.EDENORTE_EMAIL
        self.password = password or config.EDENORTE_PASSWORD
        self.timeout = timeout
        self.http: Session = requests.Session()
        self.http.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0 Safari/537.36"),
            "Accept-Language": "es-DO,es;q=0.9,en;q=0.8",
        })
        self.logged_in = False

    # ---------------------------------------------------------------- login
    def _csrf(self, html: str) -> str | None:
        m = (CSRF_RE.search(html) or CSRF_RE_ALT.search(html)
             or re.search(r'name="csrf-token"\s+content="([^"]+)"', html))
        return m.group(1) if m else None

    def login(self) -> None:
        try:
            page = self.http.get(f"{self.base}/user/login", timeout=self.timeout)
        except requests.RequestException as exc:
            raise PortalError(f"No se pudo abrir el portal: {exc}") from exc
        if page.status_code >= 500:
            raise PortalError(f"El portal respondio {page.status_code} en el login")
        token = self._csrf(page.text)
        if not token:
            raise PortalError("No se encontro el token _csrf en el formulario de login")

        data = {
            "_csrf": token,
            "login-form[login]": self.email,
            "login-form[password]": self.password,
        }
        try:
            resp = self.http.post(f"{self.base}/user/login", data=data,
                                  timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            raise PortalError(f"Fallo el POST de login: {exc}") from exc

        body = resp.text or ""
        if resp.status_code >= 500:
            raise PortalError(f"El portal respondio {resp.status_code} al iniciar sesion")
        if "login-form[password]" in body or "/user/login" in str(resp.url):
            raise PortalError("Credenciales rechazadas o portal no dejo iniciar sesion")
        if "Salir" not in body and "Mis Contratos" not in body:
            raise PortalError("Sesion no confirmada tras el login")
        self.logged_in = True

    def _ensure(self) -> None:
        if not self.logged_in:
            self.login()

    # ------------------------------------------------------------ contratos
    def contratos(self) -> list[Contrato]:
        """Todos los contratos asociados a la cuenta."""
        self._ensure()
        resp = self.http.get(f"{self.base}/datos_contrato", timeout=self.timeout)
        if resp.status_code >= 500:
            raise PortalError(f"El portal respondio {resp.status_code} en datos de contrato")
        html = resp.text
        salida: list[Contrato] = []
        for fila in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html):
            m = re.search(r"(?is)<th[^>]*>\s*(\d{5,12})\s*</th>", fila)
            if not m:
                continue
            nic = m.group(1)
            resto = fila[m.end():]
            texto = html_lib.unescape(_text(resto))

            def campo(etiqueta: str) -> str:
                patron = rf"{etiqueta}:\s*(.*?)(?:\s*(?:Nombre Cliente|Direcci[oó]n|Tel[eé]fono):|\s*$)"
                found = re.search(patron, texto, re.S)
                return re.sub(r"\s+", " ", found.group(1)).strip() if found else ""

            contrato = Contrato(
                nic=nic,
                titular=campo("Nombre Cliente"),
                direccion=campo("Dirección"),
                telefono=campo("Teléfono"),
            )
            if contrato.nic not in {x.nic for x in salida}:
                salida.append(contrato)
        return salida

    def asociar_contrato(self, nic: str, espera_max: int = 60) -> Contrato:
        """Asocia un NIC a la cuenta si no esta y espera a que el portal lo confirme.

        El portal tarda unos segundos en reflejarlo; se consulta /datos_contrato
        hasta `espera_max` segundos antes de darse por vencido.
        """
        nic = str(nic).strip()
        self._ensure()
        try:
            page = self.http.get(f"{self.base}/agregar_contrato", timeout=self.timeout)
        except requests.RequestException as exc:
            raise PortalError(f"No se pudo abrir 'Asociar Contrato': {exc}") from exc
        token = self._csrf(page.text)
        if not token:
            raise PortalError("No se encontro el token para asociar el contrato")

        # 1. el portal valida el NIC y devuelve los datos del titular
        headers = {"X-CSRF-Token": token, "X-Requested-With": "XMLHttpRequest",
                   "Referer": f"{self.base}/agregar_contrato"}
        info = self.http.post(f"{self.base}/contrato_info?op=add",
                              data={"nic": nic}, headers=headers, timeout=self.timeout)
        fragmento = info.text or ""
        if f'name="ContratoForm[contrato]"' not in fragmento:
            raise PortalError(f"Edenorte no reconoce el NIC {nic}")

        def campo(etiqueta: str) -> str:
            m = re.search(rf"{etiqueta}:?</b>\s*([^<]+)", fragmento)
            return re.sub(r"\s+", " ", html_lib.unescape(m.group(1))).strip() if m else ""

        datos = Contrato(nic=nic, titular=campo("Nombre Cliente"),
                         direccion=campo("Direcci&oacute;n") or campo("Dirección"),
                         telefono=campo("Tel&eacute;fono") or campo("Teléfono"))

        # 2. asociar (formulario normal, sin cabecera AJAX)
        self.http.post(f"{self.base}/agregar_contrato",
                       data={"_csrf": token, "ContratoForm[contrato]": nic},
                       timeout=max(self.timeout, 90), allow_redirects=True)

        # 3. esperar a que aparezca en la cuenta
        import time as _time
        inicio = _time.time()
        while _time.time() - inicio < espera_max:
            if any(c.nic == nic for c in self.contratos()):
                self.logged_in = True
                return datos
            _time.sleep(2)
        raise PortalError(f"Edenorte no confirmo la asociacion del NIC {nic} "
                          f"en {espera_max}s; vuelve a intentarlo")

    def titular(self, nic: str) -> str:
        for c in self.contratos():
            if c.nic == str(nic):
                return c.titular
        return ""

    # ------------------------------------------------------------- historial
    def historial(self, nic: str) -> Historial:
        self._ensure()
        resp = self.http.get(f"{self.base}/historial/{nic}", timeout=self.timeout)
        if resp.status_code >= 500:
            raise PortalError(f"El portal respondio {resp.status_code} en el historial")
        html = resp.text
        hist = Historial(nic=str(nic))
        for fila in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html):
            if "factpdf" not in fila:
                continue
            ref = re.search(r"/factpdf/(\d+)", fila)
            if not ref:
                continue
            celdas = [re.sub(r"\s+", " ", _text(c)).strip()
                      for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", fila)]
            if len(celdas) < 3:
                continue
            hist.filas.append(FacturaRef(
                nic=str(nic),
                ref_pago=ref.group(1),
                cliente=celdas[0],
                tipo=celdas[1],
                emision=celdas[2],
            ))
        return hist

    def descargar_pdf(self, ref_pago: str) -> bytes:
        """Devuelve el PDF o lanza PortalError si el portal no lo tiene."""
        self._ensure()
        resp = self.http.get(f"{self.base}/factpdf/{ref_pago}", timeout=self.timeout)
        if resp.status_code >= 500:
            raise PortalError(f"El portal respondio {resp.status_code} al pedir {ref_pago}")
        if not resp.content.startswith(b"%PDF-"):
            raise PortalError(f"PDF no disponible para {ref_pago}")
        return resp.content


def abrir_sesion() -> EdenorteClient:
    c = EdenorteClient()
    c.login()
    return c
