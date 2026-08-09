# Componentes de interfaz Green Diamond CRM

## Principios

La UI principal está en español, usa progressive disclosure y evita exponer infraestructura. Verde significa correcto; amarillo, atención; rojo, error o riesgo; gris, deshabilitado/sin datos; azul, información o acción. Icono y texto acompañan siempre al color.

## Status Badge

Estados visibles: **Conectado**, **Falta configurar**, **Requiere atención**, **Error de conexión**, **Deshabilitado**. El badge nunca aparece sin explicación y una acción cuando corresponde.

## KPI Card

Incluye nombre, valor y contexto. Mientras carga muestra `Cargando…`; sin evidencia muestra `Sin datos`, nunca un número simulado. KPI técnicos conocidos mantienen tooltip o ayuda contextual con definición y fórmula.

## Empty State

Explica qué falta, por qué la lista está vacía y cuál es la siguiente acción. No usar `No data` ni un guion aislado.

## Error State

Primero presenta lenguaje operativo y acción de recuperación. Detalles técnicos sanitizados sólo mediante progressive disclosure para roles autorizados.

## Filter Bar

Agrupa filtros principales antes de la colección. Conserva defaults razonables y debe permitir volver a “Todos”. En tablet se convierte en una columna.

## Integration Card

Orden: servicio, sitio, estado, detectado, pendiente, fechas y acciones. Desktop usa 2–3 columnas; nunca ocho tarjetas comprimidas por fila.

## Credential Dialog

Campos `password`, confirmación, `autocomplete=new-password` y siempre vacíos. Acciones permitidas: configurar, reemplazar, verificar y desconectar. Prohibidas: mostrar, copiar o recuperar.

## Confirmation Dialog

Se usa para eliminar, deshabilitar o revocar. Declara impacto y qué datos se conservan. No se usa en acciones triviales.

## Site Health Issue

Traduce señales a **CRÍTICO**, **IMPORTANTE** o **MEJORA**. Ejemplo: `canonical=false` se muestra como “Falta la etiqueta canonical”.

## QA de aceptación UX

- ¿Se entiende dónde está y para qué sirve?
- ¿Los estados y errores son accionables?
- ¿Existe loading, empty y fallo aislado?
- ¿RBAC se valida también en backend?
- ¿Los secretos son write-only?
- ¿Las acciones peligrosas confirman impacto?
- ¿La vista conserva foco, contraste y degradación tablet?
- ¿Existe ayuda contextual?
