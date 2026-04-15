"""
SQLAlchemy models — Single Source of Truth dla schematu bazy.
Używamy SQLite dla MVP. Wszystkie JSONB → JSON w SQLite.
"""
import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Numeric,
    DateTime, Date, Enum, ForeignKey, JSON, create_engine
)
from sqlalchemy.orm import DeclarativeBase, relationship

# --- Typy wyliczeniowe (Enums) ---

class OrderStatus(str, enum.Enum):
    draft         = "draft"
    triage        = "triage"
    standard      = "standard"
    niestandard   = "niestandard"
    quoted        = "quoted"         # Technolog zapisał wycenę, czeka na akceptację Biura
    rejected      = "rejected"
    in_production = "in_production"  # Biuro zatwierdziło — czeka na start
    w_trakcie     = "w_trakcie"      # Technolog rozpoczął pracę
    gotowe        = "gotowe"         # Gotowe do odbioru przez klienta
    wydane        = "wydane"         # Wydane klientowi — zamknięte
    done          = "done"           # Zachowane dla wstecznej kompatybilności

class TriageBranch(str, enum.Enum):
    odrzut      = "odrzut"
    standard    = "standard"
    niestandard = "niestandard"

class UserRole(str, enum.Enum):
    biuro     = "biuro"
    technolog = "technolog"
    dyrektor  = "dyrektor"

class Priority(str, enum.Enum):
    low    = "low"
    normal = "normal"
    high   = "high"
    urgent = "urgent"

class DocType(str, enum.Enum):
    przyjecie = "przyjęcie"  # Dokument Przyjęcia (wpływ materiału)
    rozchod   = "rozchód"    # Dokument Rozchodowy (wydanie materiału)

class ParamRequestStatus(str, enum.Enum):
    pending  = "pending"   # Technolog czeka na odpowiedź
    answered = "answered"  # Biuro odpowiedziało


# --- Baza deklaratywna ---

class Base(DeclarativeBase):
    pass


# =============================================================================
# 1. USERS
# =============================================================================
class User(Base):
    __tablename__ = "users"

    id   = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    pin  = Column(String(10))   # prosty PIN dla MVP, bez bcrypt na razie


# =============================================================================
# 2. ZLECENIE WEWNĘTRZNE (Dokument 1 z kartki)
#    Pola: Klient, Operacje, Osoby odpowiedzialne, Termin (graniczny)
# =============================================================================
class Order(Base):
    __tablename__ = "orders"

    id            = Column(Integer, primary_key=True)
    order_number  = Column(String(20), unique=True)   # np. "22/2026"
    client        = Column(String(200), nullable=False)
    status        = Column(Enum(OrderStatus), default=OrderStatus.draft, nullable=False)
    triage_branch = Column(Enum(TriageBranch))         # wynik Triage Engine
    deadline      = Column(Date)                       # Termin wykonania — GRANICZNY
    notes            = Column(Text)
    has_drawing      = Column(Boolean, default=False)  # Gotowy projekt? — kluczowy dla triage
    material         = Column(String(100))             # Materiał (sprawdzany w Odrzut)
    purpose          = Column(Text)                    # Przeznaczenie / Do czego to? (Nowa część)
    estimated_value  = Column(Numeric(10, 2), default=0)
    order_type       = Column(String(20), default="remont")  # remont | catalog | nowa_czesc
    sop_name         = Column(String(200))             # wybrana SOP (np. "Wymiana zęba w łyżce")
    description      = Column(Text)                    # Opis problemu (remont/nowa_czesc)
    requires_visit   = Column(Boolean, default=False)  # Wymaga wizyty u klienta
    template_id      = Column(Integer, ForeignKey("product_templates.id"))  # Wybrany produkt (catalog)
    quantity         = Column(Integer, default=1)      # Ilość sztuk (catalog)
    is_defence       = Column(Boolean, default=False)  # Projekt zbrojeniowy / MON — czerwona ikona w liście
    created_at    = Column(DateTime, default=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("users.id"))
    assigned_to_id = Column(Integer, ForeignKey("users.id"))  # Technolog przy niestandard

    # Relacje
    created_by  = relationship("User", foreign_keys=[created_by_id])
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
    operations  = relationship("OrderOperation", back_populates="order", cascade="all, delete")
    quote       = relationship("Quote", back_populates="order", uselist=False)
    material_request = relationship("MaterialRequest", back_populates="order", uselist=False)
    quality_cards    = relationship("QualityCard", back_populates="order", cascade="all, delete")
    param_requests   = relationship("ParameterRequest", back_populates="order", cascade="all, delete")
    attachments      = relationship("OrderAttachment", back_populates="order", cascade="all, delete")


