import os
from datetime import datetime

LOG_FILE = "logs/actividad.log"

def registrar_log(usuario, accion):
    """Registra una línea de actividad en el archivo de log."""
    if not os.path.exists("logs"):
        os.makedirs("logs")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{timestamp}] Usuario: {usuario} | Acción: {accion}\n"

    with open(LOG_FILE, "a", encoding="utf-8") as log:
        log.write(linea)
