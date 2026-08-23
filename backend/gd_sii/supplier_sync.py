from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import bindparam, text

from .schemas import IssuerData


class SupplierSyncService:
    TAX_FIELDS = {
        "razon_social": "legal_name", "giro": "business_activity",
        "codigo_actividad_economica": "activity_code", "direccion_tributaria": "address",
        "comuna_tributaria": "commune", "ciudad_tributaria": "city",
    }

    def sync_suppliers_batch(self, conn, issuers: Iterable[IssuerData], *, source: str = "SII") -> tuple[dict[str, int], dict[str, int], list[dict]]:
        unique = {issuer.rut: issuer for issuer in issuers}
        if not unique:
            return {}, {"seen": 0, "created": 0, "updated": 0, "unchanged": 0}, []
        statement = text("SELECT id_proveedor, rut_normalized, razon_social, giro, codigo_actividad_economica, direccion_tributaria, comuna_tributaria, ciudad_tributaria FROM inv_proveedores WHERE rut_normalized IN :ruts ORDER BY id_proveedor").bindparams(bindparam("ruts", expanding=True))
        existing: dict[str, dict] = {}
        for row in conn.execute(statement, {"ruts": list(unique)}).mappings():
            existing.setdefault(row["rut_normalized"], dict(row))
        mapping: dict[str, int] = {}
        metrics = {"seen": len(unique), "created": 0, "updated": 0, "unchanged": 0}
        events: list[dict] = []
        for rut, issuer in unique.items():
            body, dv = rut.split("-", 1)
            current = existing.get(rut)
            if current is None:
                supplier_id = conn.execute(text("""
                    INSERT INTO inv_proveedores(
                      nombre, rut_normalized, rut, dv, razon_social, giro,
                      codigo_actividad_economica, direccion_tributaria, comuna_tributaria,
                      ciudad_tributaria, source, first_seen_at, last_seen_at,
                      last_sii_update_at, created_by, updated_by, is_active, updated_at
                    ) VALUES (
                      :name, :rut, :body, :dv, :name, :giro, :activity, :address, :commune,
                      :city, :source, now(), now(), now(), 'SYSTEM_SII', 'SYSTEM_SII', TRUE, now()
                    ) RETURNING id_proveedor
                """), {"name": issuer.legal_name or rut, "rut": rut, "body": body, "dv": dv,
                       "giro": issuer.business_activity, "activity": issuer.activity_code,
                       "address": issuer.address, "commune": issuer.commune, "city": issuer.city,
                       "source": source}).scalar_one()
                mapping[rut] = int(supplier_id)
                metrics["created"] += 1
                events.append({"action": "SUPPLIER_CREATED_FROM_SII", "supplier_id": int(supplier_id),
                               "rut": rut, "fields": sorted(k for k, v in issuer.__dict__.items() if v)})
                continue
            changes = {column: getattr(issuer, attr) for column, attr in self.TAX_FIELDS.items()
                       if getattr(issuer, attr) and getattr(issuer, attr) != current.get(column)}
            params = {"id": current["id_proveedor"], **changes}
            assignments = [f"{column}=:{column}" for column in changes]
            assignments += ["last_seen_at=now()", "last_sii_update_at=now()", "updated_by='SYSTEM_SII'", "updated_at=now()"]
            conn.execute(text(f"UPDATE inv_proveedores SET {', '.join(assignments)} WHERE id_proveedor=:id"), params)
            mapping[rut] = int(current["id_proveedor"])
            metrics["updated" if changes else "unchanged"] += 1
            if changes:
                events.append({"action": "SUPPLIER_UPDATED_FROM_SII", "supplier_id": int(current["id_proveedor"]),
                               "rut": rut, "changes": {key: {"before": current.get(key), "after": value}
                                                         for key, value in changes.items()}})
        return mapping, metrics, events

    def sync_supplier_from_tax_document(self, conn, *, issuer: IssuerData, source: str = "SII") -> int:
        mapping, _, _ = self.sync_suppliers_batch(conn, [issuer], source=source)
        return mapping[issuer.rut]
