BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS fin_legal_entities (
    id_legal_entity BIGSERIAL PRIMARY KEY,
    legal_code TEXT NOT NULL UNIQUE,
    legal_name TEXT NOT NULL,
    rut TEXT NOT NULL UNIQUE,
    is_group_member BOOLEAN NOT NULL DEFAULT TRUE,
    is_holding BOOLEAN NOT NULL DEFAULT FALSE,
    is_rolfi BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wi_sii_connections (
    id_sii_connection BIGSERIAL PRIMARY KEY,
    id_legal_entity BIGINT NOT NULL UNIQUE REFERENCES fin_legal_entities(id_legal_entity),
    environment TEXT NOT NULL DEFAULT 'CERTIFICATION' CHECK (environment IN ('CERTIFICATION','PRODUCTION')),
    rut_company TEXT NOT NULL,
    dv_company TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DISCONNECTED' CHECK (status IN ('DISCONNECTED','CONFIGURED','CONNECTED','ERROR','DISABLED')),
    certificate_secret_key TEXT,
    certificate_filename TEXT,
    certificate_subject TEXT,
    certificate_serial TEXT,
    certificate_valid_from TIMESTAMPTZ,
    certificate_valid_to TIMESTAMPTZ,
    last_auth_at TIMESTAMPTZ,
    last_sync_at TIMESTAMPTZ,
    last_sync_status TEXT,
    last_error_code TEXT,
    last_error_safe TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by BIGINT
);

ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS rut_normalized TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS rut TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS dv TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS razon_social TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS giro TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS actividad_economica TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS codigo_actividad_economica TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS direccion_tributaria TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS comuna_tributaria TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS ciudad_tributaria TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS last_sii_update_at TIMESTAMPTZ;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS created_by TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS updated_by TEXT;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE inv_proveedores ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS ix_inv_proveedores_rut_normalized ON inv_proveedores(rut_normalized);
DO $$
BEGIN
  IF NOT EXISTS (
      SELECT rut_normalized FROM inv_proveedores
      WHERE rut_normalized IS NOT NULL GROUP BY rut_normalized HAVING count(*) > 1
  ) THEN
    CREATE UNIQUE INDEX IF NOT EXISTS ux_inv_proveedores_rut_normalized
      ON inv_proveedores(rut_normalized) WHERE rut_normalized IS NOT NULL;
  END IF;
END $$;

CREATE OR REPLACE VIEW fin_supplier_duplicate_ruts AS
SELECT rut_normalized, count(*) AS duplicate_count, array_agg(id_proveedor ORDER BY id_proveedor) AS supplier_ids
FROM inv_proveedores WHERE rut_normalized IS NOT NULL
GROUP BY rut_normalized HAVING count(*) > 1;

CREATE TABLE IF NOT EXISTS fin_sii_received_documents (
    id BIGSERIAL PRIMARY KEY,
    id_legal_entity BIGINT NOT NULL REFERENCES fin_legal_entities(id_legal_entity),
    supplier_id INT NOT NULL REFERENCES inv_proveedores(id_proveedor),
    receiver_rut TEXT NOT NULL,
    issuer_rut TEXT NOT NULL,
    issuer_dv TEXT NOT NULL,
    issuer_name TEXT,
    document_type TEXT NOT NULL,
    folio TEXT NOT NULL,
    issue_date DATE NOT NULL,
    reception_date DATE,
    due_date DATE,
    payment_method TEXT,
    net_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    exempt_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    vat_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    vat_non_recoverable NUMERIC(18,2) NOT NULL DEFAULT 0,
    other_tax_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CLP',
    purchase_type TEXT,
    sii_status TEXT NOT NULL DEFAULT 'REGISTERED',
    source TEXT NOT NULL,
    source_external_id TEXT,
    xml_sha256 TEXT,
    xml_storage_key TEXT,
    purchase_order TEXT,
    dispatch_guide TEXT,
    reference_text TEXT,
    accounts_payable_id INT,
    financial_status TEXT NOT NULL DEFAULT 'NEW' CHECK (financial_status IN ('NEW','PENDING_REVIEW','APPROVED','SCHEDULED','PARTIALLY_PAID','PAID','REJECTED','CREDITED','CANCELLED')),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(id_legal_entity, issuer_rut, document_type, folio)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fin_sii_xml_sha256 ON fin_sii_received_documents(xml_sha256) WHERE xml_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fin_sii_docs_date ON fin_sii_received_documents(id_legal_entity, issue_date DESC);
CREATE INDEX IF NOT EXISTS ix_fin_sii_docs_supplier ON fin_sii_received_documents(supplier_id);

CREATE TABLE IF NOT EXISTS fin_sii_received_document_lines (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES fin_sii_received_documents(id) ON DELETE CASCADE,
    line_number INT NOT NULL,
    item_code TEXT,
    description TEXT,
    quantity NUMERIC(18,6),
    unit TEXT,
    unit_price NUMERIC(18,6),
    discount_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    surcharge_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    line_net_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, line_number)
);

CREATE TABLE IF NOT EXISTS fin_sii_document_references (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES fin_sii_received_documents(id) ON DELETE CASCADE,
    reference_line INT NOT NULL,
    reference_document_type TEXT,
    reference_folio TEXT,
    reference_date DATE,
    reference_code TEXT,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, reference_line)
);

