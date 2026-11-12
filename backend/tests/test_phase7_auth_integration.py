import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.init_db import init_database


@pytest.mark.asyncio
async def test_auth_demo_credentials():
    await init_database()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/auth/demo-credentials")
        assert res.status_code == 200
        data = res.json()
        assert data["email"] == "alex@meridian.demo"
        assert data["password"] == "meridian2026"
        assert data["user_id"] == "usr-alex-01"


@pytest.mark.asyncio
async def test_auth_login_success_and_me():
    await init_database()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Login with primary demo email
        res = await client.post(
            "/api/auth/login",
            json={"email": "alex@meridian.demo", "password": "meridian2026"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user_id"] == "usr-alex-01"
        assert data["full_name"] == "Alex Rivera"

        token = data["access_token"]

        # Call /me with Bearer token
        me_res = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["user_id"] == "usr-alex-01"
        assert me_data["email"] == "alex.rivera@demo.meridian"


@pytest.mark.asyncio
async def test_auth_login_with_alias_email():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Login with database email
        res = await client.post(
            "/api/auth/login",
            json={"email": "alex.rivera@demo.meridian", "password": "meridian2026"},
        )
        assert res.status_code == 200
        assert res.json()["user_id"] == "usr-alex-01"


@pytest.mark.asyncio
async def test_auth_login_invalid_password():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/auth/login",
            json={"email": "alex@meridian.demo", "password": "wrongpassword"},
        )
        assert res.status_code == 401
        assert "detail" in res.json()


@pytest.mark.asyncio
async def test_auth_logout():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/auth/logout")
        assert res.status_code == 200
        assert res.json()["message"] == "Successfully logged out"
