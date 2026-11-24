import hashlib
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import get_db, engine
from app.api.deps import get_current_user, get_current_admin
from app.config import settings
from app.models import Base, User, Invoice, InvoiceAuditEvent
from app.models.accounting_period import AccountingPeriod, AccountingPeriodAudit
from app.models.financial_record import (
    FinancialRecord,
    FinancialRecordAuditEvent,
)


PDF = b"%PDF-1.4\nOffice Suite document workflow integration test\n%%EOF"


@pytest.fixture
def workflow():
    assert engine.dialect.name == "postgresql", (
        f"PostgreSQL is mandatory; detected: {engine.dialect.name}"
    )

    connection = engine.connect()
    outer_transaction = connection.begin()

    # Temporary PostgreSQL DDL inside the test transaction.
    # PostgreSQL rolls these tables back with the outer transaction.
    Base.metadata.create_all(
        bind=connection,
        tables=[
            AccountingPeriod.__table__,
            AccountingPeriodAudit.__table__,
        ],
        checkfirst=True,
    )

    TestingSession = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    db = TestingSession()
    original_overrides = app.dependency_overrides.copy()

    try:
        admin = User(
            email="workflow-test@example.invalid",
            full_name="Workflow Test Admin",
            hashed_password="not-a-real-password",
            is_active=True,
            is_superuser=True,
            role="admin",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        def override_db():
            session = TestingSession()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_current_admin] = lambda: admin

        client = TestClient(app, raise_server_exceptions=True)
        yield client, admin, TestingSession
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        db.close()

        if outer_transaction.is_active:
            outer_transaction.rollback()

        connection.close()


def test_complete_document_workflow(workflow, tmp_path, monkeypatch):
    client, admin, TestingSession = workflow
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    import app.api.invoices as invoice_api

    def fake_process(file_bytes, filename, db, user):
        return {
            "fingerprint": hashlib.sha256(file_bytes).hexdigest(),
            "ocr_text": (
                "SUPPLIER: Workflow Test Supplier\n"
                "INVOICE: INV-E2E-001\n"
                "DATE: 2026-11-23\nTOTAL: 120.00 EUR"
            ),
            "extracted_data": {
                "supplier_name": "Workflow Test Supplier",
                "supplier_address": "Test Street 1",
                "invoice_number": "INV-E2E-001",
                "supplier_tax_id": "TEST123",
                "supplier_email": "supplier@example.invalid",
                "invoice_date": "2026-11-23",
                "due_date": "2026-12-23",
                "currency": "EUR",
                "subtotal": "100.00",
                "tax_rate": "20.00",
                "tax_amount": "20.00",
                "total_amount": "120.00",
                "line_items": [],
                "extraction_method": "integration-test",
            },
            "validation": {
                "valid": True,
                "errors": [],
                "warnings": [],
                "requires_human_review": True,
                "calculated_total": "120.00",
            },
            "requires_human_review": True,
        }

    monkeypatch.setattr(invoice_api, "process_invoice", fake_process)

    # 1. Upload dokumenta.
    response = client.post(
        "/api/invoices/upload",
        data={"direction": "incoming"},
        files={"file": ("workflow-invoice.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    invoice_id = response.json()["invoice"]["id"]
    assert response.json()["invoice"]["status"] == "pending_review"

    # 2. Provera OCR ekstrakcije i pregleda.
    response = client.get(f"/api/invoices/{invoice_id}/review")
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["status"] == "pending_review"
    assert review["extraction"]["invoice_number"] == "INV-E2E-001"
    assert review["ocr_text"]
    assert review["requires_human_review"] is True

    # 3. Duplikat istog dokumenta mora biti odbijen.
    response = client.post(
        "/api/invoices/upload",
        data={"direction": "incoming"},
        files={"file": ("duplicate.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 409, response.text

    # 4. Odobravanje fakture.
    response = client.post(f"/api/invoices/{invoice_id}/approve")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"

    # 5. Preuzimanje originalnog dokumenta.
    response = client.get(f"/api/invoices/{invoice_id}/download")
    assert response.status_code == 200, response.text
    assert response.content == PDF

    # 6. Audit trag uploadovanja i odobravanja.
    response = client.get(f"/api/invoices/{invoice_id}/audit")
    assert response.status_code == 200, response.text
    events = response.json()
    event_types = {event["event_type"] for event in events}
    assert "uploaded" in event_types
    assert "approved" in event_types

    # 7. Kreiranje finansijskog zapisa povezanog sa fakturom.
    response = client.post(
        "/api/financial-records",
        json={
            "record_type": "expense",
            "record_date": "2026-11-23",
            "amount": "120.00",
            "currency": "EUR",
            "category": "Office supplies",
            "counterparty": "Workflow Test Supplier",
            "description": "Linked invoice integration test",
            "payment_method": "bank_transfer",
            "document_number": "INV-E2E-001",
            "invoice_id": invoice_id,
        },
    )
    assert response.status_code == 201, response.text
    record_id = response.json()["id"]
    assert response.json()["invoice_id"] == invoice_id

    # 8. Provera da finansijski zapis zaista čuva vezu sa fakturom.
    response = client.get(f"/api/financial-records/{record_id}")
    assert response.status_code == 200, response.text
    assert response.json()["invoice_id"] == invoice_id
    assert response.json()["amount"] == "120.00"

    # 9. Provera da faktura i dalje postoji i da je odobrena.
    response = client.get(f"/api/invoices/{invoice_id}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    assert response.json()["invoice_number"] == "INV-E2E-001"
