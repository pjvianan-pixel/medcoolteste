"""PDF generation for medical documents (F5 Part 2).

Produces a simple, human-readable PDF for prescriptions and exam requests.
The layout is intentionally minimal; it is designed to be compatible with
future ICP-Brasil / CFM electronic-prescription requirements by keeping
all data points (professional info, patient info, consult metadata, items)
clearly separated in the generated file.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.db.models.medical_document import DocumentType, MedicalDocument


# ── Style helpers ──────────────────────────────────────────────────────────────

_STYLES = getSampleStyleSheet()

_TITLE_STYLE = ParagraphStyle(
    "DocTitle",
    parent=_STYLES["Heading1"],
    fontSize=16,
    alignment=TA_CENTER,
    spaceAfter=4,
)
_SUBTITLE_STYLE = ParagraphStyle(
    "DocSubtitle",
    parent=_STYLES["Normal"],
    fontSize=10,
    alignment=TA_CENTER,
    textColor=colors.HexColor("#555555"),
    spaceAfter=12,
)
_SECTION_STYLE = ParagraphStyle(
    "Section",
    parent=_STYLES["Heading3"],
    fontSize=11,
    textColor=colors.HexColor("#1a1a2e"),
    spaceBefore=10,
    spaceAfter=4,
)
_BODY_STYLE = ParagraphStyle(
    "Body",
    parent=_STYLES["Normal"],
    fontSize=10,
    alignment=TA_LEFT,
    spaceAfter=2,
)
_LABEL_STYLE = ParagraphStyle(
    "Label",
    parent=_STYLES["Normal"],
    fontSize=9,
    textColor=colors.HexColor("#777777"),
    spaceAfter=1,
)
_FOOTER_STYLE = ParagraphStyle(
    "Footer",
    parent=_STYLES["Normal"],
    fontSize=9,
    alignment=TA_CENTER,
    textColor=colors.HexColor("#555555"),
)


def _hr() -> HRFlowable:
    return HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=6)


# ── Public API ─────────────────────────────────────────────────────────────────


def generate_medical_document_pdf(
    *,
    document: MedicalDocument,
    professional_name: str,
    professional_crm: str,
    professional_specialty: str,
    patient_name: str,
    patient_cpf: str | None,
    patient_dob: str | None,
    consult_date: str,
    signed_at: datetime,
) -> bytes:
    """Render a PDF for *document* and return the raw bytes.

    All caller-supplied strings are treated as plain text and are **not**
    interpreted as HTML / ReportLab markup, preventing any injection.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    story: list[Any] = []

    # ── Document title ─────────────────────────────────────────────────────
    if document.document_type == DocumentType.PRESCRIPTION:
        doc_title = "RECEITA MÉDICA"
    else:
        label = document.subtype.value if document.subtype else "EXAME"
        doc_title = f"PEDIDO DE EXAME – {label}"

    story.append(Paragraph(doc_title, _TITLE_STYLE))
    story.append(Paragraph("Teleconsulta Médica", _SUBTITLE_STYLE))
    story.append(_hr())

    # ── Professional information ────────────────────────────────────────────
    story.append(Paragraph("PROFISSIONAL", _SECTION_STYLE))
    pro_data = [
        ["Nome:", _esc(professional_name)],
        ["CRM:", _esc(professional_crm)],
        ["Especialidade:", _esc(professional_specialty)],
    ]
    story.append(_info_table(pro_data))

    story.append(Spacer(1, 6))
    story.append(_hr())

    # ── Patient information ────────────────────────────────────────────────
    story.append(Paragraph("PACIENTE", _SECTION_STYLE))
    pat_data = [["Nome:", _esc(patient_name)]]
    if patient_cpf:
        pat_data.append(["CPF:", _esc(patient_cpf)])
    if patient_dob:
        pat_data.append(["Data de nascimento:", _esc(patient_dob)])
    story.append(_info_table(pat_data))

    story.append(Spacer(1, 6))
    story.append(_hr())

    # ── Consultation information ───────────────────────────────────────────
    story.append(Paragraph("CONSULTA", _SECTION_STYLE))
    consult_data = [
        ["Data/hora da teleconsulta:", _esc(consult_date)],
        ["ID da consulta:", _esc(str(document.consult_request_id))],
    ]
    story.append(_info_table(consult_data))

    story.append(Spacer(1, 6))
    story.append(_hr())

    # ── Document content ───────────────────────────────────────────────────
    if document.document_type == DocumentType.PRESCRIPTION:
        story.extend(_render_prescription_items(document.content_json or []))
    else:
        story.extend(_render_exam_items(document.content_json or []))

    story.append(Spacer(1, 10))
    story.append(_hr())

    # ── Signature block ────────────────────────────────────────────────────
    story.append(Paragraph("ASSINATURA ELETRÔNICA SIMPLES", _SECTION_STYLE))
    sig_text = (
        f"Documento assinado eletronicamente por <b>{_esc(professional_name)}</b>"
        f" (CRM {_esc(professional_crm)}) em {signed_at.strftime('%d/%m/%Y às %H:%M:%S')} UTC."
    )
    story.append(Paragraph(sig_text, _BODY_STYLE))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "Este documento é válido como assinatura eletrônica simples. "
            "Para fins de prescrição eletrônica com validade ICP-Brasil / CFM, "
            "uma assinatura digital certificada é necessária.",
            _FOOTER_STYLE,
        )
    )

    doc.build(story)
    return buf.getvalue()


# ── Internal helpers ───────────────────────────────────────────────────────────


def _esc(text: str) -> str:
    """Escape special ReportLab XML characters in plain text."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _info_table(rows: list[list[str]]) -> Table:
    """Build a two-column label/value table."""
    t = Table(rows, colWidths=[5 * cm, None], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def _render_prescription_items(items: list[dict]) -> list[Any]:
    """Render prescription medication items."""
    story: list[Any] = [Paragraph("PRESCRIÇÃO", _SECTION_STYLE)]
    for i, item in enumerate(items, start=1):
        drug = _esc(item.get("drug_name", ""))
        dosage = _esc(item.get("dosage", ""))
        instructions = _esc(item.get("instructions", ""))
        duration = item.get("duration_days")

        story.append(Paragraph(f"<b>{i}. {drug} – {dosage}</b>", _BODY_STYLE))
        story.append(Paragraph(instructions, _BODY_STYLE))
        if duration:
            story.append(Paragraph(f"Duração: {duration} dia(s)", _LABEL_STYLE))
        story.append(Spacer(1, 4))
    return story


def _render_exam_items(items: list[dict]) -> list[Any]:
    """Render exam request items."""
    story: list[Any] = [Paragraph("EXAMES SOLICITADOS", _SECTION_STYLE)]
    for i, item in enumerate(items, start=1):
        exam = _esc(item.get("exam_name", ""))
        exam_type = _esc(item.get("type", ""))
        notes = item.get("notes")

        story.append(Paragraph(f"<b>{i}. {exam}</b> ({exam_type})", _BODY_STYLE))
        if notes:
            story.append(Paragraph(_esc(notes), _LABEL_STYLE))
        story.append(Spacer(1, 4))
    return story
