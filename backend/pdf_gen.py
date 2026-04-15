"""
PDF Generator — Arkusz Zlecenia Wewnętrznego
Zastępuje ręczne Excele CNC/Montaż dla operatorów.

Używa Jinja2 (renderuje HTML) → WeasyPrint (konwertuje na PDF).
Jeśli WeasyPrint nie jest zainstalowany, zwraca HTML jako fallback.
"""
import os
from datetime import date
from typing import Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Ścieżka do folderu z szablonami Jinja2
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


def _render_html(order, template, quote) -> str:
    """Renderuje szablon Jinja2 do stringa HTML."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("arkusz.html")
    return tmpl.render(
        order=order,
        sop_template=template,   # ProductTemplate (może być None dla Niestandard)
        quote=quote,
        today=date.today().strftime("%d.%m.%Y"),
    )


def generate_arkusz_pdf(order, template, quote=None) -> bytes:
    """
    Główna funkcja — generuje PDF Arkusza Zlecenia.
    Fallback na HTML jeśli WeasyPrint nie jest zainstalowany (np. dev bez GTK).
    """
    html_str = _render_html(order, template, quote)

    try:
        from weasyprint import HTML
        return HTML(string=html_str, base_url=TEMPLATES_DIR).write_pdf()
    except (ImportError, OSError):
        # ImportError: weasyprint nie zainstalowany.
        # OSError: weasyprint zainstalowany ale brak GTK/gobject na Windows.
        # Fallback: zwróć HTML — przeglądarka otworzy go normalnie.
        return html_str.encode("utf-8")


def generate_oferta_pdf(order, template, quote=None) -> bytes:
    """
    Generuje PDF Oferty Handlowej dla klienta.
    Zawiera: cena netto/brutto, termin, dane klienta.
    NIE zawiera: kroków SOP, maszyn, stawek robocizny — to tajemnica handlowa.
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("oferta.html")
    html_str = tmpl.render(
        order=order,
        sop_template=template,
        quote=quote,
        today=date.today().strftime("%d.%m.%Y"),
    )
    try:
        from weasyprint import HTML
        return HTML(string=html_str, base_url=TEMPLATES_DIR).write_pdf()
    except (ImportError, OSError):
        return html_str.encode("utf-8")


def get_content_type(order) -> str:
    """Zwraca poprawny Content-Type w zależności od dostępności WeasyPrint."""
    try:
        from weasyprint import HTML  # noqa: F401
        return "application/pdf"
    except (ImportError, OSError):
        return "text/html; charset=utf-8"