CREATE TABLE IF NOT EXISTS fin_sii_document_sources (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES fin_sii_received_documents(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fin_sii_source_identity
  ON fin_sii_document_sources(document_id, source, external_id, sha256);

CREATE TABLE IF NOT EXISTS wi_sii_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    id_legal_entity BIGINT NOT NULL REFERENCES fin_legal_entities(id_legal_entity),
    source TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','SUCCESS','PARTIAL','ERROR')),
    period_from DATE,
    period_to DATE,
    records_seen INT NOT NULL DEFAULT 0,
    records_inserted INT NOT NULL DEFAULT 0,
    records_updated INT NOT NULL DEFAULT 0,
    records_skipped INT NOT NULL DEFAULT 0,
    records_failed INT NOT NULL DEFAULT 0,
    suppliers_seen INT NOT NULL DEFAULT 0,
    suppliers_created INT NOT NULL DEFAULT 0,
    suppliers_updated INT NOT NULL DEFAULT 0,
    suppliers_unchanged INT NOT NULL DEFAULT 0,
    payables_created INT NOT NULL DEFAULT 0,
    payables_updated INT NOT NULL DEFAULT 0,
    error_code TEXT,
    error_safe TEXT,
    triggered_by BIGINT
);

ALTER TABLE fin_gastos ADD COLUMN IF NOT EXISTS id_legal_entity BIGINT REFERENCES fin_legal_entities(id_legal_entity);
ALTER TABLE fin_gastos ADD COLUMN IF NOT EXISTS supplier_id INT REFERENCES inv_proveedores(id_proveedor);
ALTER TABLE fin_gastos ADD COLUMN IF NOT EXISTS sii_document_id BIGINT REFERENCES fin_sii_received_documents(id);
ALTER TABLE fin_gastos ADD COLUMN IF NOT EXISTS parent_payable_id INT REFERENCES fin_gastos(id_gasto);
ALTER TABLE fin_gastos ADD COLUMN IF NOT EXISTS amount_original NUMERIC(18,2);
ALTER TABLE fin_gastos ADD COLUMN IF NOT EXISTS balance NUMERIC(18,2);
ALTER TABLE fin_gastos ADD COLUMN IF NOT EXISTS payable_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE fin_gastos ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS ux_fin_gastos_sii_document ON fin_gastos(sii_document_id) WHERE sii_document_id IS NOT NULL;

DO $$ BEGIN
  ALTER TABLE fin_sii_received_documents ADD CONSTRAINT fk_fin_sii_accounts_payable
    FOREIGN KEY (accounts_payable_id) REFERENCES fin_gastos(id_gasto);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;

-- Rollback de codigo: seguro porque todas las columnas son aditivas y el codigo anterior las ignora.
-- Rollback de esquema (solo tras backup y revision de datos): eliminar primero FKs/tablas fin_sii_*,
-- luego wi_sii_*; no eliminar automaticamente columnas agregadas a inv_proveedores/fin_gastos.
