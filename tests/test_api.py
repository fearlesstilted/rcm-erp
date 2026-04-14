"""
Integration tests for the RCM ERP FastAPI backend (main.py).

Uses FastAPI's TestClient with an in-memory SQLite database.
The `get_db` dependency is overridden so tests never touch rcm_erp.db.

Coverage:
- Health check
- Create order (POST /api/orders)
- List orders (GET /api/orders) — empty and with data
- Get order by ID (GET /api/orders/{id}) — found and 404
- Triage: odrzut path (material triggers reject rule)
- Triage: standard path (catalog order with template)
- Triage: niestandard path (no drawing, no template)
- Soft delete template (DELETE /api/templates/{id})
- Restore template (PATCH /api/templates/{id}/restore)
- Analytics endpoint (GET /api/analytics)
- Settings read/update (GET /api/settings, PATCH /api/settings/{key})
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Import main FIRST so all models are registered in Base.metadata before we
# call create_all. The order matters: models.py must be fully loaded.
import main as app_module
from main import app, get_db
from models import Base, ConstraintRule, ProductTemplate, Setting

# ─── Shared in-memory engine ──────────────────────────────────────────────────
# StaticPool forces SQLite to reuse a single connection across all threads.
# Without it, each ASGI handler thread gets a NEW in-memory DB (empty = no tables).
_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_TEST_ENGINE)
_TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)


@pytest.fixture(scope="function")
def db_session():
    """
    Fresh tables for each test — drops and recreates using the shared engine.
    StaticPool keeps data visible across threads; drop/create gives isolation.
    """
    Base.metadata.drop_all(_TEST_ENGINE)
    Base.metadata.create_all(_TEST_ENGINE)
    session = _TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(db_session):
    """
    TestClient that uses the in-memory DB session via dependency override.
    Any endpoint that calls `get_db` will get our in-memory session instead.
    """
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def seed_reject_rule(db_session, material_value="aluminium"):
    """Insert a reject rule for the given material value."""
    rule = ConstraintRule(
        rule_name=f"Odrzut {material_value}",
        field="material",
        operator="eq",
        value=material_value,
        action="reject",
        message=f"Nie wykonujemy: {material_value}",
        is_active=True,
    )
    db_session.add(rule)
    db_session.commit()
    return rule


def seed_template(db_session, name="Wymiana zęba", category="remont", price=500.0):
    """Insert a product template into the in-memory DB."""
    tmpl = ProductTemplate(
        name=name,
        category=category,
        base_price_pln=price,
        margin_pct=0.25,
        is_active=True,
        operations_json=[],
        materials_json=[],
        instruction_blocks=[],
        machines_json=[],
    )
    db_session.add(tmpl)
    db_session.commit()
    db_session.refresh(tmpl)
    return tmpl


def seed_setting(db_session, key="labor_rate_pln", value="90"):
    """Insert a setting row."""
    s = Setting(key=key, value=value, label="Stawka robocizny (PLN/h)")
    db_session.add(s)
    db_session.commit()
    return s


def create_order(http_client, **kwargs):
    """POST /api/orders with sensible defaults, override via kwargs."""
    payload = dict(
        client="Test Klient S.A.",
        material="S355",
        deadline=str(__import__("datetime").date.today()),
        has_drawing=True,
        notes="",
        purpose="",
        estimated_value=1000.0,
        order_type="remont",
        description="Test",
        requires_visit=False,
        template_id=None,
        quantity=1,
        is_defence=False,
    )
    payload.update(kwargs)
    resp = http_client.post("/api/orders", json=payload)
    return resp


# ─── Health check ─────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ─── Order CRUD ───────────────────────────────────────────────────────────────

class TestOrders:
    def test_create_order_returns_201(self, client):
        resp = create_order(client)
        assert resp.status_code == 201
        data = resp.json()
        assert data["client"] == "Test Klient S.A."
        assert data["status"] == "draft"

    def test_create_order_generates_order_number(self, client):
        resp = create_order(client)
        assert "/" in resp.json()["order_number"]  # format: "1/2026"

    def test_list_orders_empty(self, client):
        resp = client.get("/api/orders")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_orders_returns_created(self, client):
        create_order(client)
        create_order(client, client="Drugi Klient")
        resp = client.get("/api/orders")
        assert len(resp.json()) == 2

    def test_get_order_by_id(self, client):
        order_id = create_order(client).json()["id"]
        resp = client.get(f"/api/orders/{order_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == order_id

    def test_get_order_not_found(self, client):
        resp = client.get("/api/orders/9999")
        assert resp.status_code == 404

    def test_order_number_increments(self, client):
        n1 = create_order(client).json()["order_number"]
        n2 = create_order(client).json()["order_number"]
        seq1 = int(n1.split("/")[0])
        seq2 = int(n2.split("/")[0])
        assert seq2 == seq1 + 1

    def test_is_defence_flag_stored(self, client):
        resp = create_order(client, is_defence=True)
        assert resp.json()["is_defence"] is True


# ─── Triage ───────────────────────────────────────────────────────────────────

class TestTriage:
    def test_triage_odrzut_material(self, client, db_session):
        """Order with rejected material → odrzut branch, order status=rejected."""
        seed_reject_rule(db_session, "aluminium")
        order_id = create_order(client, material="aluminium").json()["id"]

        resp = client.post(f"/api/orders/{order_id}/triage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["branch"] == "odrzut"

        # Verify order status updated in DB
        order_resp = client.get(f"/api/orders/{order_id}")
        assert order_resp.json()["status"] == "rejected"

    def test_triage_standard_catalog(self, client, db_session):
        """Catalog order with valid template_id → standard branch."""
        tmpl = seed_template(db_session)
        order_id = create_order(
            client,
            order_type="catalog",
            template_id=tmpl.id,
            has_drawing=False,
        ).json()["id"]

        resp = client.post(f"/api/orders/{order_id}/triage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["branch"] == "standard"
        assert data["template_id"] == tmpl.id

    def test_triage_niestandard_no_drawing(self, client, db_session):
        """No drawing, not catalog, no templates → niestandard."""
        order_id = create_order(client, has_drawing=False, order_type="remont").json()["id"]
        resp = client.post(f"/api/orders/{order_id}/triage")
        assert resp.status_code == 200
        assert resp.json()["branch"] == "niestandard"

    def test_triage_order_not_found(self, client):
        resp = client.post("/api/orders/9999/triage")
        assert resp.status_code == 404

    def test_triage_reject_beats_catalog(self, client, db_session):
        """Even catalog order gets rejected if material rule triggers."""
        seed_reject_rule(db_session, "aluminium")
        tmpl = seed_template(db_session)
        order_id = create_order(
            client,
            material="aluminium",
            order_type="catalog",
            template_id=tmpl.id,
        ).json()["id"]

        resp = client.post(f"/api/orders/{order_id}/triage")
        assert resp.json()["branch"] == "odrzut"


# ─── Templates (soft delete) ──────────────────────────────────────────────────

class TestTemplates:
    def test_list_templates(self, client, db_session):
        seed_template(db_session, "Szablon A")
        resp = client.get("/api/templates")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "Szablon A" in names

    def test_soft_delete_sets_inactive(self, client, db_session):
        """DELETE /api/templates/{id} sets is_active=False, doesn't remove from DB."""
        tmpl = seed_template(db_session)
        resp = client.delete(f"/api/templates/{tmpl.id}")
        assert resp.status_code == 204  # endpoint uses status_code=204 (no content)

        # Template should no longer appear in the active list
        list_resp = client.get("/api/templates")
        ids = [t["id"] for t in list_resp.json()]
        assert tmpl.id not in ids

    def test_soft_delete_not_found(self, client):
        resp = client.delete("/api/templates/9999")
        assert resp.status_code == 404

    def test_restore_template(self, client, db_session):
        """PATCH /api/templates/{id}/restore brings the template back."""
        tmpl = seed_template(db_session)
        client.delete(f"/api/templates/{tmpl.id}")

        resp = client.patch(f"/api/templates/{tmpl.id}/restore")
        assert resp.status_code == 200

        list_resp = client.get("/api/templates")
        ids = [t["id"] for t in list_resp.json()]
        assert tmpl.id in ids


