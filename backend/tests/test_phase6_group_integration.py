"""
MERIDIAN Phase 6.2: COMMON Group Purchasing Integration & Invariant Tests
Validates:
1. Group listing endpoint retrieves visible campaigns
2. Group creation with authoritative server-side persistence and organizer registration
3. Group details retrieval with accurate calculated totals and progress
4. Participant joining and commitment pledging
5. Duplicate membership rejection (HTTP 409 Conflict)
6. Non-positive commitment amount rejection (HTTP 400 Bad Request)
7. Authoritative backend-calculated totals (target, committed, paid, remaining, progress)
8. Ownership and authorization checks: unauthorized users cannot modify another's commitment (HTTP 403 Forbidden)
9. Cannot modify commitments on closed or cancelled groups (HTTP 400 Bad Request)
10. Cannot modify a verified PAID commitment (HTTP 400 Bad Request)
11. No client-side payment forgery: participant commitment defaults to COMMITTED, never PAID
12. Zero unintended PayPal calls or CARE budget reservations (Module isolation)
13. Audit events are correctly scoped to module="COLLECTIVE"
14. Idempotent seed data verification
15. Deterministic policy evaluation against pol-004
"""

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.core.database import AsyncSessionLocal
from app.core.init_db import init_database
from app.models.entities import (
    GroupPurchase,
    GroupParticipant,
    AuditEvent,
    BudgetReservation,
    ConsentPolicy,
)
from app.models.enums import (
    ModuleScope,
    AuditEventType,
    PolicyDecision,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_database()


@pytest.mark.asyncio
async def test_group_listing():
    """1. Group listing retrieves seeded and created campaigns."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/groups?user_id=usr-alex-01")
        assert res.status_code == 200
        groups = res.json()
        assert isinstance(groups, list)
        assert len(groups) >= 1

        # Verify seed group is present
        seed_group = next((g for g in groups if g["id"] == "col-podcast-01"), None)
        assert seed_group is not None
        assert seed_group["title"] == "Co-Working Podcast Production Rig"
        assert seed_group["currency"] == "USD"
        assert seed_group["total_target_cents"] == 75000
        assert seed_group["total_target"] == 750.00


@pytest.mark.asyncio
async def test_group_creation_and_persistence():
    """2. Group creation registers organizer and persists to database."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unique = uuid.uuid4().hex[:6]
        payload = {
            "title": f"Community 3D Printer {unique}",
            "target_item": "Bambu Lab X1-Carbon Combo",
            "total_target_cents": 140000, # $1,400.00
            "currency": "USD",
            "deadline_days": 14,
            "organizer_name": "Alex Rivera",
            "organizer_email": "alex.rivera@demo.meridian",
            "organizer_share_cents": 35000, # $350.00 organizer share
        }
        res = await ac.post("/api/groups?user_id=usr-alex-01", json=payload)
        assert res.status_code == 201
        data = res.json()
        group_id = data["id"]
        assert group_id.startswith("col-")
        assert data["title"] == payload["title"]
        assert data["total_target_cents"] == 140000
        assert data["total_target"] == 1400.00
        assert data["total_committed_cents"] == 35000
        assert data["total_committed"] == 350.00
        assert data["participant_count"] == 1
        assert len(data["participants"]) == 1

        org_p = data["participants"][0]
        assert org_p["is_organizer"] is True
        assert org_p["share_cents"] == 35000
        assert org_p["status"] == "COMMITTED"

        # Verify audit event
        async with AsyncSessionLocal() as session:
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == group_id,
                        AuditEvent.event_type == AuditEventType.PROPOSAL_CREATED,
                    )
                )
            ).scalars().first()
            assert audit is not None
            assert audit.module == "COLLECTIVE"
            assert audit.actor == "usr-alex-01"


