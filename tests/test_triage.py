"""
Unit tests for the Triage Engine (triage.py).

No real database — all DB queries are replaced with MagicMock.
This means tests are fast and run without seed.py or any SQLite file.

Coverage:
- ODRZUT: reject rule matches → immediate rejection
- ODRZUT: warn rule matches → warning collected but order proceeds
- ODRZUT: rule field missing on order → rule skipped safely
- STANDARD (catalog): order_type="catalog" + valid template_id → standard
- STANDARD (catalog): order_type="catalog" + no template → standard with unknown name
- STANDARD (drawing): has_drawing=True + SOP name matches template → standard
- STANDARD (drawing): has_drawing=True + category fallback matches → standard
- NIESTANDARD: has_drawing=False, no catalog → niestandard (brak rysunku)
- NIESTANDARD: has_drawing=True but no template matches → niestandard (brak szablonu)
- OPERATORS: eq, in, lt, gt — all branches of _matches_rule
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from unittest.mock import MagicMock, patch
import pytest

from triage import run_triage, TriageInput, TriageResult
from models import ConstraintRule, ProductTemplate, ApprovedMaterial


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_rule(rule_name, field, operator, value, action="reject", message=None):
    """Build a ConstraintRule ORM object without a real DB."""
    r = ConstraintRule()
    r.rule_name = rule_name
    r.field = field
    r.operator = operator
    r.value = value
    r.action = action
    r.message = message or f"Odrzut: {rule_name}"
    r.is_active = True
    return r


def make_template(id, name, category="remont"):
    """Build a ProductTemplate ORM object without a real DB."""
    t = ProductTemplate()
    t.id = id
    t.name = name
    t.category = category
    t.is_active = True
    return t


def make_approved_material(name, category="stal"):
    m = ApprovedMaterial()
    m.name = name
    m.category = category
    m.is_active = True
    return m


def make_db(rules=None, templates=None, approved_materials=None):
    """Return a mock Session that returns the given rules, templates, and approved_materials."""
    db = MagicMock()

    rules = rules or []
    templates = templates or []
    # None = table is empty (no whitelist enforced); [] = table exists but empty (warns on any material)
    _approved = approved_materials  # keep None sentinel

    def query_side_effect(model):
        mock_query = MagicMock()
        if model is ConstraintRule:
            mock_query.filter.return_value.all.return_value = rules
        elif model is ProductTemplate:
            inner = MagicMock()
            inner.first.return_value = templates[0] if templates else None
            mock_query.filter.return_value = inner
            mock_query.filter.return_value.filter.return_value = inner
        elif model is ApprovedMaterial:
            inner = MagicMock()
            inner.all.return_value = _approved if _approved is not None else []
            mock_query.filter.return_value = inner
        return mock_query

    db.query.side_effect = query_side_effect
    return db


def base_order(**kwargs):
    """Sensible defaults for TriageInput so tests only set what they care about."""
    defaults = dict(
        client="Test Klient",
        material="S355",
        deadline_days=14,
        has_drawing=True,
        order_type="remont",
        sop_name=None,
        template_id=None,
        estimated_value=1000.0,
    )
    defaults.update(kwargs)
    return TriageInput(**defaults)


# ─── ODRZUT tests ─────────────────────────────────────────────────────────────

class TestOdrzut:
    def test_reject_rule_blocks_order(self):
        """A matching reject rule must return branch=odrzut immediately."""
        rule = make_rule("Aluminium", "material", "eq", "aluminium", action="reject")
        order = base_order(material="aluminium")
        result = run_triage(order, make_db(rules=[rule]))
        assert result.branch == "odrzut"
        assert result.rule_name == "Aluminium"

    def test_reject_rule_case_insensitive(self):
        """eq operator must be case-insensitive (Aluminium vs ALUMINIUM)."""
        rule = make_rule("Aluminium", "material", "eq", "aluminium", action="reject")
        order = base_order(material="ALUMINIUM")
        result = run_triage(order, make_db(rules=[rule]))
        assert result.branch == "odrzut"

    def test_reject_rule_in_operator(self):
        """'in' operator should reject when material is in the comma-separated list."""
        rule = make_rule("Egzotyczne", "material", "in", "nierdzewka,aluminium,tytan")
        order = base_order(material="tytan")
        result = run_triage(order, make_db(rules=[rule]))
        assert result.branch == "odrzut"

    def test_reject_rule_lt_operator(self):
        """'lt' operator should reject when deadline_days is below threshold."""
        rule = make_rule("Za krótki termin", "deadline_days", "lt", "3")
        order = base_order(deadline_days=2)
        result = run_triage(order, make_db(rules=[rule]))
        assert result.branch == "odrzut"

    def test_reject_rule_gt_operator(self):
        """'gt' operator should reject when estimated_value exceeds threshold."""
        rule = make_rule("Zbyt duże zlecenie", "estimated_value", "gt", "50000")
        order = base_order(estimated_value=99000.0)
        result = run_triage(order, make_db(rules=[rule]))
        assert result.branch == "odrzut"

    def test_non_matching_rule_passes(self):
        """An order that doesn't match any rule should NOT be rejected."""
        rule = make_rule("Aluminium", "material", "eq", "aluminium")
        order = base_order(material="S355")  # steel — fine
        result = run_triage(order, make_db(rules=[rule]))
        assert result.branch != "odrzut"

    def test_missing_field_skips_rule(self):
        """If the order doesn't have the rule's field, the rule is silently skipped."""
        rule = make_rule("Ghost rule", "nonexistent_field", "eq", "whatever")
        order = base_order()
        result = run_triage(order, make_db(rules=[rule]))
        assert result.branch != "odrzut"

    def test_warn_rule_does_not_block(self):
        """action='warn' collects a warning but lets the order proceed."""
        rule = make_rule(
            "Materiał trudny", "material", "eq", "S690",
            action="warn", message="Materiał trudny — sprawdź dostępność"
        )
        order = base_order(material="S690", has_drawing=False)
        result = run_triage(order, make_db(rules=[rule]))
        assert result.branch != "odrzut"
        # triage appends rule.message (not rule_name) to warnings
        assert len(result.warnings) == 1

    def test_warn_rule_message_in_warnings(self):
        """Warning message from warn rule should appear in result.warnings."""
        rule = make_rule("W", "material", "eq", "S690", action="warn", message="Uwaga: S690")
        order = base_order(material="S690", has_drawing=False)
        result = run_triage(order, make_db(rules=[rule]))
        assert "Uwaga: S690" in result.warnings