# =============================================================================
# 3. OPERACJE DO WYKONANIA (część Zlecenia Wewnętrznego)
# =============================================================================
class OrderOperation(Base):
    __tablename__ = "order_operations"

    id          = Column(Integer, primary_key=True)
    order_id    = Column(Integer, ForeignKey("orders.id"), nullable=False)
    operation   = Column(String(100), nullable=False)  # "SPAWANIE", "CNC", "LASER"
    responsible = Column(String(100))                  # Osoba odpowiedzialna
    sequence    = Column(Integer, default=1)           # Kolejność wykonania
    status      = Column(String(20), default="pending")  # pending/in_progress/done
    tech_card   = relationship("TechCard", back_populates="operation", uselist=False)

    order = relationship("Order", back_populates="operations")


# =============================================================================
# 4. KARTA TECHNOLOGICZNA (Dokument 2 z kartki)
#    Dokumentacja wykonawcza dla poszczególnych operacji
# =============================================================================
class TechCard(Base):
    __tablename__ = "tech_cards"

    id           = Column(Integer, primary_key=True)
    operation_id = Column(Integer, ForeignKey("order_operations.id"), nullable=False)
    template_id  = Column(Integer, ForeignKey("product_templates.id"))  # NULL jeśli niestandard

    # instruction_blocks: [{order: 1, text: "Oczyścić powierzchnię"}, ...]
    # Technolog klika bloki, nie pisze tekstu ręcznie
    instruction_blocks = Column(JSON, default=list)

    # machines: [{machine: "PIŁA", checked: True}, ...]
    machines     = Column(JSON, default=list)
    created_by_id = Column(Integer, ForeignKey("users.id"))

    operation    = relationship("OrderOperation", back_populates="tech_card")
    created_by   = relationship("User")


# =============================================================================
# 5. KARTA KONTROLNA WYROBU (Dokument 3 z kartki)
#    WAŻNE: Karta po KAŻDYM etapie, nie tylko na końcu!
#    check_welds USUNIĘTE — przekreślone na kartce
# =============================================================================
class QualityCard(Base):
    __tablename__ = "quality_cards"

    id           = Column(Integer, primary_key=True)
    order_id     = Column(Integer, ForeignKey("orders.id"), nullable=False)
    operation_id = Column(Integer, ForeignKey("order_operations.id"))
    stage_name   = Column(String(100))  # nazwa etapu, np. "Spawanie", "Malowanie"

    # Sprawdzenia (z kartki — bez check_welds, bo przekreślone)
    check_linear   = Column(Boolean, default=False)  # Sprawdzenie wymiarów liniowych
    check_geometric = Column(Boolean, default=False) # Sprawdzenie wymiarów geometrycznych
    check_surface  = Column(Boolean, default=False)  # Sprawdzenie wymogów powierzchni

    passed      = Column(Boolean)
    checked_by  = Column(String(100))
    checked_at  = Column(DateTime)

    order = relationship("Order", back_populates="quality_cards")


# =============================================================================
# 6. ZAPOTRZEBOWANIE MATERIAŁOWE (Dokument 5 z kartki)
#    Pola: Numer zlecenia, Klient, Lista materiałów (rodzaj+ilość), Wymogi, Priorytet
# =============================================================================
class MaterialRequest(Base):
    __tablename__ = "material_requests"

    id       = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    client   = Column(String(200))

    # materials: [{type: "Stal S355", qty: 10, unit: "kg"}, ...]
    materials    = Column(JSON, default=list)
    extra_notes  = Column(Text)
    priority     = Column(Enum(Priority), default=Priority.normal)
    status       = Column(String(20), default="pending")  # pending/sourced/delivered

    order = relationship("Order", back_populates="material_request")


