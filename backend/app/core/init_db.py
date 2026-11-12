from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.core.database import engine, Base, AsyncSessionLocal
from app.models.enums import (
    UserRole,
    TransactionLifecycleState,
    ModuleScope,
    ApprovalMode,
    ApprovalType,
    AuditEventType,
    SeverityLevel,
)
from app.models.entities import (
    User,
    CareProfile,
    GuardianRelationship,
    DelegatedPermission,
    ConsentPolicy,
    Merchant,
    Product,
    PurchaseProposal,
    ProposalItem,
    TransactionFingerprint,
    Approval,
    AuditEvent,
    BusinessProfile,
    Invoice,
    GroupPurchase,
    GroupParticipant,
    ReorderSchedule,
    BudgetReservation,
)
from app.engine.fingerprint import compute_transaction_fingerprint

def migrate_tables_sync(connection):
    """Safely adds missing columns to existing SQLite database without dropping tables or losing records."""
    try:
        result = connection.exec_driver_sql("PRAGMA table_info(transactions)")
        cols = {row[1] for row in result}
        if cols:
            if "capture_idempotency_key" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN capture_idempotency_key VARCHAR(64)")
            if "is_simulated" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN is_simulated BOOLEAN DEFAULT 0 NOT NULL")
            if "reconciliation_status" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN reconciliation_status VARCHAR(50)")
            if "version" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN version INTEGER DEFAULT 1 NOT NULL")
            if "paypal_order_id" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN paypal_order_id VARCHAR(100)")
            if "paypal_capture_id" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN paypal_capture_id VARCHAR(100)")
            if "paypal_status" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN paypal_status VARCHAR(50)")
            if "paypal_approval_url" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN paypal_approval_url VARCHAR(500)")
            if "fingerprint_sha256" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN fingerprint_sha256 VARCHAR(64) DEFAULT '' NOT NULL")
            if "first_dispatched_at" not in cols:
                connection.exec_driver_sql("ALTER TABLE transactions ADD COLUMN first_dispatched_at TIMESTAMP")

        # Migrate care_profiles
        result_cp = connection.exec_driver_sql("PRAGMA table_info(care_profiles)")
        cp_cols = {row[1] for row in result_cp}
        if cp_cols:
            if "reserved_cents" not in cp_cols:
                connection.exec_driver_sql("ALTER TABLE care_profiles ADD COLUMN reserved_cents INTEGER DEFAULT 0 NOT NULL")
            if "simulated_monthly_spent_cents" not in cp_cols:
                connection.exec_driver_sql("ALTER TABLE care_profiles ADD COLUMN simulated_monthly_spent_cents INTEGER DEFAULT 0 NOT NULL")

        # Migrate consent_policies
        result_pol = connection.exec_driver_sql("PRAGMA table_info(consent_policies)")
        pol_cols = {row[1] for row in result_pol}
        if pol_cols:
            if "reserved_cents" not in pol_cols:
                connection.exec_driver_sql("ALTER TABLE consent_policies ADD COLUMN reserved_cents INTEGER DEFAULT 0 NOT NULL")
            if "simulated_monthly_spent_cents" not in pol_cols:
                connection.exec_driver_sql("ALTER TABLE consent_policies ADD COLUMN simulated_monthly_spent_cents INTEGER DEFAULT 0 NOT NULL")

        # Migrate users (Phase 7: Password Hash)
        result_usr = connection.exec_driver_sql("PRAGMA table_info(users)")
        usr_cols = {row[1] for row in result_usr}
        if usr_cols:
            if "password_hash" not in usr_cols:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)")
            # Set default password hash for usr-alex-01 (meridian2026)
            connection.exec_driver_sql(
                "UPDATE users SET password_hash = 'pbkdf2_sha256$100000$c4a2a16d84a77fe29b69b59628148b52$bf605e6b7780f2d4e89bb6ea8e932402422774dbb1d0339d1b09bce05500ce4c' WHERE id = 'usr-alex-01' AND (password_hash IS NULL OR password_hash = '')"
            )
    except Exception:
        pass

