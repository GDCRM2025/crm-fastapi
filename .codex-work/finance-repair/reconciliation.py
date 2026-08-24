from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Iterable

from sqlalchemy import text

from backend.core.activity_log import log_activity

from .exceptions import PayableReconciliationError


MONEY_ZERO = Decimal("0.00")
MONEY_TOLERANCE = Decimal("0.50")


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise PayableReconciliationError("Monto de conciliación inválido") from exc


def _normalized(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        re.sub(r"[^A-Z0-9]+", " ", raw.encode("ascii", "ignore").decode().upper()).split()
    )


_NAME_NOISE = {
    "A", "AL", "DE", "DEL", "EL", "EN", "LA", "LAS", "LOS", "Y",
    "CIA", "COMPANIA", "COMERCIAL", "EIRL", "EMPRESA", "INVERSIONES",
    "LIMITADA", "LTDA", "PAGO", "SOCIEDAD", "SPA", "TRANSFERENCIA",
    "TRASPASO",
}


def _distinctive_tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normalized(value).split()
        if len(token) >= 4 and token not in _NAME_NOISE
    }


def payable_state(net_payable: Any, paid_amount: Any) -> dict[str, Any]:
    net = max(MONEY_ZERO, _money(net_payable))
    paid = max(MONEY_ZERO, _money(paid_amount))
    balance = max(MONEY_ZERO, net - paid)
    if net <= MONEY_ZERO:
        status = "CREDITED"
    elif balance <= MONEY_TOLERANCE:
        balance = MONEY_ZERO
        status = "PAID"
    elif paid > MONEY_ZERO:
        status = "PARTIALLY_PAID"
    else:
        status = "PENDING"
    return {"net_payable": net, "paid_amount": paid, "balance": balance, "status": status}


@dataclass(frozen=True)
class MatchCandidate:
    payable_id: int
    score: int
    confidence: str
    match_method: str
    amount_applied: Decimal
    reasons: tuple[str, ...]


