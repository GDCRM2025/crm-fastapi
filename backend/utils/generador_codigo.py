# backend/utils/generador_codigo.py

def generar_codigo_cliente(marca: str, nombre: str, numero: int):
    sigla = {
        "CAMALEON": "CAM",
        "GOURMET": "GOU",
        "EXPRESS": "EXP",
        "DEL SABOR": "DEL"
    }.get(marca.upper(), "GEN")

    iniciales = ''.join([x[0] for x in nombre.upper().split() if x])[:2]
    correlativo = str(numero).zfill(3)

    return f"{sigla}-{iniciales}-{correlativo}"