async def seed_phase5_fixtures_if_missing(session):
    """Ensures Phase 5 fixtures (pol-003, pol-004, prop-102) exist in database."""
    now = datetime.now(timezone.utc)
    one_year_later = now + timedelta(days=365)

    # 1. Check pol-003 (BUSINESS)
    p3 = await session.execute(select(ConsentPolicy).where(ConsentPolicy.id == "pol-003"))
    if not p3.scalar_one_or_none():
        policy_biz = ConsentPolicy(
            id="pol-003",
            user_id="usr-alex-01",
            scope=ModuleScope.BUSINESS,
            title="Office Cloud & Software Operations",
            max_per_order_cents=30000,
            monthly_budget_cents=120000,
            monthly_spent_cents=64000,
            currency="USD",
            allowed_merchants=["AWS Cloud Sandbox", "GitHub Enterprise", "Figma Inc"],
            allowed_categories=["Infrastructure", "SaaS", "Dev Tools"],
            approval_mode=ApprovalMode.EXPLICIT_ALWAYS,
            valid_until=one_year_later,
            is_revoked=False,
            policy_version=1,
        )
        session.add(policy_biz)

    # 2. Check pol-004 (COLLECTIVE)
    p4 = await session.execute(select(ConsentPolicy).where(ConsentPolicy.id == "pol-004"))
    if not p4.scalar_one_or_none():
        policy_col = ConsentPolicy(
            id="pol-004",
            user_id="usr-alex-01",
            scope=ModuleScope.COLLECTIVE,
            title="Group Equipment Pool Rules",
            max_per_order_cents=50000,
            monthly_budget_cents=80000,
            monthly_spent_cents=0,
            currency="USD",
            allowed_merchants=["CameraHouse Demo", "B&H Sandbox"],
            allowed_categories=["Video Production", "Studio Gear"],
            approval_mode=ApprovalMode.EXPLICIT_ALWAYS,
            valid_until=one_year_later,
            is_revoked=False,
            policy_version=1,
        )
        session.add(policy_col)

    # 3. Check prop-102 (CARE)
    p102 = await session.execute(select(PurchaseProposal).where(PurchaseProposal.id == "prop-102"))
    if not p102.scalar_one_or_none():
        prop_care = PurchaseProposal(
            id="prop-102",
            user_id="usr-eleanor-02",
            care_profile_id="care-eleanor-01",
            module=ModuleScope.CARE,
            title="Continuous Glucose Monitor Sensor Refill (14-day Pods)",
            merchant_id="m-carerx-02",
            merchant_name="CareRx Pharmacy Sandbox",
            subtotal_cents=3250,
            tax_cents=260,
            shipping_cents=0,
            total_cents=3510,
            currency="USD",
            ai_reasoning="Care reorder for Eleanor Vance. Whitelisted merchant and within Care monthly budget.",
            state=TransactionLifecycleState.AWAITING_APPROVAL,
        )
        session.add(prop_care)
        await session.flush()

        item_care = ProposalItem(
            id="item-cgm-01",
            proposal_id="prop-102",
            product_id="prod-cgm-02",
            name="Continuous Glucose Monitor Sensor Pods (14-day)",
            sku="cgm-14d-pod",
            category="Medical Supplies",
            quantity=1,
            unit_price_cents=3250,
            total_cents=3250,
        )
        session.add(item_care)

        canonical_json, sha_hash = compute_transaction_fingerprint(
            user_id="usr-eleanor-02",
            module=ModuleScope.CARE.value,
            merchant_id="m-carerx-02",
            merchant_name="CareRx Pharmacy Sandbox",
            items=[{
                "product_id": "prod-cgm-02",
                "name": "Continuous Glucose Monitor Sensor Pods (14-day)",
                "sku": "cgm-14d-pod",
                "category": "Medical Supplies",
                "quantity": 1,
                "unit_price_cents": 3250,
                "total_cents": 3250,
            }],
            total_cents=3510,
            currency="USD",
            policy_version=1,
            subtotal_cents=3250,
            tax_cents=260,
            shipping_cents=0,
            care_profile_id="care-eleanor-01",
        )
        fp_care = TransactionFingerprint(
            id="fp-cgm-02",
            proposal_id="prop-102",
            canonical_payload=canonical_json,
            sha256_hash=sha_hash,
            policy_version=1,
        )
        session.add(fp_care)

    # 4. Check CARE Reorder Schedules
    s1 = await session.execute(select(ReorderSchedule).where(ReorderSchedule.id == "sched-cgm-01"))
    if not s1.scalar_one_or_none():
        session.add(ReorderSchedule(
            id="sched-cgm-01",
            care_profile_id="care-eleanor-01",
            product_name="Continuous Glucose Monitor Sensor Pods",
            category="Medical Supplies",
            preferred_merchant="CareRx Pharmacy Sandbox",
            unit_cost_cents=3250,
            frequency_days=25,
            days_remaining=3,
            auto_reorder_eligible=True,
            last_ordered_at=now - timedelta(days=22),
        ))

    s2 = await session.execute(select(ReorderSchedule).where(ReorderSchedule.id == "sched-vit-02"))
    if not s2.scalar_one_or_none():
        session.add(ReorderSchedule(
            id="sched-vit-02",
            care_profile_id="care-eleanor-01",
            product_name="Vitamin D3 2000IU Softgels",
            category="Vitamins",
            preferred_merchant="CareRx Pharmacy Sandbox",
            unit_cost_cents=1600,
            frequency_days=60,
            days_remaining=18,
            auto_reorder_eligible=True,
            last_ordered_at=now - timedelta(days=42),
        ))

    s3 = await session.execute(select(ReorderSchedule).where(ReorderSchedule.id == "sched-cream-03"))
    if not s3.scalar_one_or_none():
        session.add(ReorderSchedule(
            id="sched-cream-03",
            care_profile_id="care-eleanor-01",
            product_name="Hypoallergenic Skin Barrier Cream",
            category="Daily Essentials",
            preferred_merchant="HealthDirect",
            unit_cost_cents=2100,
            frequency_days=30,
            days_remaining=24,
            auto_reorder_eligible=True,
            last_ordered_at=now - timedelta(days=6),
        ))

    # 5. Check FLOW Business Profile & Invoices (Idempotent seed hardening)
    bp_res = await session.execute(select(BusinessProfile).where(BusinessProfile.id == "biz-acme-01"))
    biz_prof = bp_res.scalar_one_or_none()
    if not biz_prof:
        biz_prof = BusinessProfile(
            id="biz-acme-01",
            user_id="usr-alex-01",
            company_name="Acme Design Studio",
            operating_cash_cents=1420000,
            minimum_safety_reserve_cents=800000,
            currency="USD",
        )
        session.add(biz_prof)
        await session.flush()

    flow_invoices = [
        {
            "id": "inv-rec-01",
            "business_profile_id": "biz-acme-01",
            "counterparty": "Global Logistics Partners",
            "invoice_type": "RECEIVABLE",
            "amount_cents": 345000,
            "due_date": now + timedelta(days=4),
            "status": "PENDING",
            "category": "Client Retainer",
        },
        {
            "id": "inv-rec-02",
            "business_profile_id": "biz-acme-01",
            "counterparty": "Design Client Alpha",
            "invoice_type": "RECEIVABLE",
            "amount_cents": 210000,
            "due_date": now + timedelta(days=12),
            "status": "PENDING",
            "category": "Consulting",
        },
        {
            "id": "inv-pay-01",
            "business_profile_id": "biz-acme-01",
            "counterparty": "AWS Cloud",
            "invoice_type": "PAYABLE",
            "amount_cents": 48000,
            "due_date": now + timedelta(days=2),
            "status": "PENDING",
            "category": "Infrastructure",
        },
        {
            "id": "inv-pay-02",
            "business_profile_id": "biz-acme-01",
            "counterparty": "Downtown Studio Lease",
            "invoice_type": "PAYABLE",
            "amount_cents": 180000,
            "due_date": now + timedelta(days=7),
            "status": "PENDING",
            "category": "Rent",
        },
    ]
    for inv_data in flow_invoices:
        existing_inv = await session.execute(select(Invoice).where(Invoice.id == inv_data["id"]))
        if not existing_inv.scalar_one_or_none():
            session.add(Invoice(**inv_data))

    # 6. Check COMMON Collective Group & Participants (Idempotent seed hardening)
    grp_res = await session.execute(select(GroupPurchase).where(GroupPurchase.id == "col-podcast-01"))
    group_col = grp_res.scalar_one_or_none()
    if not group_col:
        group_col = GroupPurchase(
            id="col-podcast-01",
            organizer_user_id="usr-alex-01",
            title="Co-Working Podcast Production Rig",
            target_item="Sony ZV-E10 II + Dual Mic Kit",
            total_target_cents=75000, # $750.00
            amount_collected_cents=0, # Commitments are distinct from payments
            currency="USD",
            status="OPEN",
            deadline=now + timedelta(days=6),
        )
        session.add(group_col)
        await session.flush()

    common_participants = [
        {
            "id": "part-alex-01",
            "group_purchase_id": "col-podcast-01",
            "user_id": "usr-alex-01",
            "name": "Alex Rivera (Organizer)",
            "email": "alex.rivera@demo.meridian",
            "share_cents": 18750,
            "status": "COMMITTED",
            "is_organizer": True,
        },
        {
            "id": "part-maya-02",
            "group_purchase_id": "col-podcast-01",
            "user_id": None,
            "name": "Maya Lin",
            "email": "maya@demo.io",
            "share_cents": 18750,
            "status": "COMMITTED",
            "is_organizer": False,
        },
        {
            "id": "part-marcus-03",
            "group_purchase_id": "col-podcast-01",
            "user_id": None,
            "name": "Marcus Brody",
            "email": "marcus@demo.io",
            "share_cents": 18750,
            "status": "COMMITTED",
            "is_organizer": False,
        },
        {
            "id": "part-elena-04",
            "group_purchase_id": "col-podcast-01",
            "user_id": None,
            "name": "Elena Rostova",
            "email": "elena@demo.io",
            "share_cents": 18750,
            "status": "PENDING",
            "is_organizer": False,
        },
    ]
    for part_data in common_participants:
        existing_part = await session.execute(
            select(GroupParticipant).where(GroupParticipant.id == part_data["id"])
        )
        if not existing_part.scalar_one_or_none():
            session.add(GroupParticipant(**part_data))

    await session.commit()