# ─── STANDARD tests ───────────────────────────────────────────────────────────

class TestStandard:
    def test_catalog_order_always_standard(self):
        """order_type='catalog' skips drawing check and goes straight to standard."""
        tmpl = make_template(id=5, name="Wymiana zęba")
        order = base_order(order_type="catalog", template_id=5, has_drawing=False)
        result = run_triage(order, make_db(templates=[tmpl]))
        assert result.branch == "standard"
        assert result.template_id == 5

    def test_catalog_unknown_template_still_standard(self):
        """Catalog order with no matching template → still standard, unknown name."""
        order = base_order(order_type="catalog", template_id=None)
        result = run_triage(order, make_db(templates=[]))
        assert result.branch == "standard"
        assert result.template_id is None
        assert "nieznany" in result.message

    def test_drawing_plus_sop_name_is_standard(self):
        """has_drawing=True + sop_name matches a template → standard branch."""
        tmpl = make_template(id=3, name="Wymiana zęba w łyżce")
        order = base_order(has_drawing=True, sop_name="Wymiana zęba", order_type="remont")
        result = run_triage(order, make_db(templates=[tmpl]))
        assert result.branch == "standard"
        assert result.template_id == 3

    def test_drawing_plus_category_fallback_is_standard(self):
        """has_drawing=True, no sop_name, but category matches a template → standard."""
        tmpl = make_template(id=7, name="Remont ogólny", category="remont")
        order = base_order(has_drawing=True, sop_name=None, order_type="remont")
        result = run_triage(order, make_db(templates=[tmpl]))
        assert result.branch == "standard"


