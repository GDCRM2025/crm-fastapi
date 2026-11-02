from contextlib import contextmanager
from unittest.mock import patch

from backend.routers import atlas_service


class FakeMappings:
    def __init__(self, rows):
        self.rows = rows
    def all(self):
        return self.rows


class FakeResult:
    def __init__(self, rows=None, scalar=True):
        self.rows = rows or []
        self.scalar_value = scalar
    def scalar(self):
        return self.scalar_value
    def mappings(self):
        return FakeMappings(self.rows)


class FakeConnection:
    def __init__(self):
        self.statements = []
    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "information_schema.tables" in sql:
            return FakeResult(scalar=True)
        if "WITH allocated AS" in sql:
            return FakeResult([{
                "id_bank_movement": 7,
                "amount": -50000,
                "applied_amount": 20000,
                "remaining_amount": 30000,
                "id_legal_entity": 2
            }])
        if "FROM fin_sii_received_documents d" in sql:
            return FakeResult([{"id": 9, "folio": "5859036", "payable_balance": 77323}])
        if "FROM fin_eventos" in sql:
            return FakeResult([{"id_evento": 11, "marca": "CAMALEON", "monto_neto": 100000}])
        return FakeResult([])


def _patched_connection(fake):
    @contextmanager
    def factory():
        yield fake
    return patch.object(atlas_service, "get_connection", factory)


def test_bank_transactions_reads_real_bank_tables_and_allocations():
    fake = FakeConnection()
    with _patched_connection(fake):
        result = atlas_service.bank_transactions()
    assert result["data"][0]["remaining_amount"] == 30000
    sql = "\\n".join(fake.statements)
    assert "fin_bank_movements" in sql
    assert "fin_bank_accounts" in sql
    assert "fin_payable_bank_allocations" in sql
    assert "UPDATE " not in sql.upper()
    assert "DELETE " not in sql.upper()


def test_payables_reads_received_dte_and_linked_balance():
    fake = FakeConnection()
    with _patched_connection(fake):
        result = atlas_service.payables()
    assert result["data"][0]["folio"] == "5859036"
    assert result["data"][0]["payable_balance"] == 77323
    sql = "\\n".join(fake.statements)
    assert "fin_sii_received_documents" in sql
    assert "fin_gastos" in sql
    assert "UPDATE " not in sql.upper()


def test_sales_reads_net_vat_transport_and_brand():
    fake = FakeConnection()
    with _patched_connection(fake):
        result = atlas_service.sales()
    assert result["data"][0]["marca"] == "CAMALEON"
    sql = "\\n".join(fake.statements)
    for field in ("monto_neto","monto_bruto","iva","traslado","tipo_cliente"):
        assert field in sql
    assert "UPDATE " not in sql.upper()
