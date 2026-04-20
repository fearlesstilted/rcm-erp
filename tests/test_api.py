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
- Structured quote v2 (POST /api/orders/{id}/quote/structured)
- Status transition: niestandard → quoted → in_production
- Confirm order (POST /api/orders/{id}/confirm)
- Confirm guard: 409 on wrong status
- Save as template (POST /api/orders/{id}/save-as-template)
- Attachments: list (GET /api/orders/{id}/attachments) — empty
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


# ─── Structured Quote v2 ──────────────────────────────────────────────────────

class TestStructuredQuote:
    """POST /api/orders/{id}/quote/structured — formula: procesy + mat + waga + spawanie."""

    def _niestandard_order_id(self, client, db_session):
        """Helper: create an order and triage it to niestandard."""
        seed_setting(db_session)
        order_id = create_order(client, has_drawing=False, order_type="remont").json()["id"]
        client.post(f"/api/orders/{order_id}/triage")
        return order_id

    def test_structured_quote_returns_201(self, client, db_session):
        order_id = self._niestandard_order_id(client, db_session)
        resp = client.post(f"/api/orders/{order_id}/quote/structured", json={
            "processes": [{"name": "Cięcie", "cost": 150.0}],
            "material_cost": 500.0,
            "weight_kg": 20.0,
            "weight_rate_pln_kg": 15.0,
            "welding_hours": 3.0,
            "overhead_pct": 0.10,
            "margin_pct": 0.25,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["total_net"] > 0
        assert data["estimate_version"] == "v3"

    def test_structured_quote_formula(self, client, db_session):
        """Verify v3 formula: (ops_total + material_total + extra_labor) * overhead * margin."""
        seed_setting(db_session, "labor_rate_pln", "100")
        order_id = create_order(client, has_drawing=False).json()["id"]
        client.post(f"/api/orders/{order_id}/triage")

        resp = client.post(f"/api/orders/{order_id}/quote/structured", json={
            # ops: hours × rate = 3h × 100 PLN/h = 300
            "processes": [{"name": "Spawanie", "hours": 3.0, "rate_per_hour": 100.0, "cost": 0}],
            # material: 20 kg × 20 PLN/kg = 400
            "material_weight_kg": 20.0,
            "material_price_per_kg": 20.0,
            "labor_hours": 0.0,
            "overhead_pct": 0.0,
            "margin_pct": 0.0,
        })
        # base = 300 + 400 = 700, overhead=0, margin=0 → 700
        assert resp.json()["total_net"] == 700.0

    def test_structured_quote_flips_status_to_quoted(self, client, db_session):
        """After saving structured quote, order.status must become 'quoted'."""
        order_id = self._niestandard_order_id(client, db_session)
        client.post(f"/api/orders/{order_id}/quote/structured", json={
            "processes": [], "material_cost": 100.0, "weight_kg": 0,
            "weight_rate_pln_kg": 15, "welding_hours": 1,
        })
        order = client.get(f"/api/orders/{order_id}").json()
        assert order["status"] == "quoted"

    def test_structured_quote_upserts(self, client, db_session):
        """Submitting twice should update the existing quote, not create a second one."""
        order_id = self._niestandard_order_id(client, db_session)
        base = {"processes": [], "material_cost": 100.0, "weight_kg": 0,
                "weight_rate_pln_kg": 15, "welding_hours": 0}
        client.post(f"/api/orders/{order_id}/quote/structured", json=base)
        base["material_cost"] = 999.0
        resp = client.post(f"/api/orders/{order_id}/quote/structured", json=base)
        assert resp.status_code == 201
        # GET should return the updated price
        q = client.get(f"/api/orders/{order_id}/quote").json()
        assert q["material_cost"] == 999.0

    def test_quote_edit_preserves_quoted_status(self, client, db_session):
        """Editing a quote after first save must NOT demote/change status."""
        order_id = self._niestandard_order_id(client, db_session)
        base = {"processes": [], "material_cost": 100.0, "weight_kg": 0,
                "weight_rate_pln_kg": 15, "welding_hours": 0}
        client.post(f"/api/orders/{order_id}/quote/structured", json=base)
        # Status is now 'quoted'; second save = edit
        base["material_cost"] = 777.0
        resp = client.post(f"/api/orders/{order_id}/quote/structured", json=base)
        assert resp.status_code == 201
        order = client.get(f"/api/orders/{order_id}").json()
        assert order["status"] == "quoted"

    def test_quote_edit_updates_last_edited_at(self, client, db_session):
        """last_edited_at must be set on each save and move forward on edit."""
        order_id = self._niestandard_order_id(client, db_session)
        base = {"processes": [], "material_cost": 100.0, "weight_kg": 0,
                "weight_rate_pln_kg": 15, "welding_hours": 0}
        first = client.post(f"/api/orders/{order_id}/quote/structured", json=base).json()
        assert first.get("last_edited_at") is not None
        base["material_cost"] = 200.0
        second = client.post(f"/api/orders/{order_id}/quote/structured", json=base).json()
        assert second["last_edited_at"] >= first["last_edited_at"]

    def test_quote_edit_allowed_in_in_production(self, client, db_session):
        """Wycena editable even after confirm → in_production."""
        order_id = self._niestandard_order_id(client, db_session)
        base = {"processes": [], "material_cost": 100.0, "weight_kg": 0,
                "weight_rate_pln_kg": 15, "welding_hours": 0}
        client.post(f"/api/orders/{order_id}/quote/structured", json=base)
        client.post(f"/api/orders/{order_id}/confirm")  # quoted → in_production
        base["material_cost"] = 555.0
        resp = client.post(f"/api/orders/{order_id}/quote/structured", json=base)
        assert resp.status_code == 201
        order = client.get(f"/api/orders/{order_id}").json()
        assert order["status"] == "in_production"


# ─── Status transitions ───────────────────────────────────────────────────────

class TestStatusTransitions:
    """Status machine: niestandard → quoted → in_production."""

    def test_simple_quote_also_flips_to_quoted(self, client, db_session):
        """Legacy /quote endpoint should also set status=quoted for niestandard orders."""
        seed_setting(db_session)
        order_id = create_order(client, has_drawing=False).json()["id"]
        client.post(f"/api/orders/{order_id}/triage")
        client.post(f"/api/orders/{order_id}/quote", json={
            "labor_hours": 2.0, "material_cost": 100.0,
            "overhead_pct": 0.1, "margin_pct": 0.25,
        })
        order = client.get(f"/api/orders/{order_id}").json()
        assert order["status"] == "quoted"

    def test_confirm_quoted_order(self, client, db_session):
        """POST /confirm on quoted order → status becomes in_production."""
        seed_setting(db_session)
        order_id = create_order(client, has_drawing=False).json()["id"]
        client.post(f"/api/orders/{order_id}/triage")
        client.post(f"/api/orders/{order_id}/quote/structured", json={
            "processes": [], "material_cost": 500.0, "weight_kg": 0,
            "weight_rate_pln_kg": 15, "welding_hours": 0,
        })
        resp = client.post(f"/api/orders/{order_id}/confirm")
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_production"

    def test_confirm_rejects_non_quoted_status(self, client, db_session):
        """POST /confirm on a draft/niestandard order → 409 Conflict."""
        order_id = create_order(client).json()["id"]
        resp = client.post(f"/api/orders/{order_id}/confirm")
        assert resp.status_code == 409

    def test_confirm_not_found(self, client):
        resp = client.post("/api/orders/9999/confirm")
        assert resp.status_code == 404


# ─── Save as template ─────────────────────────────────────────────────────────

class TestSaveAsTemplate:
    def test_save_creates_template(self, client, db_session):
        """POST /save-as-template creates a ProductTemplate from the order+quote."""
        seed_setting(db_session)
        order_id = create_order(client, has_drawing=False, material="S355").json()["id"]
        client.post(f"/api/orders/{order_id}/triage")
        client.post(f"/api/orders/{order_id}/quote/structured", json={
            "processes": [{"name": "Spawanie TIG", "cost": 300.0}],
            "material_cost": 200.0, "weight_kg": 15.0,
            "weight_rate_pln_kg": 15.0, "welding_hours": 2.0,
        })
        resp = client.post(f"/api/orders/{order_id}/save-as-template",
                           json={"name": "Nowy szablon z zlecenia"})
        assert resp.status_code == 201
        tmpl = resp.json()
        assert tmpl["name"] == "Nowy szablon z zlecenia"
        assert tmpl["is_active"] is True

    def test_save_template_appears_in_catalog(self, client, db_session):
        """Template saved from order should appear in GET /api/templates."""
        seed_setting(db_session)
        order_id = create_order(client, has_drawing=False).json()["id"]
        client.post(f"/api/orders/{order_id}/triage")
        client.post(f"/api/orders/{order_id}/quote/structured", json={
            "processes": [], "material_cost": 100.0,
            "weight_kg": 0, "weight_rate_pln_kg": 15, "welding_hours": 0,
        })
        client.post(f"/api/orders/{order_id}/save-as-template",
                    json={"name": "Test katalog"})
        templates = client.get("/api/templates").json()
        names = [t["name"] for t in templates]
        assert "Test katalog" in names

    def test_save_template_not_found(self, client):
        resp = client.post("/api/orders/9999/save-as-template", json={"name": "X"})
        assert resp.status_code == 404


# ─── Attachments ─────────────────────────────────────────────────────────────

class TestAttachments:
    def test_list_attachments_empty(self, client, db_session):
        """GET /attachments on a fresh order should return empty list."""
        order_id = create_order(client).json()["id"]
        resp = client.get(f"/api/orders/{order_id}/attachments")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_delete_attachment_not_found(self, client):
        resp = client.delete("/api/attachments/9999")
        assert resp.status_code == 404


# ─── Production pipeline (start / complete / deliver) ─────────────────────────

class TestProductionPipeline:
    def _order_in_production(self, client, db_session):
        """Helper: create order, triage → niestandard, quote, confirm → in_production."""
        seed_setting(db_session)
        order_id = create_order(client, has_drawing=False).json()["id"]
        client.post(f"/api/orders/{order_id}/triage")
        client.post(f"/api/orders/{order_id}/quote/structured", json={
            "processes": [{"name": "Spawanie", "cost": 400.0}],
            "material_cost": 100.0, "weight_kg": 10.0,
            "weight_rate_pln_kg": 15.0, "welding_hours": 1.0,
        })
        client.post(f"/api/orders/{order_id}/confirm")
        return order_id

    def test_start_moves_to_w_trakcie(self, client, db_session):
        order_id = self._order_in_production(client, db_session)
        resp = client.post(f"/api/orders/{order_id}/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "w_trakcie"

    def test_complete_moves_to_gotowe(self, client, db_session):
        order_id = self._order_in_production(client, db_session)
        client.post(f"/api/orders/{order_id}/start")
        resp = client.post(f"/api/orders/{order_id}/complete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "gotowe"

    def test_deliver_moves_to_wydane(self, client, db_session):
        order_id = self._order_in_production(client, db_session)
        client.post(f"/api/orders/{order_id}/start")
        client.post(f"/api/orders/{order_id}/complete")
        resp = client.post(f"/api/orders/{order_id}/deliver")
        assert resp.status_code == 200
        assert resp.json()["status"] == "wydane"

    def test_start_guard_wrong_status(self, client, db_session):
        """POST /start on a non in_production order returns 409."""
        order_id = create_order(client).json()["id"]
        resp = client.post(f"/api/orders/{order_id}/start")
        assert resp.status_code == 409

    def test_complete_guard_wrong_status(self, client, db_session):
        """POST /complete on a non w_trakcie order returns 409."""
        order_id = create_order(client).json()["id"]
        resp = client.post(f"/api/orders/{order_id}/complete")
        assert resp.status_code == 409

    def test_deliver_guard_wrong_status(self, client, db_session):
        """POST /deliver on a non gotowe order returns 409."""
        order_id = create_order(client).json()["id"]
        resp = client.post(f"/api/orders/{order_id}/deliver")
        assert resp.status_code == 409

    def test_full_pipeline_404(self, client):
        for endpoint in ("start", "complete", "deliver"):
            resp = client.post(f"/api/orders/9999/{endpoint}")
            assert resp.status_code == 404, f"{endpoint} should 404"


# ─── PATCH order ──────────────────────────────────────────────────────────────

class TestPatchOrder:
    def test_patch_updates_client(self, client, db_session):
        """PATCH /api/orders/{id} — zmiana klienta."""
        order_id = create_order(client).json()["id"]
        resp = client.patch(f"/api/orders/{order_id}", json={"client": "Nowy Klient S.A."})
        assert resp.status_code == 200
        assert resp.json()["client"] == "Nowy Klient S.A."

    def test_patch_partial_update(self, client, db_session):
        """PATCH nie nadpisuje pól których nie wysłaliśmy."""
        order_id = create_order(client).json()["id"]
        original = client.get(f"/api/orders/{order_id}").json()
        client.patch(f"/api/orders/{order_id}", json={"notes": "Pilne!"})
        updated = client.get(f"/api/orders/{order_id}").json()
        assert updated["notes"] == "Pilne!"
        assert updated["client"] == original["client"]   # niezmieniony

    def test_patch_404(self, client):
        """PATCH nieistniejącego zlecenia → 404."""
        resp = client.patch("/api/orders/9999", json={"client": "X"})
        assert resp.status_code == 404


# ─── XLSX export ──────────────────────────────────────────────────────────────

# ─── Approved Materials CRUD ─────────────────────────────────────────────────

class TestApprovedMaterials:
    def test_list_empty(self, client):
        resp = client.get("/api/approved-materials")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_material(self, client):
        resp = client.post("/api/approved-materials", json={"name": "S235", "category": "stal"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "S235"

    def test_list_returns_created(self, client):
        client.post("/api/approved-materials", json={"name": "S355"})
        resp = client.get("/api/approved-materials")
        assert any(m["name"] == "S355" for m in resp.json())

    def test_duplicate_rejected(self, client):
        client.post("/api/approved-materials", json={"name": "S235"})
        resp = client.post("/api/approved-materials", json={"name": "S235"})
        assert resp.status_code == 409

    def test_soft_delete(self, client):
        r = client.post("/api/approved-materials", json={"name": "TestMat"}).json()
        client.delete(f"/api/approved-materials/{r['id']}")
        names = [m["name"] for m in client.get("/api/approved-materials").json()]
        assert "TestMat" not in names


class TestExport:
    def test_xlsx_export_returns_file(self, client, db_session):
        """GET /api/export/xlsx returns an Excel file with correct content-type."""
        create_order(client)
        resp = client.get("/api/export/xlsx")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["content-type"]
        assert len(resp.content) > 0

    def test_xlsx_export_empty_db(self, client):
        """Export works even with no orders."""
        resp = client.get("/api/export/xlsx")
        assert resp.status_code == 200


# ─── BENCHMARKS ────────────────────────────────────────────────────────────────

class TestBenchmarks:
    def test_benchmark_empty_returns_warning(self, client):
        """GET /benchmarks/price-per-kg with no records returns warning."""
        resp = client.get("/api/benchmarks/price-per-kg?material=unknown&order_type=remont")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["warning"] is not None
        assert "Brak danych" in data["warning"] or "Недостаточно" in data["warning"]

    def test_benchmark_confirms_record_price_history(self, client, db_session):
        """POST /confirm saves PriceHistory snapshot with PLN/kg calculation."""
        # Utwórz zlecenie + wycenę
        seed_setting(db_session)
        order_id = create_order(client, material="S235", has_drawing=False).json()["id"]
        client.post(f"/api/orders/{order_id}/triage")
        quote_payload = {
            "material_weight_kg": 20.0,
            "material_price_per_kg": 5.0,
            "processes": [{"name": "Cięcie", "hours": 2.0, "rate_per_hour": 50.0}],
            "overhead_pct": 0.10,
            "margin_pct": 0.25,
        }
        client.post(f"/api/orders/{order_id}/quote/structured", json=quote_payload)

        # Potwierdź zlecenie → powinno zapisać PriceHistory
        resp = client.post(f"/api/orders/{order_id}/confirm")
        assert resp.status_code == 200

        # Sprawdź PriceHistory — powinny być dane z wyceny
        from sqlalchemy import text
        result = db_session.execute(text("SELECT parameters_json FROM price_history LIMIT 1"))
        row = result.fetchone()
        assert row is not None, "PriceHistory should have been recorded"

    def test_benchmark_aggregates_by_material(self, client, db_session):
        """GET /benchmarks/price-per-kg aggregates multiple quotes by material."""
        from models import PriceHistory
        from datetime import date

        # Wstaw ręcznie kilka próbek w PriceHistory
        for i in range(3):
            h = PriceHistory(
                order_type="remont",
                total_price_historical=1000.0 + (i * 100),
                parameters_json={"weight_kg": 10.0, "pln_kg": 100.0 + i, "material": "S235"},
                order_date=date.today(),
                client="Client A",
            )
            db_session.add(h)
        db_session.commit()

        # Zapytaj benchmark dla S235
        resp = client.get("/api/benchmarks/price-per-kg?material=S235")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert data["warning"] is None  # ≥3 próbek
        # avg powinno być ~100 (średnia 100, 101, 102)
        assert 100 <= data["avg_pln_kg"] <= 102
        assert data["min_pln_kg"] == 100
        assert data["max_pln_kg"] == 102
