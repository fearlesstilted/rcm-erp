"""
Triage Engine — serce systemu.
Wejście: dane zlecenia od Biuro
Wyjście: TriageResult (odrzut / standard / niestandard) + wiadomość

Logika (dokładnie wg schematu z kartki):
  1. ODRZUT   — twarде reguły z bazy (materiał, termin, marża)
  2. STANDARD — jest szablon w katalogu + klient ma gotowy rysunek
  3. NIESTANDARD — wszystko inne → kolejka Technologa
"""
from dataclasses import dataclass
from typing import Optional
from sqlalchemy.orm import Session
from models import ConstraintRule, ProductTemplate


@dataclass
class TriageInput:
    """Dane wejściowe z formularza Biuro (Step-by-Step Wizard)."""
    client: str
    material: str              # np. "nierdzewka", "S355", "żeliwo"
    deadline_days: int         # ile dni do deadline
    has_drawing: bool          # Gotowy projekt? ✓/✗
    order_type: str            # typ z Wizarda: "remont", "catalog", "nowa_czesc"
    sop_name: Optional[str]    # np. "Wymiana zęba" — jeśli order_type == "remont"
    template_id: Optional[int] = None   # ID wybranego produktu (catalog) — przekazany z Order
    estimated_value: float = 0.0


@dataclass
class TriageResult:
    branch: str               # "odrzut" | "standard" | "niestandard"
    message: str              # wiadomość dla Biuro
    template_id: Optional[int] = None   # ID szablonu jeśli branch == "standard"
    rule_name: Optional[str] = None     # nazwa reguły jeśli branch == "odrzut"
    warnings: list = None               # ostrzeżenia (action="warn") — nie blokują zlecenia


def run_triage(order: TriageInput, db: Session) -> TriageResult:
    """
    Główna funkcja Triage Engine.
    Kolejność sprawdzeń jest ważna — Odrzut zawsze sprawdzamy PIERWSZE.
    """

    # ----------------------------------------------------------------
    # KROK 1: ODRZUT — sprawdź twarde reguły z bazy danych
    # action="reject" → twardy odrzut, action="warn" → ostrzeżenie (nie blokuje)
    # ----------------------------------------------------------------
    rules = db.query(ConstraintRule).filter(ConstraintRule.is_active == True).all()
    warnings = []

    for rule in rules:
        if _matches_rule(order, rule):
            if rule.action == "reject":
                return TriageResult(
                    branch="odrzut",
                    message=rule.message or f"Odrzut: {rule.rule_name}",
                    rule_name=rule.rule_name,
                    warnings=warnings,
                )
            else:
                # action="warn" — zapisz ostrzeżenie, ale nie blokuj
                warnings.append(rule.message or rule.rule_name)

    # ----------------------------------------------------------------
    # KROK 2: STANDARD
    # Przypadek A: Biuro wybrało produkt z katalogu (order_type == "catalog")
    #              → zawsze Standard, niezależnie od rysunku
    # Przypadek B: Jest rysunek + pasujący szablon w bazie
    # ----------------------------------------------------------------
    if order.order_type == "catalog":
        # template_id jest polem TriageInput — bez getattr, bez hacków
        template = db.query(ProductTemplate).filter(
            ProductTemplate.id == order.template_id,
            ProductTemplate.is_active == True
        ).first() if order.template_id else None
        name = template.name if template else "nieznany produkt"
        return TriageResult(
            branch="standard",
            message=f"Standard (katalog): '{name}'. Wycena wygenerowana automatycznie.",
            template_id=template.id if template else None,
            warnings=warnings,
        )

    if order.has_drawing:
        template = _find_matching_template(order, db)
        if template:
            return TriageResult(
                branch="standard",
                message=f"Standard: zastosowano szablon '{template.name}'.",
                template_id=template.id,
                warnings=warnings,
            )

    # ----------------------------------------------------------------
    # KROK 3: NIESTANDARD — brak szablonu lub brak rysunku
    # ----------------------------------------------------------------
    reason = "brak rysunku" if not order.has_drawing else "brak pasującego szablonu"
    return TriageResult(
        branch="niestandard",
        message=f"Niestandard ({reason}). Przekazano do Technologa.",
        warnings=warnings,
    )


def _matches_rule(order: TriageInput, rule: ConstraintRule) -> bool:
    """
    Sprawdza czy zlecenie narusza regułę Odrzutu.
    Obsługiwane operatory: eq, in, lt, gt
    """
    # Pobierz wartość pola z obiektu order
    field_value = getattr(order, rule.field, None)
    if field_value is None:
        return False

    op = rule.operator
    rule_val = rule.value

    if op == "eq":
        # Porównanie case-insensitive dla stringów
        return str(field_value).lower() == rule_val.lower()

    elif op == "in":
        # rule.value to lista rozdzielona przecinkiem: "nierdzewka,aluminium,tytan"
        allowed = [v.strip().lower() for v in rule_val.split(",")]
        return str(field_value).lower() in allowed

    elif op == "lt":
        # Pole musi być liczbą (np. deadline_days, estimated_value)
        try:
            return float(field_value) < float(rule_val)
        except (TypeError, ValueError):
            return False

    elif op == "gt":
        try:
            return float(field_value) > float(rule_val)
        except (TypeError, ValueError):
            return False

    return False


def _find_matching_template(order: TriageInput, db: Session) -> Optional[ProductTemplate]:
    """
    Szuka szablonu w katalogu pasującego do zlecenia.
    Proste dopasowanie po nazwie SOP lub kategorii.
    """
    query = db.query(ProductTemplate).filter(ProductTemplate.is_active == True)

    if order.sop_name:
        # Szukaj po nazwie (case-insensitive LIKE)
        tmpl = query.filter(
            ProductTemplate.name.ilike(f"%{order.sop_name}%")
        ).first()
        if tmpl:
            return tmpl

    if order.order_type:
        # Fallback: szukaj po kategorii
        tmpl = query.filter(
            ProductTemplate.category == order.order_type
        ).first()
        if tmpl:
            return tmpl

    return None