# ─── NIESTANDARD tests ────────────────────────────────────────────────────────

class TestNiestandard:
    def test_no_drawing_no_catalog_is_niestandard(self):
        """No drawing, not a catalog order → niestandard (brak rysunku)."""
        order = base_order(has_drawing=False, order_type="remont")
        result = run_triage(order, make_db())
        assert result.branch == "niestandard"
        assert "brak rysunku" in result.message

    def test_drawing_but_no_template_is_niestandard(self):
        """has_drawing=True but no matching template in DB → niestandard."""
        order = base_order(has_drawing=True, sop_name="Coś unikatowego", order_type="nowa_czesc")
        result = run_triage(order, make_db(templates=[]))  # empty template list
        assert result.branch == "niestandard"
        assert "brak pasującego szablonu" in result.message

    def test_niestandard_has_no_template_id(self):
        """Niestandard result must not carry a template_id."""
        order = base_order(has_drawing=False)
        result = run_triage(order, make_db())
        assert result.template_id is None


# ─── Priority: odrzut before standard ────────────────────────────────────────

class TestPriority:
    def test_reject_beats_catalog_match(self):
        """Even a catalog order should be rejected if it hits a hard reject rule."""
        rule = make_rule("Aluminium", "material", "eq", "aluminium", action="reject")
        tmpl = make_template(id=1, name="Produkt aluminium")
        order = base_order(
            material="aluminium",
            order_type="catalog",
            template_id=1,
        )
        result = run_triage(order, make_db(rules=[rule], templates=[tmpl]))
        assert result.branch == "odrzut"

    def test_multiple_rules_first_reject_wins(self):
        """First matching reject rule stops evaluation — second rule irrelevant."""
        rule1 = make_rule("R1", "material", "eq", "aluminium", message="Powód 1")
        rule2 = make_rule("R2", "material", "eq", "aluminium", message="Powód 2")
        order = base_order(material="aluminium", has_drawing=False)
        result = run_triage(order, make_db(rules=[rule1, rule2]))
        assert result.branch == "odrzut"
        assert result.message == "Powód 1"  # first rule wins


# ─── MATERIAL WHITELIST tests ─────────────────────────────────────────────────

class TestMaterialWhitelist:
    def test_approved_material_no_warn(self):
        """Material present in whitelist — no warning generated."""
        approved = [make_approved_material("S235"), make_approved_material("S355")]
        order = base_order(material="S235", has_drawing=False)
        result = run_triage(order, make_db(approved_materials=approved))
        mat_warnings = [w for w in (result.warnings or []) if "materiał" in w.lower() or "material" in w.lower()]
        assert mat_warnings == []

    def test_unknown_material_adds_warning(self):
        """Material not in whitelist → warning added, order not rejected."""
        approved = [make_approved_material("S235"), make_approved_material("S355")]
        order = base_order(material="mithril-99", has_drawing=False)
        result = run_triage(order, make_db(approved_materials=approved))
        assert result.branch != "odrzut"
        assert any("mithril-99" in w for w in (result.warnings or []))

    def test_empty_whitelist_no_warn(self):
        """If approved_materials table is empty — no warning (feature not configured)."""
        order = base_order(material="cokolwiek", has_drawing=False)
        result = run_triage(order, make_db(approved_materials=[]))
        mat_warnings = [w for w in (result.warnings or []) if "materiał" in w.lower()]
        assert mat_warnings == []

    def test_material_whitelist_case_insensitive(self):
        """Whitelist check is case-insensitive."""
        approved = [make_approved_material("S235")]
        order = base_order(material="s235", has_drawing=False)
        result = run_triage(order, make_db(approved_materials=approved))
        mat_warnings = [w for w in (result.warnings or []) if "s235" in w.lower()]
        assert mat_warnings == []
