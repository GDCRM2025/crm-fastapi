# Prompts para trabajo paralelo por ramas

## Reglas comunes para todos los GPT

Pega primero este bloque y después uno de los encargos específicos.

```text
Trabajas en el CRM Green Diamond. Tu trabajo debe quedar aislado en la rama que
te indico y en un worktree propio. No edites el worktree principal, porque tiene
cambios sin commit pertenecientes al usuario.

Reglas obligatorias:
1. Antes de editar, ejecuta pwd, git status --short, git branch --show-current,
   git log -1 --oneline y busca AGENTS.md.
2. Verifica que estás en la rama asignada. Si no existe, créala con prefijo
   codex/. No cambies de rama en el workspace principal.
3. Primero audita el código, esquema, rutas y pruebas relacionadas. Documenta
   hallazgos concretos y diferencia datos reales de supuestos.
4. No despliegues producción, no reinicies servicios, no cambies DNS, Meta,
   credenciales, WireGuard ni datos contables reales.
5. No copies secretos a archivos, logs, commits ni respuestas.
6. Conserva compatibilidad PostgreSQL y el esquema existente. Las migraciones
   deben ser idempotentes, reversibles y no destructivas.
7. Implementa sólo el frente asignado. No modifiques cotizaciones/PDF ni otros
   módulos aunque detectes problemas; repórtalos aparte.
8. Agrega pruebas proporcionadas al riesgo y ejecútalas. Incluye prueba de
   autorización para CONTROL DE GESTION cuando corresponda.
9. Al terminar, revisa git diff, crea un commit pequeño y entrega: hash, archivos,
   migraciones, pruebas ejecutadas, riesgos y orden de integración.
10. Detente antes de cualquier despliegue. El integrador principal publicará.
```

## Rama 1: conciliación, cartolas y RCV

Rama: `codex/finance-reconciliation-rcv`

```text
Objetivo: completar la operación diaria de cartolas, RCV, documentos recibidos,
cuentas por pagar y conciliación bancaria.

Datos auditados de referencia, que debes volver a verificar:
- 516 movimientos, 462 pendientes.
- 331 egresos; 277 sin PUC/imputación.
- 161 documentos SII/RCV; 149 pendientes de revisión.
- No hay asignaciones activas entre los 331 egresos y facturas.

Entrega:
- Flujo claro: cargar RCV por RUT -> validar -> listar factura -> sugerir cruce ->
  confirmar/revertir conciliación.
- Detección de períodos/RUT faltantes y duplicados por receptor, tipo y folio.
- Vista de egresos sin factura, facturas sin pago y diferencias de monto/fecha.
- CONTROL DE GESTION puede cargar, clasificar, conciliar y revertir; nunca borrar
  leads ni administrar usuarios.
- Pruebas de CSV inválido, duplicado, conciliación parcial, reversa y permisos.
```

## Rama 2: créditos y P&L corporativo

Rama: `codex/finance-loans-pnl`

```text
Objetivo: construir módulo interno de créditos, simulador y reglas P&L/cashflow.

Requisitos:
- Tablas de créditos, plan de cuotas y pagos, vinculables a entidad legal,
  movimiento bancario y cuenta PUC.
- Simulador con capital, tasa, número de cuotas, fecha inicial, seguros/comisiones
  y sistema francés; mostrar cuota, interés, amortización, costo total y CAE
  estimada, claramente rotulada como simulación.
- Contabilidad correcta: cuota completa en flujo de caja; interés/comisiones en
  P&L; capital contra pasivo, no como gasto.
- Semilla idempotente de los cuatro créditos aportados por Oscar, conservando
  Banco de Chile, Forum, PedidosYa y sus sociedades/saldos/cuotas.
- Gastos fijos corporativos mensuales base: 15.181.659; servicios 709.621;
  autopistas 1.042.729. No crear pagos reales sin evidencia bancaria.
- Ads: regla de distribución inicial 25% Camaleón, Gourmet, Express y Del Sabor,
  editable y con suma obligatoria de 100%.
- Egresos actuales deben exigir PUC y entidad: Holding Green o Rolfi según el
  responsable real; no inferir automáticamente por nombre del comercio.
- UI auditable, exportación y pruebas de cálculos/redondeos/permisos.
```

## Rama 3: Meta omnicanal

Rama: `codex/meta-omnichannel`

```text
Objetivo: estabilizar WhatsApp coexistente, Instagram, Messenger y correo sin
modificar configuración externa ni secretos.

Estado auditado que debes verificar:
- Webhook WhatsApp permanente: https://crm.greendiamond.cl/meta/webhook/whatsapp
- Token WhatsApp expirado el 07-08-2026.
- Falta META_EMBEDDED_SIGNUP_CONFIG_ID.
- La app Meta sólo muestra suscripción whatsapp_business_account.
- Existen mapeos locales para 7 IDs de Instagram y cuatro marcas, con duplicados
  de username que deben auditarse.
- Correo conserva datos históricos, pero no tiene credenciales activas IMAP/SMTP.

Entrega:
- Health dashboard sin exponer secretos: token válido/expirado, callback,
  suscripciones, WABA/números, páginas/IG y última recepción por canal.
- Flujo Embedded Signup con estado seguro y mensajes accionables cuando falte
  config ID o token.
- Separar webhooks WhatsApp, Page/Messenger e Instagram y validar firmas.
- Idempotencia/deduplicación, enrutamiento por marca y pruebas con fixtures.
- Checklist exacto de acciones externas pendientes en Meta. No ejecutes cambios
  externos desde esta rama.
```

## Rama 4: BI Control Tower

Rama: `codex/bi-control-tower`

```text
Objetivo: convertir GD Intelligence/BI en una torre de control útil para Joaquín.

Fuentes actuales a verificar: ventas, leads, cotizaciones, tareas, Search Console,
GA4, salud web, SII/RCV, cartolas, conciliación, P&L y créditos. Google Ads y Meta
Ads pueden no tener credenciales; mostrar ausencia de fuente, nunca ceros falsos.

Entrega:
- Diccionario de KPI con fórmula, fuente, período, zona horaria y frescura.
- Panel ejecutivo: ventas, margen, caja, CxC, CxP, gasto sin factura, factura sin
  pago, deuda/cuotas y alertas de calidad de datos.
- Estado de cada conector: conectado con datos, conectado sin datos, expirado,
  incompleto o no configurado.
- Drill-down hasta registros fuente y fecha de actualización.
- Consultas de Joaquín exclusivamente sobre vistas analytics de solo lectura.
- Pruebas que impidan convertir ausencia de datos en valor cero.
```

## Creación segura de worktrees

Debe ejecutarla el integrador desde un clon limpio, no desde el workspace sucio:

```bash
git worktree add ../crm-finance-reconciliation -b codex/finance-reconciliation-rcv HEAD
git worktree add ../crm-finance-loans -b codex/finance-loans-pnl HEAD
git worktree add ../crm-meta-omnichannel -b codex/meta-omnichannel HEAD
git worktree add ../crm-bi-control-tower -b codex/bi-control-tower HEAD
```

No conviene ejecutar todavía estos comandos sobre el workspace actual: producción
contiene hotfixes posteriores al último commit local y antes debe consolidarse un
baseline limpio.