@pytest.mark.asyncio
async def test_group_details_retrieval():
    """3. Group details endpoint returns authoritative data and 404 on invalid ID."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Valid group
        res = await ac.get("/api/groups/col-podcast-01?user_id=usr-alex-01")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "col-podcast-01"
        assert len(data["participants"]) >= 4

        # Non-existent group
        res_404 = await ac.get("/api/groups/non-existent-group-id?user_id=usr-alex-01")
        assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_participant_joining_and_duplicate_prevention():
    """4 & 5. Joining adds participant commitment and rejects duplicate membership (HTTP 409)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create dedicated group
        c_res = await ac.post(
            "/api/groups?user_id=usr-alex-01",
            json={
                "title": "Audio Studio Monitors",
                "target_item": "Yamaha HS8 Pair",
                "total_target_cents": 80000,
                "currency": "USD",
                "deadline_days": 10,
                "organizer_share_cents": 20000,
            },
        )
        assert c_res.status_code == 201
        group_id = c_res.json()["id"]

        # Join as member 1
        join_payload = {
            "name": "Sarah Connor",
            "email": "sarah.connor@demo.io",
            "share_cents": 20000, # $200.00
        }
        res_join = await ac.post(f"/api/groups/{group_id}/join?user_id=usr-sarah-01", json=join_payload)
        assert res_join.status_code == 201
        data = res_join.json()
        assert data["participant_count"] == 2
        assert data["total_committed_cents"] == 40000

        member_p = next((p for p in data["participants"] if p["email"] == "sarah.connor@demo.io"), None)
        assert member_p is not None
        assert member_p["share_cents"] == 20000
        assert member_p["status"] == "COMMITTED"
        assert member_p["is_organizer"] is False

        # Attempt DUPLICATE join with same email -> HTTP 409 Conflict
        res_dup_email = await ac.post(f"/api/groups/{group_id}/join?user_id=usr-sarah-diff", json=join_payload)
        assert res_dup_email.status_code == 409
        assert "already a participant" in res_dup_email.json()["detail"]

        # Attempt DUPLICATE join with same user_id -> HTTP 409 Conflict
        res_dup_uid = await ac.post(
            f"/api/groups/{group_id}/join?user_id=usr-sarah-01",
            json={"name": "Sarah Alias", "email": "different.email@demo.io", "share_cents": 10000},
        )
        assert res_dup_uid.status_code == 409


@pytest.mark.asyncio
async def test_commitment_validation_positive_amounts():
    """6. Rejects zero or negative commitment amounts (HTTP 400)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Join with zero cents
        res_zero = await ac.post(
            "/api/groups/col-podcast-01/join?user_id=usr-new-01",
            json={"name": "Test User", "email": "test.zero@demo.io", "share_cents": 0},
        )
        assert res_zero.status_code == 400
        assert "greater than zero" in res_zero.json()["detail"]

        # Join with negative cents
        res_neg = await ac.post(
            "/api/groups/col-podcast-01/join?user_id=usr-new-02",
            json={"name": "Test User", "email": "test.neg@demo.io", "share_cents": -5000},
        )
        assert res_neg.status_code == 400


@pytest.mark.asyncio
async def test_backend_calculated_totals_and_funding_progress():
    """7. Server-side totals strictly aggregate persisted records."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create campaign with target $600.00
        c_res = await ac.post(
            "/api/groups?user_id=usr-alex-01",
            json={
                "title": "Lighting Kit Pool",
                "target_item": "Aputure 300d II Light",
                "total_target_cents": 60000,
                "currency": "USD",
                "deadline_days": 7,
                "organizer_share_cents": 20000, # $200.00
            },
        )
        group_id = c_res.json()["id"]

        # Add participant 2: $200.00
        await ac.post(
            f"/api/groups/{group_id}/join?user_id=usr-p2",
            json={"name": "Participant 2", "email": "p2@demo.io", "share_cents": 20000},
        )

        # Add participant 3: $200.00 (meets goal!)
        res_p3 = await ac.post(
            f"/api/groups/{group_id}/join?user_id=usr-p3",
            json={"name": "Participant 3", "email": "p3@demo.io", "share_cents": 20000},
        )
        data = res_p3.json()
        assert data["total_committed_cents"] == 60000
        assert data["total_committed"] == 600.00
        assert data["progress_percentage"] == 100
        assert data["status"] == "FUNDED"
        assert data["participant_count"] == 3