# =============================================================================
# 7. PRODUCT TEMPLATES (Katalog — gałąź Standard)
#    Technolog tworzy szablon raz → Biuro używa automatycznie
# =============================================================================
class ProductTemplate(Base):
    __tablename__ = "product_templates"

    id       = Column(Integer, primary_key=True)
    name     = Column(String(200), nullable=False)  # "Wymiana zęba w łyżce"
    category = Column(String(50))                    # "remont", "zbrojenie", "prefabrykat"

    # operations_json: [{op: "SPAWANIE", hours: 2.0, responsible: "spawacz"}, ...]
    operations_json = Column(JSON, default=list)

    # materials_json: [{mat: "Stal S355", qty_kg: 10, unit: "kg"}, ...]
    materials_json  = Column(JSON, default=list)

    # instruction_blocks: [{order: 1, text: "Oczyścić powierzchnię"}, ...]
    # Bloki SOP — Technolog klika, nie pisze ręcznie
    instruction_blocks = Column(JSON, default=list)

    # machines_json: [{machine: "PIŁA", required: True}, ...]
    machines_json   = Column(JSON, default=list)

    base_price_pln  = Column(Numeric(10, 2))
    margin_pct      = Column(Numeric(5, 4), default=0.25)
    is_active       = Column(Boolean, default=True)


# =============================================================================
# 8. CONSTRAINT RULES (Reguły Odrzutu)
#    Dynamiczne — Technolog/Dyrektor może dodać nową regułę bez zmiany kodu
# =============================================================================
class ConstraintRule(Base):
    __tablename__ = "constraint_rules"

    id        = Column(Integer, primary_key=True)
    rule_name = Column(String(100))           # "Materiał nierdzewny"
    field     = Column(String(50))            # "material", "deadline_days", "order_value"
    operator  = Column(String(10))            # "eq", "lt", "gt", "in"
    value     = Column(String(200))           # "nierdzewka" lub "3" lub "500"
    action    = Column(String(10), default="reject")   # "reject" lub "warn"
    message   = Column(Text)                  # "Nie wykonamy — materiał poza zakresem"
    is_active = Column(Boolean, default=True)


# =============================================================================
# 9. QUOTES (Wycena)
# =============================================================================
class Quote(Base):
    __tablename__ = "quotes"

    id            = Column(Integer, primary_key=True)
    order_id      = Column(Integer, ForeignKey("orders.id"), nullable=False)

    # line_items: [{name, qty, unit_price, total}, ...]
    line_items    = Column(JSON, default=list)
    labor_hours   = Column(Numeric(8, 2))
    material_cost = Column(Numeric(10, 2))
    overhead_pct  = Column(Numeric(5, 4), default=0.10)
    total_net     = Column(Numeric(10, 2))
    margin_pct    = Column(Numeric(5, 4))
    is_zapor      = Column(Boolean, default=False)  # Zaporowa marża (Fuck-Off Price)
    pdf_path      = Column(String(500))
    created_at    = Column(DateTime, default=datetime.utcnow)

    # --- Wycena strukturalna v2 (formuła technologa) ---
    # processes_json: [{name: "Cięcie plazmą", cost: 150.0}, ...]
    processes_json     = Column(JSON, default=list)
    weight_kg          = Column(Numeric(10, 3), default=0)
    weight_rate_pln_kg = Column(Numeric(6, 2), default=15)   # 7–30 PLN/kg zależnie od złożoności
    welding_hours      = Column(Numeric(8, 2), default=0)
    weight_netto_kg    = Column(Numeric(10, 3), default=0)   # informacyjnie — nie wchodzi do ceny
    weight_brutto_kg   = Column(Numeric(10, 3), default=0)   # informacyjnie — nie wchodzi do ceny
    estimate_version   = Column(String(10), default="v1")    # v1=legacy, v2=structured

    order = relationship("Order", back_populates="quote")


# =============================================================================
# 10. ORDER ATTACHMENTS (Załączniki — rysunki, dokumentacja)
#     Technolog może dołączyć PDF/DXF/JPG do zlecenia
# =============================================================================
class OrderAttachment(Base):
    __tablename__ = "order_attachments"

    id          = Column(Integer, primary_key=True)
    order_id    = Column(Integer, ForeignKey("orders.id"), nullable=False)
    filename    = Column(String(255), nullable=False)     # oryginalna nazwa pliku
    stored_path = Column(String(500), nullable=False)     # uploads/{order_id}/{uuid}_{name}
    size_bytes  = Column(Integer)
    mime_type   = Column(String(100))
    uploaded_by = Column(String(50))   # "technolog" / "biuro" — rola z PIN
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="attachments")


