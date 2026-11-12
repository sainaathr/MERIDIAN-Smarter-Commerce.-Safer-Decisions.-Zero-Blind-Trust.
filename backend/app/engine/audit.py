from datetime import datetime, timezone
from typing import Optional, Dict, Any
from app.models.enums import AuditEventType, SeverityLevel

def create_audit_record(
    event_type: AuditEventType,
    actor: str,
    role: str,
    module: str,
    summary: str,
    entity_id: Optional[str] = None,
    previous_state: Optional[str] = None,
    new_state: Optional[str] = None,
    decision: Optional[str] = None,
    fingerprint: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    severity: SeverityLevel = SeverityLevel.INFO,
) -> Dict[str, Any]:
    """
    Creates an immutable audit event payload.
    Provides structured tamper-evident application-level auditing.
    """
    return {
        "timestamp": datetime.now(timezone.utc),
        "event_type": event_type,
        "actor": actor,
        "role": role,
        "module": module,
        "summary": summary,
        "entity_id": entity_id,
        "previous_state": previous_state,
        "new_state": new_state,
        "decision": decision,
        "fingerprint": fingerprint,
        "details": details or {},
        "severity": severity,
    }
