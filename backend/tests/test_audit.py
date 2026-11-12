import pytest
from app.models.enums import AuditEventType, SeverityLevel
from app.engine.audit import create_audit_record

def test_create_audit_record():
    record = create_audit_record(
        event_type=AuditEventType.TAMPER_DETECTED,
        actor="Engine Sentinel",
        role="SECURITY",
        module="TRUST_ENGINE",
        summary="Tampering prevented",
        entity_id="prop-01",
        fingerprint="sha256_mock_hash",
        details={"diff": "+$100"},
        severity=SeverityLevel.ALERT,
    )
    assert record["event_type"] == AuditEventType.TAMPER_DETECTED
    assert record["actor"] == "Engine Sentinel"
    assert record["severity"] == SeverityLevel.ALERT
    assert record["fingerprint"] == "sha256_mock_hash"
    assert record["details"]["diff"] == "+$100"
