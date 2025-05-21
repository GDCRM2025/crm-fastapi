from datetime import datetime

def obtener_fecha_actual():
    return datetime.now().strftime("%d/%m/%Y")

def obtener_dia_mes_anio(fecha_str):
    fecha = datetime.strptime(fecha_str, "%Y-%m-%d")
    return fecha.strftime("%A"), fecha.strftime("%B"), fecha.strftime("%Y")

def obtener_segmentacion(monto):
    try:
        monto = float(monto)
        if monto >= 700000:
            return "Tipo 1"
        elif monto >= 400000:
            return "Tipo 2"
        else:
            return "Tipo 3"
    except:
        return "Sin Clasificar"