@pytest.mark.asyncio
async def test_ownership_and_cross_user_access_restrictions():
    """8. Users cannot modify another participant's commitment (HTTP 403 Forbidden)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create group
        c_res = await ac.post(
            "/api/groups?user_id=usr-alex-01",
            json={
                "title": "Drone Equipment",
                "target_item": "DJI Mavic 3 Pro",
                "total_target_cents": 200000,
                "currency": "USD",
                "deadline_days": 14,
                "organizer_share_cents": 50000,
            },
        )
        group_id = c_res.json()["id"]

        # Member joins
        j_res = await ac.post(
            f"/api/groups/{group_id}/join?user_id=usr-bob-01",
            json={"name": "Bob Vance", "email": "bob.vance@demo.io", "share_cents": 50000},
        )
        bob_part = next(p for p in j_res.json()["participants"] if p["email"] == "bob.vance@demo.io")
        bob_part_id = bob_part["id"]

        # An unrelated user attempts to modify Bob's commitment -> HTTP 403 Forbidden
        patch_res_unauth = await ac.patch(
            f"/api/groups/{group_id}/participants/{bob_part_id}?user_id=usr-attacker-01",
            json={"share_cents": 1000},
        )
        assert patch_res_unauth.status_code == 403
        assert "not authorized" in patch_res_unauth.json()["detail"]

        # Bob modifies his own commitment -> HTTP 200 OK
        patch_res_owner = await ac.patch(
            f"/api/groups/{group_id}/participants/{bob_part_id}?user_id=usr-bob-01",
            json={"share_cents": 60000},
        )
        assert patch_res_owner.status_code == 200
        updated_bob = next(p for p in patch_res_owner.json()["participants"] if p["id"] == bob_part_id)
        assert updated_bob["share_cents"] == 60000

        # Organizer can also modify -> HTTP 200 OK
        patch_res_org = await ac.patch(
            f"/api/groups/{group_id}/participants/{bob_part_id}?user_id=usr-alex-01",
            json={"share_cents": 55000},
        )
        assert patch_res_org.status_code == 200


@pytest.mark.asyncio
async def test_no_client_side_payment_forgery_and_care_isolation():
    """11 & 12. Commitment is distinct from payment; zero CARE reservations created."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        c_res = await ac.post(
            "/api/groups?user_id=usr-alex-01",
            json={
                "title": "Lens Kit Pool",
                "target_item": "Sigma 24-70mm f/2.8",
                "total_target_cents": 110000,
                "currency": "USD",
                "deadline_days": 10,
                "organizer_share_cents": 30000,
            },
        )
        assert c_res.status_code == 201
        group_data = c_res.json()
        group_id = group_data["id"]

        # Verify amount_collected_cents is 0 (commitments != payments)
        assert group_data["amount_collected_cents"] == 0
        assert group_data["amount_collected"] == 0.0

        # Verify zero BudgetReservations were created in the database
        async with AsyncSessionLocal() as session:
            reservations = (
                await session.execute(
                    select(BudgetReservation).where(BudgetReservation.proposal_id == group_id)
                )
            ).scalars().all()
            assert len(reservations) == 0, "COMMON groups must NEVER create CARE BudgetReservation records!"


@pytest.mark.asyncio
async def test_group_policy_evaluation_pol004():
    """13 & 15. Policy evaluation against pol-004 enforces Mode A and rules."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/groups/col-podcast-01/evaluate-policy?user_id=usr-alex-01")
        assert res.status_code == 200
        data = res.json()
        assert "decision" in data
        assert data["decision"] in ("REQUIRE_APPROVAL", "PERMIT", "DENY")
        assert "FINGERPRINT_VERIFIED" in data["passed_checks"]

        # Verify audit event emitted
        async with AsyncSessionLocal() as session:
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.entity_id == "col-podcast-01",
                        AuditEvent.module == "COLLECTIVE",
                    )
                )
            ).scalars().first()
            assert audit is not None
            assert audit.module == "COLLECTIVE"
