# ATLAS Finance CRM read-only service
Branch: feat/atlas-readonly-service-20261102
No production deployment is included.

## Authentication
Set ATLAS_FINANCE_SERVICE_TOKEN in the CRM API runtime environment to a randomly generated secret of at least 32 characters. Never commit it, place it in source control, or expose it in logs. Requests must use Authorization: Bearer <token>. The route fails closed with HTTP 503 if the secret is not configured and HTTP 401 for invalid credentials.

## Endpoints
- GET /api/atlas/finance/sourceHealth: reports presence of finance, SII and possible bank tables, and active legal entities (including verified RUT).
- GET /api/atlas/finance/sales?from=YYYY-MM-DD&to=YYYY-MM-DD: event financial records, defaulting from 2000-01-01 through today.
- GET /api/atlas/finance/receivables: positive event balances.
- GET /api/atlas/finance/payables: received SII DTE with supplier, RUT, folio, net, VAT, total, SII status and linked payable balance. This is a document register; pending-review documents must not be treated as confirmed debt.
- GET /api/atlas/finance/issuedDocuments: invoice metadata currently attached to CRM event records. It is not a full SII-issued boleta register.
- GET /api/atlas/finance/bankTransactions: reads fin_bank_movements joined to fin_bank_accounts and confirmed/suggested fin_payable_bank_allocations; returns applied and remaining amounts by legal entity, with date and entity filters. Fails 503 if any required table is unavailable.

## Source data
Reads fin_eventos, fin_gastos only through linked payable metadata, fin_sii_received_documents, inv_proveedores, fin_legal_entities and checks candidate bank table names. No CREATE/ALTER/INSERT/UPDATE/DELETE is performed by this router. Bank movements are read from the identified CRM tables fin_bank_movements, fin_bank_accounts and fin_payable_bank_allocations.

## Production gate
Before deploy: inspect active cPanel/Passenger app root and actual deployment ref, set secret in protected environment, verify endpoint access with the service token, validate returned RUTs and record counts, identify bank movement table and issued boleta source. Then configure Brontos ATLAS connector paths. All actions remain GET/read-only.