async def init_database():
    """Initializes tables and seeds initial synthetic test fixtures."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(migrate_tables_sync)

    async with AsyncSessionLocal() as session:
        # Check if users already seeded
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none():
            from app.api.auth_routes import hash_password
            users_without_pwd = await session.execute(select(User).where(User.password_hash.is_(None)))
            for u in users_without_pwd.scalars().all():
                u.password_hash = hash_password("Password123!")
            await session.commit()
            await seed_phase5_fixtures_if_missing(session)
            return # Already seeded

        now = datetime.now(timezone.utc)
        one_year_later = now + timedelta(days=365)

        # 1. Users
        from app.api.auth_routes import hash_password
        default_pwd_hash = hash_password("Password123!")
        user_alex = User(
            id="usr-alex-01",
            email="alex.rivera@demo.meridian",
            full_name="Alex Rivera",
            role=UserRole.CONSUMER,
            password_hash=default_pwd_hash,
            is_active=True,
        )
        user_eleanor = User(
            id="usr-eleanor-02",
            email="eleanor.vance@demo.meridian",
            full_name="Eleanor Vance",
            role=UserRole.CARE_RECIPIENT,
            password_hash=default_pwd_hash,
            is_active=True,
        )
        session.add_all([user_alex, user_eleanor])
        await session.flush()

        # 2. Care Profile & Guardianship
        care_profile = CareProfile(
            id="care-eleanor-01",
            user_id=user_eleanor.id,
            recipient_name="Eleanor Vance",
            approval_mode=ApprovalMode.CONTROLLED_AUTO,
            monthly_budget_cents=25000, # $250.00
            monthly_spent_cents=12000,  # $120.00
            currency="USD",
            notes_redacted="Authorized continuous glucose monitors and daily multivitamins.",
        )
        session.add(care_profile)
        await session.flush()

        guardian_rel = GuardianRelationship(
            id="gr-alex-eleanor-01",
            care_profile_id=care_profile.id,
            guardian_user_id=user_alex.id,
            relationship_type="Primary Financial Guardian",
            can_approve=True,
            max_approval_amount_cents=15000, # $150.00
            is_active=True,
        )
        session.add(guardian_rel)
        await session.flush()

        delegated_perm = DelegatedPermission(
            id="dp-medical-01",
            guardian_relationship_id=guardian_rel.id,
            category="Medical Supplies",
            max_per_order_cents=7500, # $75.00
            is_active=True,
        )
        session.add(delegated_perm)

        # 3. Consent Policies
        policy_personal = ConsentPolicy(
            id="pol-001",
            user_id=user_alex.id,
            scope=ModuleScope.PERSONAL,
            title="Personal Electronics & Tech",
            max_per_order_cents=15000, # $150.00
            monthly_budget_cents=40000, # $400.00
            monthly_spent_cents=8500,   # $85.00
            currency="USD",
            allowed_merchants=["Keychron Official", "Anker Direct", "BestBuy Sandbox", "Adorama"],
            allowed_categories=["Peripherals", "Cables", "Audio"],
            approval_mode=ApprovalMode.EXPLICIT_ALWAYS,
            valid_until=one_year_later,
            is_revoked=False,
            policy_version=1,
        )
        policy_care = ConsentPolicy(
            id="pol-002",
            user_id=user_eleanor.id,
            scope=ModuleScope.CARE,
            title="Assisted Care — Eleanor Recurring Essentials",
            max_per_order_cents=7500,  # $75.00
            monthly_budget_cents=25000, # $250.00
            monthly_spent_cents=12000,  # $120.00
            currency="USD",
            allowed_merchants=["CareRx Pharmacy Sandbox", "HealthDirect", "Walgreens Demo"],
            allowed_categories=["Medical Supplies", "Vitamins", "Daily Essentials"],
            approval_mode=ApprovalMode.CONTROLLED_AUTO,
            valid_until=one_year_later,
            is_revoked=False,
            policy_version=1,
        )
        session.add_all([policy_personal, policy_care])
        await session.flush()

        # 4. Merchants & Products
        merchant_keychron = Merchant(
            id="m-keychron-01",
            name="Keychron Official",
            category="Peripherals",
            trust_score=98,
            is_whitelisted=True,
        )
        merchant_carerx = Merchant(
            id="m-carerx-02",
            name="CareRx Pharmacy Sandbox",
            category="Medical Supplies",
            trust_score=99,
            is_whitelisted=True,
        )
        session.add_all([merchant_keychron, merchant_carerx])

        product_keyboard = Product(
            id="prod-k3pro-01",
            merchant_id=merchant_keychron.id,
            name="Keychron K3 Pro Ultra-Slim (Red Switches)",
            sku="k3p-red-01",
            category="Peripherals",
            unit_price_cents=8400, # $84.00
            currency="USD",
        )
        product_sensors = Product(
            id="prod-cgm-02",
            merchant_id=merchant_carerx.id,
            name="Continuous Glucose Monitor Sensor Pods (14-day)",
            sku="cgm-14d-pod",
            category="Medical Supplies",
            unit_price_cents=3250, # $32.50
            currency="USD",
        )
        session.add_all([product_keyboard, product_sensors])
        await session.flush()

        # 5. Purchase Proposals & Fingerprints
        proposal_keyboard = PurchaseProposal(
            id="prop-101",
            user_id=user_alex.id,
            module=ModuleScope.PERSONAL,
            title="Wireless Mechanical Keyboard (Quiet Switches)",
            merchant_id=merchant_keychron.id,
            merchant_name=merchant_keychron.name,
            subtotal_cents=8400,
            tax_cents=672,
            shipping_cents=0,
            total_cents=9072, # $90.72
            currency="USD",
            ai_reasoning="Matches natural language criteria for a quiet slim mechanical keyboard under $100. Merchant is whitelisted.",
            state=TransactionLifecycleState.AWAITING_APPROVAL,
        )
        session.add(proposal_keyboard)
        await session.flush()

        item_keyboard = ProposalItem(
            id="item-kb-01",
            proposal_id=proposal_keyboard.id,
            product_id=product_keyboard.id,
            name=product_keyboard.name,
            sku=product_keyboard.sku,
            category=product_keyboard.category,
            quantity=1,
            unit_price_cents=8400,
            total_cents=8400,
        )
        session.add(item_keyboard)

        # Compute & store cryptographic fingerprint
        canonical_json, sha_hash = compute_transaction_fingerprint(
            user_id=user_alex.id,
            module=ModuleScope.PERSONAL.value,
            merchant_id=merchant_keychron.id,
            merchant_name=merchant_keychron.name,
            items=[{
                "product_id": product_keyboard.id,
                "name": product_keyboard.name,
                "sku": product_keyboard.sku,
                "category": product_keyboard.category,
                "quantity": 1,
                "unit_price_cents": 8400,
                "total_cents": 8400,
            }],
            total_cents=9072,
            currency="USD",
            policy_version=1,
            subtotal_cents=8400,
            tax_cents=672,
            shipping_cents=0,
        )
        fp_keyboard = TransactionFingerprint(
            id="fp-kb-01",
            proposal_id=proposal_keyboard.id,
            canonical_payload=canonical_json,
            sha256_hash=sha_hash,
            policy_version=1,
        )
        session.add(fp_keyboard)

        # 6. Audit Events
        audit_event_1 = AuditEvent(
            id="aud-seed-001",
            timestamp=now,
            event_type=AuditEventType.PROPOSAL_CREATED,
            actor="Gemini Proposal Synthesizer",
            role="AI_ORCHESTRATOR",
            module="PERSONAL",
            summary="Synthesized Proposal #prop-101 for Keychron K3 Pro",
            entity_id=proposal_keyboard.id,
            details={"total_cents": 9072, "merchant": "Keychron Official"},
            severity=SeverityLevel.INFO,
        )
        audit_event_2 = AuditEvent(
            id="aud-seed-002",
            timestamp=now,
            event_type=AuditEventType.FINGERPRINT_GENERATED,
            actor="Trust Engine Cryptographic Kernel",
            role="SYSTEM",
            module="PERSONAL",
            summary=f"Bound canonical SHA-256 fingerprint: {sha_hash[:16]}...",
            entity_id=proposal_keyboard.id,
            fingerprint=sha_hash,
            details={"policy_version": 1},
            severity=SeverityLevel.INFO,
        )
        session.add_all([audit_event_1, audit_event_2])

        # 7. Care Reorder Schedule
        care_schedule = ReorderSchedule(
            id="sched-cgm-01",
            care_profile_id=care_profile.id,
            product_name="Continuous Glucose Monitor Sensor Pods",
            category="Medical Supplies",
            preferred_merchant="CareRx Pharmacy Sandbox",
            unit_cost_cents=3250,
            frequency_days=25,
            days_remaining=3,
            auto_reorder_eligible=True,
            last_ordered_at=now - timedelta(days=22),
        )
        session.add(care_schedule)

        # 8. Business Profile & Invoices
        biz_profile = BusinessProfile(
            id="biz-acme-01",
            user_id=user_alex.id,
            company_name="Acme Design Studio",
            operating_cash_cents=1420000, # $14,200.00
            minimum_safety_reserve_cents=800000, # $8,000.00
            currency="USD",
        )
        session.add(biz_profile)
        await session.flush()

        inv_1 = Invoice(
            id="inv-rec-01",
            business_profile_id=biz_profile.id,
            counterparty="Global Logistics Partners",
            invoice_type="RECEIVABLE",
            amount_cents=345000, # $3,450.00
            due_date=now + timedelta(days=4),
            status="PENDING",
            category="Client Retainer",
        )
        inv_2 = Invoice(
            id="inv-rec-02",
            business_profile_id=biz_profile.id,
            counterparty="Design Client Alpha",
            invoice_type="RECEIVABLE",
            amount_cents=210000, # $2,100.00
            due_date=now + timedelta(days=12),
            status="PENDING",
            category="Consulting",
        )
        inv_3 = Invoice(
            id="inv-pay-01",
            business_profile_id=biz_profile.id,
            counterparty="AWS Cloud",
            invoice_type="PAYABLE",
            amount_cents=48000, # $480.00
            due_date=now + timedelta(days=2),
            status="PENDING",
            category="Infrastructure",
        )
        inv_4 = Invoice(
            id="inv-pay-02",
            business_profile_id=biz_profile.id,
            counterparty="Downtown Studio Lease",
            invoice_type="PAYABLE",
            amount_cents=180000, # $1,800.00
            due_date=now + timedelta(days=7),
            status="PENDING",
            category="Rent",
        )
        session.add_all([inv_1, inv_2, inv_3, inv_4])

        # 9. Group Purchase
        group_col = GroupPurchase(
            id="col-podcast-01",
            organizer_user_id=user_alex.id,
            title="Co-Working Podcast Production Rig",
            target_item="Sony ZV-E10 II + Dual Mic Kit",
            total_target_cents=75000, # $750.00
            amount_collected_cents=56250, # $562.50
            currency="USD",
            status="OPEN",
            deadline=now + timedelta(days=6),
        )
        session.add(group_col)

        await session.commit()
