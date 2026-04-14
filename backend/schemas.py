"""
Pydantic schemas — walidacja danych wejście/wyjście API.
Oddzielone od modeli SQLAlchemy celowo (separacja warstw).
"""
from datetime import date, datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field


# =============================================================================
# USERS
# =============================================================================
class UserOut(BaseModel):
    id:   int
    name: str
    role: str
    model_config = {"from_attributes": True}


# =============================================================================
# ORDERS — Zlecenie Wewnętrzne
# =============================================================================
class OrderCreate(BaseModel):
    """Dane wejściowe z Wizarda Biuro (Step 1-3)."""
    client:          str
    deadline:        date
    material:        Optional[str]  = None
    has_drawing:     bool           = False
    order_type:      str            = "remont"   # remont | catalog | nowa_czesc
    sop_name:        Optional[str]  = None
    purpose:         Optional[str]  = None
    notes:           Optional[str]  = None
    estimated_value: float          = 0.0
    description:     Optional[str]  = None        # Opis problemu (remont/nowa_czesc)
    requires_visit:  bool           = False        # Wymaga wizyty u klienta
    template_id:     Optional[int]  = None         # Wybrany produkt z katalogu
    quantity:        int            = 1            # Ilość sztuk (catalog)
    is_defence:      bool           = False        # Projekt zbrojeniowy / MON


class OrderOut(BaseModel):
    id:            int
    order_number:  Optional[str]
    client:        str
    status:        str
    triage_branch: Optional[str]
    deadline:      Optional[date]
    has_drawing:   bool
    material:      Optional[str]
    notes:         Optional[str]
    sop_name:      Optional[str]   = None
    template_id:   Optional[int]   = None
    description:   Optional[str]   = None
    requires_visit: bool           = False
    quantity:      Optional[int]   = None
    estimated_value: Optional[float] = None
    is_defence:    bool            = False
    created_at:    datetime
    model_config = {"from_attributes": True}


# =============================================================================
# TRIAGE
# =============================================================================
class TriageResponse(BaseModel):
    branch:      str   # "odrzut" | "standard" | "niestandard"
    message:     str
    template_id: Optional[int] = None
    rule_name:   Optional[str] = None
    warnings:    List[str] = []   # ostrzeżenia (action="warn") — wyświetlane, nie blokują


# =============================================================================
# QUOTES — Wycena
# =============================================================================
class QuoteCreate(BaseModel):
    """Ręczna wycena przez Technologa (gałąź Niestandard)."""
    labor_hours:   float
    material_cost: float
    overhead_pct:  float = 0.10
    margin_pct:    float = 0.25
    # line_items: [{name, qty, unit_price}, ...]
    line_items:    List[Any] = Field(default_factory=list)


class QuoteZaporCreate(BaseModel):
    """
    Zaporowa marża — Technolog klika jeden przycisk.
    System sam oblicza cenę zaporową (materiał × robocizna × mnożnik 3-4).
    """
    material_cost:   float
    hours_estimate:  float  # przybliżone godziny robocizny


class QuoteOut(BaseModel):
    id:            int
    order_id:      int
    labor_hours:   Optional[float]
    material_cost: Optional[float]
    total_net:     Optional[float]
    margin_pct:    Optional[float]
    is_zapor:      bool
    created_at:    datetime
    model_config = {"from_attributes": True}


# =============================================================================
# MATERIAL REQUESTS — Zapotrzebowanie Materiałowe
# =============================================================================
class MaterialRequestCreate(BaseModel):
    client:      str
    materials:   List[Any] = Field(default_factory=list)  # [{type, qty, unit}]
    extra_notes: Optional[str] = None
    priority:    str = "normal"


class MaterialRequestOut(BaseModel):
    id:       int
    order_id: int
    client:   str
    materials: List[Any]
    priority:  str
    status:    str
    model_config = {"from_attributes": True}


# =============================================================================
# QUALITY CARDS — Karta Kontrolna (po każdym etapie)
# =============================================================================
class QualityCardCreate(BaseModel):
    operation_id:    Optional[int] = None
    stage_name:      str
    check_linear:    bool = False
    check_geometric: bool = False
    check_surface:   bool = False
    passed:          bool
    checked_by:      str


class QualityCardOut(BaseModel):
    id:              int
    order_id:        int
    stage_name:      Optional[str]
    check_linear:    bool
    check_geometric: bool
    check_surface:   bool
    passed:          Optional[bool]
    checked_by:      Optional[str]
    checked_at:      Optional[datetime]
    model_config = {"from_attributes": True}


# =============================================================================
# PARAMETER REQUESTS — "Zapytaj o parametry" (Technolog → Biuro)
# =============================================================================
class ParameterRequestCreate(BaseModel):
    question_text: str   # np. "Proszę podać markę stali i grubość ścianki"


class ParameterRequestAnswer(BaseModel):
    answer_text: str     # odpowiedź Biuro


class ParameterRequestOut(BaseModel):
    id:            int
    order_id:      int
    question_text: str
    answer_text:   Optional[str]
    status:        str
    asked_at:      datetime
    answered_at:   Optional[datetime]
    model_config = {"from_attributes": True}


# =============================================================================
# PRODUCT TEMPLATES — Katalog
# =============================================================================
class TemplateCreate(BaseModel):
    name:               str
    category:           str = "remont"
    operations_json:    List[Any] = Field(default_factory=list)
    materials_json:     List[Any] = Field(default_factory=list)
    instruction_blocks: List[Any] = Field(default_factory=list)
    machines_json:      List[Any] = Field(default_factory=list)
    base_price_pln:     Optional[float] = None
    margin_pct:         float = 0.25


class TemplateOut(BaseModel):
    id:                 int
    name:               str
    category:           str
    operations_json:    List[Any]
    materials_json:     List[Any]
    instruction_blocks: List[Any]
    machines_json:      List[Any]
    base_price_pln:     Optional[float]
    margin_pct:         float
    is_active:          bool
    model_config = {"from_attributes": True}


# =============================================================================
# ANALYTICS — Dashboard Dyrektora
# =============================================================================
class AnalyticsSummary(BaseModel):
    total_orders:     int
    odrzut_count:     int
    odrzut_pct:       float
    standard_count:   int
    niestandard_count: int
    avg_margin_pct:   Optional[float]
    orders_in_production: int
    orders_done:      int