class BankInvoiceMatchingService:
    def unreconciled_movements(
        self,
        conn,
        *,
        legal_entity_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        entity_filter = "AND ba.id_legal_entity=:entity" if legal_entity_id else ""
        rows = conn.execute(text(f"""
          WITH allocated AS (
            SELECT bank_movement_id,COALESCE(sum(amount_applied),0) AS applied
            FROM fin_payable_bank_allocations WHERE status='CONFIRMED'
            GROUP BY bank_movement_id
          )
          SELECT bm.id_bank_movement,bm.tx_date,bm.description,bm.reference,bm.amount,
            bm.status AS bank_status,ba.id_legal_entity,ba.bank_name,ba.label,
            abs(bm.amount)-COALESCE(a.applied,0) AS remaining_amount
          FROM fin_bank_movements bm
          JOIN fin_bank_accounts ba ON ba.id_bank_account=bm.id_bank_account
          LEFT JOIN allocated a ON a.bank_movement_id=bm.id_bank_movement
          WHERE bm.amount < 0
            AND abs(bm.amount)-COALESCE(a.applied,0) > :tolerance
            AND NOT EXISTS(
              SELECT 1 FROM fin_gastos legacy
              WHERE legacy.bank_movement_id=bm.id_bank_movement
            )
            {entity_filter}
          ORDER BY bm.tx_date DESC,bm.id_bank_movement DESC
          LIMIT :limit
        """), {"entity": legal_entity_id, "limit": limit, "tolerance": MONEY_TOLERANCE}).mappings().all()
        return [dict(row) for row in rows]

    def score_candidate(self, movement: dict[str, Any], payable: dict[str, Any]) -> MatchCandidate:
        movement_remaining = _money(movement.get("remaining_amount"))
        payable_balance = _money(payable.get("balance"))
        movement_text = _normalized(
            f"{movement.get('description') or ''} {movement.get('reference') or ''}"
        )
        supplier_name = _normalized(
            payable.get("supplier_name") or payable.get("issuer_name") or ""
        )
        supplier_rut = re.sub(r"\D", "", str(payable.get("supplier_rut") or ""))
        folio = _normalized(payable.get("folio"))
        score = 0
        reasons: list[str] = []

        exact_amount = abs(movement_remaining - payable_balance) <= MONEY_TOLERANCE
        if exact_amount:
            score += 50
            reasons.append("MONTO_EXACTO")
        elif payable_balance > MONEY_ZERO and payable_balance <= movement_remaining + MONEY_TOLERANCE:
            score += 25
            reasons.append("MONTO_COMPATIBLE")

        rut_match = len(supplier_rut) >= 7 and supplier_rut in re.sub(r"\D", "", movement_text)
        if rut_match:
            score += 30
            reasons.append("RUT_EXACTO")

        folio_match = bool(folio and re.search(rf"(?:^|\s){re.escape(folio)}(?:\s|$)", movement_text))
        if folio_match:
            score += 20
            reasons.append("FOLIO_EN_GLOSA")

        name_match = False
        supplier_tokens = _distinctive_tokens(supplier_name)
        movement_tokens = _distinctive_tokens(movement_text)
        if len(supplier_name) >= 4:
            name_match = supplier_name in movement_text or SequenceMatcher(
                None, supplier_name, movement_text
            ).ratio() >= 0.72
        if not name_match and supplier_tokens:
            name_match = bool(supplier_tokens & movement_tokens)
        if name_match:
            score += 20
            reasons.append("NOMBRE_PROVEEDOR")

        movement_date = movement.get("tx_date")
        issue_date = payable.get("issue_date")
        if isinstance(movement_date, date) and isinstance(issue_date, date):
            days = abs((movement_date - issue_date).days)
            if days <= 3:
                score += 10
                reasons.append("FECHA_3_DIAS")
            elif days <= 7:
                score += 5
                reasons.append("FECHA_7_DIAS")

        score = min(score, 100)
        if rut_match and exact_amount:
            method = "AUTO_RUT_AMOUNT"
        elif exact_amount:
            method = "AUTO_EXACT"
        else:
            method = "AUTO_AMOUNT_DATE"
        confidence = "HIGH" if score >= 95 else "PROBABLE" if score >= 75 else "MANUAL_REVIEW"
        return MatchCandidate(
            payable_id=int(payable["payable_id"]),
            score=score,
            confidence=confidence,
            match_method=method,
            amount_applied=min(movement_remaining, payable_balance),
            reasons=tuple(reasons),
        )

    def rank_candidates(
        self,
        movement: dict[str, Any],
        payables: Iterable[dict[str, Any]],
    ) -> list[MatchCandidate]:
        payable_items = [
            payable
            for payable in payables
            if int(payable["id_legal_entity"]) == int(movement["id_legal_entity"])
            and _money(payable.get("balance")) > MONEY_ZERO
        ]
        candidates = [
            self.score_candidate(movement, payable)
            for payable in payable_items
        ]

        movement_remaining = _money(movement.get("remaining_amount"))
        exact_payable_ids = {
            int(payable["payable_id"])
            for payable in payable_items
            if abs(_money(payable.get("balance")) - movement_remaining) <= MONEY_TOLERANCE
        }
        if len(exact_payable_ids) == 1:
            unique_id = next(iter(exact_payable_ids))
            candidates = [
                replace(
                    candidate,
                    score=min(candidate.score + 15, 100),
                    confidence=(
                        "HIGH" if candidate.score + 15 >= 95
                        else "PROBABLE" if candidate.score + 15 >= 75
                        else "MANUAL_REVIEW"
                    ),
                    reasons=(*candidate.reasons, "MONTO_UNICO_EN_CXP"),
                )
                if candidate.payable_id == unique_id
                else candidate
                for candidate in candidates
            ]
        return sorted(candidates, key=lambda item: (-item.score, item.payable_id))

    def movement(self, conn, movement_id: int, *, lock: bool = False) -> dict[str, Any] | None:
        if lock:
            locked = conn.execute(
                text("SELECT id_bank_movement FROM fin_bank_movements WHERE id_bank_movement=:id FOR UPDATE"),
                {"id": movement_id},
            ).scalar()
            if not locked:
                return None
        row = conn.execute(text("""
          SELECT bm.id_bank_movement,bm.tx_date,bm.description,bm.reference,bm.amount,
            bm.status AS bank_status,ba.id_legal_entity,ba.bank_name,ba.label,
            abs(bm.amount)-COALESCE(sum(a.amount_applied) FILTER(WHERE a.status='CONFIRMED'),0) AS remaining_amount,
            EXISTS(SELECT 1 FROM fin_gastos legacy WHERE legacy.bank_movement_id=bm.id_bank_movement) AS legacy_classified
          FROM fin_bank_movements bm
          JOIN fin_bank_accounts ba ON ba.id_bank_account=bm.id_bank_account
          LEFT JOIN fin_payable_bank_allocations a ON a.bank_movement_id=bm.id_bank_movement
          WHERE bm.id_bank_movement=:id
          GROUP BY bm.id_bank_movement,ba.id_legal_entity,ba.bank_name,ba.label
        """), {"id": movement_id}).mappings().first()
        return dict(row) if row else None

    def open_payables(self, conn, legal_entity_id: int) -> list[dict[str, Any]]:
        rows = conn.execute(text("""
          WITH payable_amounts AS (
            SELECT root.id_gasto AS payable_id,root.id_legal_entity,root.supplier_id,
              root.sii_document_id,root.fecha AS issue_date,root.fecha_vencimiento,
              root.tipo_doc,root.doc_num AS folio,
              COALESCE(root.amount_original,root.monto,0)
                + COALESCE(sum(child.amount_original),0) AS net_payable
            FROM fin_gastos root
            LEFT JOIN fin_gastos child ON child.parent_payable_id=root.id_gasto AND child.is_active IS TRUE
            WHERE root.id_legal_entity=:entity AND root.is_active IS TRUE
              AND root.parent_payable_id IS NULL AND root.sii_document_id IS NOT NULL
            GROUP BY root.id_gasto
          ), paid AS (
            SELECT payable_id,COALESCE(sum(amount_applied),0) AS paid_amount
            FROM fin_payable_bank_allocations WHERE status='CONFIRMED' GROUP BY payable_id
          )
          SELECT p.*,COALESCE(pd.paid_amount,0) AS paid_amount,
            GREATEST(0,p.net_payable-COALESCE(pd.paid_amount,0)) AS balance,
            COALESCE(s.razon_social,s.nombre,d.issuer_name) AS supplier_name,
            COALESCE(s.rut_normalized,d.issuer_rut) AS supplier_rut
          FROM payable_amounts p
          JOIN fin_sii_received_documents d ON d.id=p.sii_document_id
          JOIN inv_proveedores s ON s.id_proveedor=p.supplier_id
          LEFT JOIN paid pd ON pd.payable_id=p.payable_id
          WHERE p.net_payable-COALESCE(pd.paid_amount,0) > 0
          ORDER BY p.issue_date DESC,p.payable_id DESC
        """), {"entity": legal_entity_id}).mappings().all()
        return [dict(row) for row in rows]

    def candidates(self, conn, movement_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        movement = self.movement(conn, movement_id)
        if not movement:
            raise PayableReconciliationError("Movimiento bancario no encontrado")
        if _money(movement["amount"]) >= MONEY_ZERO:
            raise PayableReconciliationError("Un ingreso bancario no puede pagar una cuenta por pagar")
        if movement["legacy_classified"]:
            raise PayableReconciliationError("El movimiento tiene una clasificación histórica y requiere revisión manual")
        payables = self.open_payables(conn, int(movement["id_legal_entity"]))
        by_id = {int(item["payable_id"]): item for item in payables}
        ranked = self.rank_candidates(movement, payables)
        items = []
        for match in ranked:
            payable = by_id[match.payable_id]
            items.append({
                **payable,
                "score": match.score,
                "confidence": match.confidence,
                "match_method": match.match_method,
                "suggested_amount": match.amount_applied,
                "reasons": list(match.reasons),
            })
        return movement, items

    def persist_suggestions(self, conn, movement_id: int, *, actor: dict[str, Any]) -> list[dict[str, Any]]:
        movement, candidates = self.candidates(conn, movement_id)
        persisted: list[dict[str, Any]] = []
        for item in candidates:
            if int(item["score"]) < 75:
                continue
            row = conn.execute(text("""
              INSERT INTO fin_payable_bank_allocations(
                id_legal_entity,payable_id,bank_movement_id,amount_applied,currency,
                match_method,match_confidence,status
              ) VALUES (:entity,:payable,:movement,:amount,'CLP',:method,:score,'SUGGESTED')
              ON CONFLICT (payable_id,bank_movement_id)
                WHERE status IN ('SUGGESTED','CONFIRMED')
              DO UPDATE SET amount_applied=CASE
                  WHEN fin_payable_bank_allocations.status='SUGGESTED' THEN EXCLUDED.amount_applied
                  ELSE fin_payable_bank_allocations.amount_applied END,
                match_method=CASE WHEN fin_payable_bank_allocations.status='SUGGESTED' THEN EXCLUDED.match_method ELSE fin_payable_bank_allocations.match_method END,
                match_confidence=CASE WHEN fin_payable_bank_allocations.status='SUGGESTED' THEN EXCLUDED.match_confidence ELSE fin_payable_bank_allocations.match_confidence END,
                updated_at=now()
              RETURNING id,status
            """), {"entity": movement["id_legal_entity"], "payable": item["payable_id"],
                     "movement": movement_id, "amount": item["suggested_amount"],
                     "method": item["match_method"], "score": item["score"]}).mappings().one()
            persisted.append({"id": int(row["id"]), "status": row["status"], **item})
            log_activity(conn, username=actor.get("username"), user_id=actor.get("id"), role=actor.get("role"),
                         action="BANK_MATCH_SUGGESTED", entity_type="fin_payable_bank_allocation", entity_id=int(row["id"]),
                         meta={"payable_id": item["payable_id"], "bank_movement_id": movement_id,
                               "amount": str(item["suggested_amount"]), "confidence": item["score"]})
        return persisted

    def _recalculate_payable(self, conn, payable_id: int) -> dict[str, Any]:
        row = conn.execute(text("""
          SELECT root.id_gasto,root.sii_document_id,
            COALESCE(root.amount_original,root.monto,0)+COALESCE(sum(child.amount_original),0) AS net_payable,
            COALESCE((SELECT sum(a.amount_applied) FROM fin_payable_bank_allocations a
              WHERE a.payable_id=root.id_gasto AND a.status='CONFIRMED'),0) AS paid_amount
          FROM fin_gastos root
          LEFT JOIN fin_gastos child ON child.parent_payable_id=root.id_gasto AND child.is_active IS TRUE
          WHERE root.id_gasto=:id GROUP BY root.id_gasto
        """), {"id": payable_id}).mappings().first()
        if not row:
            raise PayableReconciliationError("Cuenta por pagar no encontrada")
        state = payable_state(row["net_payable"], row["paid_amount"])
        net = state["net_payable"]
        paid = state["paid_amount"]
        balance = state["balance"]
        status = state["status"]
        conn.execute(text("""
          UPDATE fin_gastos SET balance=:balance,payable_status=:status,
            pagado=(:status='PAID'),fecha_pago=CASE WHEN :status='PAID' THEN COALESCE(fecha_pago,CURRENT_DATE) ELSE NULL END,
            updated_at=now() WHERE id_gasto=:id
        """), {"id": payable_id, "balance": balance, "status": status})
        if row["sii_document_id"]:
            document_status = "PENDING_REVIEW" if status == "PENDING" else status
            conn.execute(text("UPDATE fin_sii_received_documents SET financial_status=:status,updated_at=now() WHERE id=:id"),
                         {"id": row["sii_document_id"], "status": document_status})
        return {"payable_id": payable_id, "net_payable": net, "paid_amount": paid, "balance": balance, "status": status}

    def confirm(self, conn, movement_id: int, allocations: list[dict[str, Any]], *, actor: dict[str, Any]) -> dict[str, Any]:
        if not allocations:
            raise PayableReconciliationError("Seleccione al menos una factura")
        movement = self.movement(conn, movement_id, lock=True)
        if not movement:
            raise PayableReconciliationError("Movimiento bancario no encontrado")
        if _money(movement["amount"]) >= MONEY_ZERO:
            raise PayableReconciliationError("Un ingreso bancario no puede pagar una cuenta por pagar")
        if movement["legacy_classified"]:
            raise PayableReconciliationError("El movimiento ya tiene una clasificación histórica")
        remaining = _money(movement["remaining_amount"])
        requested = sum((_money(item.get("amount")) for item in allocations), MONEY_ZERO)
        if requested <= MONEY_ZERO or requested > remaining + MONEY_TOLERANCE:
            raise PayableReconciliationError("El total aplicado supera el saldo del movimiento")

        seen: set[int] = set()
        confirmed_ids: list[int] = []
        for item in allocations:
            payable_id = int(item.get("payable_id") or 0)
            amount = _money(item.get("amount"))
            if payable_id in seen or amount <= MONEY_ZERO:
                raise PayableReconciliationError("Asignaciones duplicadas o con monto inválido")
            seen.add(payable_id)
            payable = conn.execute(text("""
              SELECT g.id_gasto,g.id_legal_entity,g.parent_payable_id,
                GREATEST(0,COALESCE(g.balance,g.amount_original,g.monto,0)) AS current_balance
              FROM fin_gastos g WHERE g.id_gasto=:id AND g.is_active IS TRUE FOR UPDATE
            """), {"id": payable_id}).mappings().first()
            if not payable or payable["parent_payable_id"] is not None:
                raise PayableReconciliationError("Cuenta por pagar no disponible")
            if int(payable["id_legal_entity"]) != int(movement["id_legal_entity"]):
                raise PayableReconciliationError("La factura y el movimiento pertenecen a entidades distintas")
            current = self._recalculate_payable(conn, payable_id)
            if amount > _money(current["balance"]) + MONEY_TOLERANCE:
                raise PayableReconciliationError("El monto aplicado supera el saldo de una factura")
            row = conn.execute(text("""
              INSERT INTO fin_payable_bank_allocations(
                id_legal_entity,payable_id,bank_movement_id,amount_applied,currency,
                match_method,match_confidence,status,matched_at,matched_by
              ) VALUES (:entity,:payable,:movement,:amount,'CLP','MANUAL',100,'CONFIRMED',now(),:actor)
              ON CONFLICT (payable_id,bank_movement_id)
                WHERE status IN ('SUGGESTED','CONFIRMED')
              DO UPDATE SET amount_applied=EXCLUDED.amount_applied,status='CONFIRMED',
                match_method=CASE WHEN fin_payable_bank_allocations.status='SUGGESTED' THEN fin_payable_bank_allocations.match_method ELSE 'MANUAL' END,
                matched_at=now(),matched_by=EXCLUDED.matched_by,updated_at=now()
              RETURNING id
            """), {"entity": movement["id_legal_entity"], "payable": payable_id,
                     "movement": movement_id, "amount": amount, "actor": actor.get("id")}).scalar_one()
            confirmed_ids.append(int(row))
            self._recalculate_payable(conn, payable_id)
            log_activity(conn, username=actor.get("username"), user_id=actor.get("id"), role=actor.get("role"),
                         action="BANK_MATCH_CONFIRMED", entity_type="fin_payable_bank_allocation", entity_id=int(row),
                         meta={"payable_id": payable_id, "bank_movement_id": movement_id, "amount": str(amount)})
        return {"movement_id": movement_id, "allocation_ids": confirmed_ids,
                "amount_applied": requested, "remaining_amount": max(MONEY_ZERO, remaining-requested)}

    def reverse(self, conn, allocation_id: int, *, actor: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
        row = conn.execute(text("""
          SELECT id,payable_id,bank_movement_id,status FROM fin_payable_bank_allocations
          WHERE id=:id FOR UPDATE
        """), {"id": allocation_id}).mappings().first()
        if not row or row["status"] != "CONFIRMED":
            raise PayableReconciliationError("Conciliación confirmada no encontrada")
        conn.execute(text("""
          UPDATE fin_payable_bank_allocations SET status='REVERSED',reversed_at=now(),
            reversed_by=:actor,reversal_reason=:reason,updated_at=now() WHERE id=:id
        """), {"id": allocation_id, "actor": actor.get("id"), "reason": (reason or "").strip()[:240] or None})
        payable = self._recalculate_payable(conn, int(row["payable_id"]))
        log_activity(conn, username=actor.get("username"), user_id=actor.get("id"), role=actor.get("role"),
                     action="PAYABLE_BANK_RECONCILIATION_REVERSED", entity_type="fin_payable_bank_allocation", entity_id=allocation_id,
                     meta={"payable_id": row["payable_id"], "bank_movement_id": row["bank_movement_id"]})
        log_activity(conn, username=actor.get("username"), user_id=actor.get("id"), role=actor.get("role"),
                     action="BANK_MATCH_REVERSED", entity_type="fin_payable_bank_allocation", entity_id=allocation_id,
                     meta={"payable_id": row["payable_id"], "bank_movement_id": row["bank_movement_id"]})
        return {"allocation_id": allocation_id, "status": "REVERSED", "payable": payable}