# ─── Settings ─────────────────────────────────────────────────────────────────

class TestSettings:
    def test_list_settings(self, client, db_session):
        seed_setting(db_session, "labor_rate_pln", "90")
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        keys = [s["key"] for s in resp.json()]
        assert "labor_rate_pln" in keys

    def test_update_setting(self, client, db_session):
        seed_setting(db_session, "labor_rate_pln", "90")
        resp = client.patch("/api/settings/labor_rate_pln", json={"value": "110"})
        assert resp.status_code == 200
        assert resp.json()["value"] == "110"

    def test_update_setting_not_found(self, client):
        resp = client.patch("/api/settings/nonexistent", json={"value": "1"})
        assert resp.status_code == 404


# ─── Analytics ────────────────────────────────────────────────────────────────

class TestAnalytics:
    def test_analytics_returns_summary(self, client):
        resp = client.get("/api/analytics")
        assert resp.status_code == 200
        data = resp.json()
        # AnalyticsSummary has flat fields, not a nested by_branch dict
        assert "total_orders" in data
        assert "odrzut_count" in data
        assert "standard_count" in data
        assert "niestandard_count" in data

    def test_analytics_counts_orders(self, client, db_session):
        create_order(client)
        create_order(client)
        resp = client.get("/api/analytics")
        assert resp.json()["total_orders"] == 2
