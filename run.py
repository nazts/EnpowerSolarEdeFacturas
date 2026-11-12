"""Arranca el panel de Enpower Solar.

Uso normal (solo esta PC):
    .venv\\Scripts\\python.exe run.py

Para que lo vean otras PCs de la red:
    set HOST=0.0.0.0 && .venv\\Scripts\\python.exe run.py
    (en PowerShell: $env:HOST="0.0.0.0"; .venv\\Scripts\\python.exe run.py)

Variables utiles: HOST, PORT, COOKIE_SECURE (1 si va por HTTPS).
"""
from __future__ import annotations

import socket

import uvicorn

from app import config


def _ips_locales() -> list[str]:
    """IPv4 de esta PC en la red local (para dictar la URL al equipo)."""
    ips: set[str] = set()
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    # el truco del socket UDP no abre conexion: solo pregunta la ruta de salida
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def main() -> None:
    host, puerto = config.HOST, config.PORT
    print("=" * 62)
    print("  Enpower Solar · panel de facturas")
    print("=" * 62)
    if host in ("0.0.0.0", "::"):
        print(f"  Esta PC        http://127.0.0.1:{puerto}")
        ips = _ips_locales()
        for ip in ips:
            print(f"  Red local      http://{ip}:{puerto}")
        if not ips:
            print("  Red local      (no se detecto la IP; usa ipconfig)")
        print("  Recuerda abrir el puerto en el Firewall de Windows:")
        print(f"    netsh advfirewall firewall add rule name=\"Enpower {puerto}\" "
              f"dir=in action=allow protocol=TCP localport={puerto}")
    else:
        print(f"  http://{host}:{puerto}")
        print("  (solo esta PC; usa HOST=0.0.0.0 para la red local)")
    print("=" * 62)
    uvicorn.run("app.dashboard:app", host=host, port=puerto, log_level="info")


if __name__ == "__main__":
    main()
