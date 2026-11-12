import pytest
from datetime import datetime, timezone, timedelta
from app.engine.approval import verify_approval_integrity

def test_valid_approval_passes():
    """An unexpired, unrevoked approval with exact fingerprint match is valid."""
    h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": False,
    }
    policy = {"is_revoked": False}

    res = verify_approval_integrity(approval, expected_fingerprint_hash=h, policy=policy)
    assert res.is_valid is True

def test_tampered_fingerprint_invalidates_approval():
    """If expected hash differs from approval hash, approval MUST be rejected."""
    original_h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    tampered_h = "cca9cf44db0264426543b355d9d95f87b3fc4eb1851acbdf0e7b897b2520371a"
    approval = {
        "fingerprint_hash": original_h,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": False,
    }

    res = verify_approval_integrity(approval, expected_fingerprint_hash=tampered_h)
    assert res.is_valid is False
    assert "Approval hash mismatch" in res.reason

def test_expired_approval_rejected():
    """Approval past its expiry timestamp is rejected."""
    h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "is_revoked": False,
    }

    res = verify_approval_integrity(approval, expected_fingerprint_hash=h)
    assert res.is_valid is False
    assert "expired" in res.reason.lower()

def test_revoked_approval_rejected():
    """Explicitly revoked approval is rejected."""
    h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": True,
        "revoked_reason": "User cancelled purchase",
    }

    res = verify_approval_integrity(approval, expected_fingerprint_hash=h)
    assert res.is_valid is False
    assert "revoked" in res.reason.lower()

def test_revoked_policy_invalidates_dependent_approval():
    """If the underlying policy was revoked, dependent approvals become invalid."""
    h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    approval = {
        "fingerprint_hash": h,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=2),
        "is_revoked": False,
    }
    policy = {"is_revoked": True}

    res = verify_approval_integrity(approval, expected_fingerprint_hash=h, policy=policy)
    assert res.is_valid is False
    assert "policy was revoked" in res.reason.lower()
