# GD SII PRODUCTION GO-LIVE

```text
======================================================
GD SII PRODUCTION GO-LIVE
======================================================

OLD_RELEASE=/opt/greendiamond/releases/finance-pnl-v64-auditable-20260823_104952
NEW_RELEASE=/opt/greendiamond/releases/sii-finance-20260823_115153-r1
COMMIT=151ea2fb57b80d5684027b1ef01613b6dfb6d4af

DB_BACKUP=/opt/greendiamond/backups/sii-finance-20260823_115153/bf68ec5_crm2025b-before-sii.dump (PASS, 6341086 bytes, mode 600)

PRODUCTION_SCHEMA_AUDIT=PASS
LEGAL_ENTITY_MASTER=fin_legal_entities REUSED; 8 ACTIVE REAL ENTITIES
SUPPLIER_MASTER=inv_proveedores REUSED; 52 EXISTING ROWS PRESERVED
PAYABLE_MASTER=fin_gastos REUSED; 51 EXISTING ROWS PRESERVED
CREDENTIAL_VAULT=backend/gd_intelligence/credential_vault.py REUSED; wi_integration_credentials; SECURE_FILE DECRYPT PASS

SUPPLIER_DUPLICATES=0 GROUPS

MIGRATION_COPY_RUN_1=PASS
MIGRATION_COPY_RUN_2=PASS
MIGRATION_PRODUCTION=PASS

TESTS=29/29 PASS (14 EXISTING + 15 SII); SERVER SII 15/15 PASS
BUILD=PASS; EXACT PRODUCTION FRONTEND npm ci + npm run build
COMPILE=PASS

CRM_SERVICE=active
HEALTHZ=200

FINANCE=PASS; UI 200; API 200
SUPPLIERS=PASS; UI/API 200; EXISTING MASTER PRESERVED
PAYABLES=PASS; API 200; EXISTING MASTER PRESERVED
SII_UI=PASS; UI/API 200; 8 REAL LEGAL ENTITIES LISTED

ADMIN_CAN_MANAGE_SII=PASS; ADMIN REACHES MANAGE CONTROLLER; RCV AUTO-SYNC RETURNS CONTROLLED 501

SII_CRSEED=PASS; CERTIFICATION REAL
SII_GET_TOKEN=NOT RUN; REAL_PFX_REQUIRED

CERTIFICATE=NOT LOADED; SECURE UI READY
CERTIFICATE_OWNER=NOT AVAILABLE UNTIL REAL PFX LOAD
CERTIFICATE_EXPIRATION=NOT AVAILABLE UNTIL REAL PFX LOAD

REAL_XML_IMPORT=NOT RUN; REAL PFX/INPUT REQUIRED
REAL_RCV_IMPORT=NOT RUN; REAL PFX/INPUT REQUIRED

SUPPLIER_CREATED_OR_UPDATED=NOT RUN WITH REAL INPUT; RESTORED-COPY PIPELINE PASS
PAYABLE_CREATED=NOT RUN WITH REAL INPUT; RESTORED-COPY PIPELINE PASS
IDEMPOTENCY=PASS IN TESTS AND RESTORED PRODUCTION COPY; REAL REIMPORT PENDING PFX/INPUT

SII_MODE=CERTIFICATION
SII_ISSUE_ENABLED=false

AUTOMATED_RCV=NO; OFFICIAL API UNAVAILABLE; UI DISABLED WITH EXPLANATION
EMAIL_DTE_INGESTION=IMPLEMENTED READ-ONLY/IDEMPOTENT USING GIA IMAP; 0 PAYMENT MAILBOXES CONFIGURED; NON-BLOCKING

ROLLBACK_READY=PASS; OLD RELEASE RECORDED; DB DUMP VERIFIED; ATOMIC SYMLINK ROLLBACK READY

FINAL=PARTIAL
ONLY_BLOCKER=REAL_PFX_REQUIRED
======================================================
```

## Prompt de continuidad para ChatGPT/Codex

```text
Continúa el go-live de GD Finance SII desde el estado productivo ya desplegado. No repitas el
desarrollo ni la migración y no hagas reset hard, git clean, scraping del SII, emisión, aceptación
o reclamo automático de DTE.

Repositorio original:
/Users/oscarmendoza/Desktop/CRM 2025

Worktree limpio:
/Users/oscarmendoza/Desktop/CRM 2025 SII Release

Rama limpia:
codex/finanzas-sii-release

Commit limpio:
151ea2fb57b80d5684027b1ef01613b6dfb6d4af

Servidor:
oscar@192.168.100.51

Release productiva actual:
/opt/greendiamond/releases/sii-finance-20260823_115153-r1

Release anterior para rollback:
/opt/greendiamond/releases/finance-pnl-v64-auditable-20260823_104952

Backup BD verificado:
/opt/greendiamond/backups/sii-finance-20260823_115153/bf68ec5_crm2025b-before-sii.dump

Lee primero:
/Users/oscarmendoza/Desktop/CRM 2025/reports/GD_FINANCE_SII_FINAL_REPORT_20260823.md

Estado validado: servicio active, /healthz 200, Finance/Proveedores/CxP/Bancos/SII UI y APIs 200,
29/29 tests PASS, build PASS, compile PASS, migración sobre copia PASS x2, migración productiva
PASS, CrSeed real de CERTIFICATION PASS, 8 entidades legales reales visibles, 0 duplicados de RUT,
0 conexiones SII configuradas y 0 errores de journal desde la release r1. La r1 también reconcilió
un hotfix productivo concurrente de Omnicanal y su smoke quedó PASS.

Seguridad vigente y obligatoria:
GD_FINANCE_SII_ENABLED=true
SII_MODE=CERTIFICATION
SII_ISSUE_ENABLED=false
SII_ALLOW_PRODUCTION=0

Único bloqueo: falta el PFX/P12 real. No solicites que el usuario pegue el archivo, password,
llave privada o token en el chat, Git, logs o historial de terminal. Indícale cargarlo directamente
por HTTPS en FINANZAS -> SII -> CONEXIONES -> entidad real -> CARGAR CERTIFICADO.

Cuando el usuario confirme la carga segura:
1. Ejecuta POST /api/finance/sii/connections/{id}/test y exige HTTP 200, ok=true y CONNECTED.
2. No imprimas el token. Verifica status=CONNECTED y last_auth_at.
3. Importa primero un XML real y luego un CSV RCV real desde la UI segura.
4. Verifica proveedor, documento, CxP, source y audit.
5. Reimporta ambos y confirma que los conteos no aumentan.
6. Mantén CERTIFICATION, SII_ALLOW_PRODUCTION=0 y SII_ISSUE_ENABLED=false.
7. Actualiza el informe final. Sólo declara FINAL=PASS cuando GetTokenFromSeed y las importaciones
   reales idempotentes estén PASS.

Correo DTE ya está implementado en modo IMAP read-only con BODY.PEEK e idempotencia, reutilizando
Correo GIA. Actualmente no hay casilla de pagos configurada; esto es no bloqueante y no autoriza
guardar credenciales en código.
```