# =============================================================================
# 11. PRICE HISTORY (Historia cen — "jak ostatnim razem")
#     Seed: Lista zleceń usługi.xlsx / Lista zleceń usługi_1.xlsx
#     Cel: Claude API może zaproponować cenę na podstawie podobnych historycznych zleceń
# =============================================================================
class PriceHistory(Base):
    __tablename__ = "price_history"

    id                     = Column(Integer, primary_key=True)
    order_type             = Column(String(200))   # "Wymiana zęba w łyżce"
    total_price_historical = Column(Numeric(10, 2))
    # parameters_json: {weight_kg: 12, width_mm: 400, material: "S355"}
    parameters_json        = Column(JSON, default=dict)
    source                 = Column(String(200))   # nazwa pliku xlsx lub "manual"
    order_date             = Column(Date)
    client                 = Column(String(200))


# =============================================================================
# 12. SKŁADOWANIE PÓŁPRODUKTÓW (Magazyn — Dokument Przyjęcia / Rozchodowy)
#     Pola: ile, jakie zlecenie, kiedy, wyrób
# =============================================================================
class StockMovement(Base):
    __tablename__ = "stock_movements"

    id          = Column(Integer, primary_key=True)
    doc_type    = Column(Enum(DocType), nullable=False)  # przyjęcie / rozchód
    order_id    = Column(Integer, ForeignKey("orders.id"))
    item_name   = Column(String(200), nullable=False)    # JAKIE (co)
    qty         = Column(Numeric(10, 3), nullable=False)  # ILE
    unit        = Column(String(20))                      # szt / kg / m
    moved_at    = Column(DateTime, default=datetime.utcnow)  # KIEDY
    product_ref = Column(String(200))                     # WYRÓB (referencja do produktu)


# =============================================================================
# 13. SKŁADOWANIE KOMPONENTÓW MENW. (Pojemniki zbiorcze)
#     Pola: Numer zlecenia MENW, Lista Zbiorcza E, Oznaczenie pojemnika, Rejestr rozchodów
# =============================================================================
class ComponentContainer(Base):
    __tablename__ = "component_containers"

    id              = Column(Integer, primary_key=True)
    order_id        = Column(Integer, ForeignKey("orders.id"), nullable=False)
    lista_zbiorcza  = Column(String(50))   # Lista Zbiorcza E (ID partii)
    container_label = Column(String(200))  # Oznaczenie pojemnika zbiorowego numerem zlecenia
    # dispatch_log: [{to_order_id, qty, date, notes}, ...]
    # Rejestr rozchodów — do jakiego zlecenia poszły komponenty
    dispatch_log    = Column(JSON, default=list)


# =============================================================================
# 14. PARAMETER REQUESTS ("Zapytaj o parametry" — Technolog → Biuro)
#     Technolog nie dzwoni, wysyła pytanie w systemie
# =============================================================================
class ParameterRequest(Base):
    __tablename__ = "parameter_requests"

    id            = Column(Integer, primary_key=True)
    order_id      = Column(Integer, ForeignKey("orders.id"), nullable=False)
    question_text = Column(Text, nullable=False)   # "Proszę podać markę stali i grubość ścianki"
    answer_text   = Column(Text)                   # odpowiedź Biuro
    status        = Column(Enum(ParamRequestStatus), default=ParamRequestStatus.pending)
    asked_at      = Column(DateTime, default=datetime.utcnow)
    answered_at   = Column(DateTime)

    order = relationship("Order", back_populates="param_requests")


# =============================================================================
# 15. SETTINGS (Ustawienia systemowe — bez restartu serwera)
#     Dyrektor zmienia stawki przez API, nie przez plik konfiguracyjny
# =============================================================================
class Setting(Base):
    __tablename__ = "settings"

    key   = Column(String(50), primary_key=True)   # np. "labor_rate_pln"
    value = Column(String(200), nullable=False)     # np. "90"
    label = Column(String(100))                    # opis dla UI: "Stawka robocizny (PLN/h)"


# =============================================================================
# INICJALIZACJA BAZY
# =============================================================================
def init_db(db_url: str = "sqlite:///./rcm_erp.db"):
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine
