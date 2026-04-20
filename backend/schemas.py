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
    order_type:    Optional[str]   = None
    sop_name:      Optional[str]   = None
    template_id:   Optional[int]   = None
    description:   Optional[str]   = None
    purpose:       Optional[str]   = None
    requires_visit: bool           = False
    quantity:      Optional[int]   = None
    estimated_value: Optional[float] = None
    is_defence:    bool            = False
    created_at:    datetime
    model_config = {"from_attributes": True}


class OrderUpdate(BaseModel):
    """Частичное обновление заказа (PATCH). Передаём только изменённые поля."""
    client:          Optional[str]   = None
    deadline:        Optional[date]  = None
    material:        Optional[str]   = None
    has_drawing:     Optional[bool]  = None
    order_type:      Optional[str]   = None
    sop_name:        Optional[str]   = None
    purpose:         Optional[str]   = None
    notes:           Optional[str]   = None
    estimated_value: Optional[float] = None
    description:     Optional[str]   = None
    requires_visit:  Optional[bool]  = None
    quantity:        Optional[int]   = None
    is_defence:      Optional[bool]  = None


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


class ProcessItem(BaseModel):
    """Operacja produkcyjna v3: hours × rate_per_hour. Legacy: cost bezpośredni."""
    name:          str
    hours:         float = 0
    rate_per_hour: float = 0
    cost:          float = 0  # legacy fallback gdy hours/rate nieznane


class QuoteStructuredCreate(BaseModel):
    """
    Strukturalna wycena v3 (cleaner formula).
    Materiał: material_weight_kg × material_price_per_kg.
    Operacje: sum(hours × rate_per_hour), fallback na cost.
    """
    processes:             List[ProcessItem] = Field(default_factory=list)
    material_weight_kg:    float = 0   # kg surowca (np. 50 kg S235)
    material_price_per_kg: float = 0   # PLN/kg surowca (np. 3.50)
    material_cost:         float = 0   # legacy: całkowity koszt materiału
    weight_netto_kg:       float = 0
    weight_brutto_kg:      float = 0
    labor_hours:           float = 0   # dodatkowa robocizna (montaż, wykończenie)
    overhead_pct:          float = 0.10
    margin_pct:            float = 0.25
    transport_cost:        float = 0
    show_unit_prices:      bool  = True
    # Stare pola — backward compat
    weight_kg:             float = 0
    weight_rate_pln_kg:    float = 0


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
    # v2 pola
    processes_json:     Optional[List[Any]] = None
    weight_kg:          Optional[float]     = None
    weight_rate_pln_kg: Optional[float]     = None
    welding_hours:      Optional[float]     = None
    weight_netto_kg:    Optional[float]     = None
    weight_brutto_kg:   Optional[float]     = None
    estimate_version:   Optional[str]       = None
    last_edited_at:     Optional[datetime]  = None
    transport_cost:     Optional[float]     = None
    # v3 pola
    material_weight_kg:    Optional[float] = None
    material_price_per_kg: Optional[float] = None
    show_unit_prices:      bool             = True
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
# ATTACHMENTS — Załączniki (rysunki, dokumentacja)
# =============================================================================
class AttachmentOut(BaseModel):
    id:          int
    order_id:    int
    filename:    str
    stored_path: str
    size_bytes:  Optional[int]  = None
    mime_type:   Optional[str]  = None
    uploaded_by: Optional[str]  = None
    uploaded_at: datetime
    model_config = {"from_attributes": True}


# =============================================================================
# ANALYTICS — Dashboard Dyrektora
# =============================================================================
class RevenueMonth(BaseModel):
    month: str          # "2026-04"
    orders: int
    revenue_pln: float

class TopClient(BaseModel):
    client: str
    orders: int
    revenue_pln: float

class OverdueOrder(BaseModel):
    id: int
    order_number: str
    client: str
    status: str
    deadline: str

class AnalyticsSummary(BaseModel):
    total_orders:     int
    odrzut_count:     int
    odrzut_pct:       float
    standard_count:   int
    niestandard_count: int
    avg_margin_pct:   Optional[float]
    orders_in_production: int
    orders_done:      int
    # Rozszerzone dane dla Dyrektora
    revenue_by_month: List[RevenueMonth] = []
    top_clients:      List[TopClient] = []
    overdue_orders:   List[OverdueOrder] = []


# =============================================================================
# APPROVED MATERIALS — Whitelist materiałów
# =============================================================================
class ApprovedMaterialCreate(BaseModel):
    name:                str
    category:            Optional[str]   = None
    default_rate_pln_kg: Optional[float] = None
    is_active:           bool            = True
    notes:               Optional[str]   = None


class ApprovedMaterialOut(BaseModel):
    id:                  int
    name:                str
    category:            Optional[str]   = None
    default_rate_pln_kg: Optional[float] = None
    is_active:           bool
    notes:               Optional[str]   = None
    model_config = {"from_attributes": True}


# =============================================================================
# BENCHMARK — Analiza cen historycznych (benchmark/cena za kg)
# =============================================================================
class BenchmarkSample(BaseModel):
    """Pojedyncza próbka z historii cen."""
    order_id:    int
    date:        date
    weight_kg:   float
    total_net:   float
    pln_kg:      float


class BenchmarkOut(BaseModel):
    """Benchmark ceny za kg — agregacja historycznych zleceń."""
    avg_pln_kg:  float
    min_pln_kg:  float
    max_pln_kg:  float
    count:       int
    warning:     Optional[str] = None  # "Недостаточно данных" jeśli < 3 próbek
    samples:     List[BenchmarkSample]
