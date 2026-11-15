"""Canonical server-side PDF for finalised Suitability & RPL reports.

The browser renderer remains useful for previews, but issuance must be based on
the frozen report snapshot held by Django.  This renderer deliberately reads
only approved report fields and privacy-safe snapshot data; private CV text and
AI metadata are never rendered.
"""
from __future__ import annotations

from io import BytesIO


def _text(value, fallback=''):
    if value is None:
        return fallback
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item) for item in value if str(item).strip()) or fallback
    return str(value).strip() or fallback


def render_finalised_report(report) -> bytes:
    """Render the immutable six-section report snapshot to PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    snapshot = report.source_snapshot if isinstance(report.source_snapshot, dict) else {}
    learner = snapshot.get('learner') if isinstance(snapshot.get('learner'), dict) else {}
    programme = snapshot.get('programme') if isinstance(snapshot.get('programme'), dict) else {}
    understanding = snapshot.get('programmeUnderstanding') if isinstance(snapshot.get('programmeUnderstanding'), list) else []
    suitability = snapshot.get('programmeSuitability') if isinstance(snapshot.get('programmeSuitability'), dict) else {}
    skills = snapshot.get('skillsRadar') if isinstance(snapshot.get('skillsRadar'), dict) else {}
    training = snapshot.get('trainingPlan') if isinstance(snapshot.get('trainingPlan'), dict) else {}

    purple = colors.HexColor('#210044')
    lavender = colors.HexColor('#f1ebff')
    ink = colors.HexColor('#25213a')
    muted = colors.HexColor('#5f5871')
    styles = getSampleStyleSheet()
    title = ParagraphStyle('report-title', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=19, leading=23, textColor=colors.white, alignment=TA_LEFT, spaceAfter=6)
    section = ParagraphStyle('report-section', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=12, leading=15, textColor=purple, spaceBefore=5, spaceAfter=8)
    heading = ParagraphStyle('report-heading', parent=styles['Heading3'], fontName='Helvetica-Bold', fontSize=9, leading=11, textColor=purple, spaceBefore=4, spaceAfter=3)
    body = ParagraphStyle('report-body', parent=styles['BodyText'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=ink, spaceAfter=5)
    small = ParagraphStyle('report-small', parent=body, fontSize=7.4, leading=9.2, textColor=muted)
    cell = ParagraphStyle('report-cell', parent=body, fontSize=7.2, leading=9, spaceAfter=0)
    cell_bold = ParagraphStyle('report-cell-bold', parent=cell, fontName='Helvetica-Bold', textColor=purple)

    def p(value, style=body):
        escaped = _text(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br/>')
        return Paragraph(escaped, style)

    def header(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(purple)
        canvas.rect(0, A4[1] - 22 * mm, A4[0], 22 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont('Helvetica-Bold', 8)
        canvas.drawString(18 * mm, A4[1] - 9 * mm, 'KENT BUSINESS COLLEGE  |  ASSESSMENT RECORD')
        canvas.setFont('Helvetica', 7)
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f'Page {doc.page}')
        canvas.restoreState()

    def table(rows, widths):
        value = Table(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
        value.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), lavender),
            ('TEXTCOLOR', (0, 0), (-1, 0), purple),
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#c9bce4')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return value

    story = []
    story += [Table([[p('Suitability & Recognition of Prior Learning Report', title)]], colWidths=[174 * mm], style=TableStyle([('BACKGROUND', (0, 0), (-1, -1), purple), ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10)])), Spacer(1, 8)]

    story += [p('Section 01 - Assessment Record', section)]
    detail_rows = [[p('FIELD', cell_bold), p('RECORDED VALUE', cell_bold)]]
    for label, value in (
        ('Learner reference', learner.get('reference') or learner.get('id')),
        ('Employer / organisation', learner.get('employer')),
        ('Job title', learner.get('jobTitle')),
        ('Apprenticeship programme', programme.get('name') or programme.get('id')),
        ('Programme level', programme.get('level')),
        ('Assessment date', learner.get('assessmentDate')),
        ('Report reference', report.report_reference),
    ):
        detail_rows.append([p(label, cell_bold), p(value, cell)])
    story += [table(detail_rows, [55 * mm, 119 * mm]), PageBreak()]

    story += [p('Section 02 - Programme Understanding', section)]
    understanding_rows = [[p('QUESTION', cell_bold), p('RECORDED RESPONSE', cell_bold)]]
    for item in understanding:
        if isinstance(item, dict):
            understanding_rows.append([p(item.get('question')), p(item.get('answer'))])
    story += [table(understanding_rows or [[p('QUESTION', cell_bold), p('No saved response')]], [63 * mm, 111 * mm]), Spacer(1, 8)]

    story += [p('Section 03 - Programme Suitability', section)]
    response_rows = [[p('SAVED SELECTION', cell_bold), p('DETAIL', cell_bold)]]
    for item in suitability.get('responses') or []:
        if isinstance(item, dict):
            selected = item.get('selectedOptions') or item.get('answers') or []
            labels = [option.get('label', option) if isinstance(option, dict) else option for option in selected]
            response_rows.append([p(item.get('group') or 'Programme suitability'), p(labels)])
    story += [table(response_rows or [[p('SAVED SELECTION', cell_bold), p('No saved selection')]], [63 * mm, 111 * mm]), Spacer(1, 7)]
    story += [p('Approved programme suitability justification', heading), p(report.suitability_justification if report.suitability_reviewed_at else 'No approved justification recorded.', body), PageBreak()]

    story += [p('Section 04 - Skills Radar and Supporting Evidence', section)]
    summary_rows = [[p('CATEGORY', cell_bold), p('SCORE', cell_bold), p('ANSWERED', cell_bold)]]
    for item in skills.get('summary') or []:
        if isinstance(item, dict):
            summary_rows.append([p(item.get('label')), p(item.get('score') if item.get('score') is not None else 'N/A'), p(f"{item.get('answered', 0)}/{item.get('total', 0)}")])
    story += [table(summary_rows or [[p('CATEGORY', cell_bold), p('SCORE', cell_bold), p('ANSWERED', cell_bold)]], [92 * mm, 34 * mm, 48 * mm]), Spacer(1, 7), p('This section contains the saved assessment summary. Private extracted CV text and internal AI metadata are excluded from the issued report.', small), PageBreak()]

    relevant = report.relevant_prior_learning is True
    reduction = relevant and report.reduction_justified is True
    decision = ('Relevant Prior Learning - Training Reduction Approved' if reduction else 'Relevant Prior Learning - No Training Reduction Approved' if relevant else 'No Relevant Prior Learning - Full Standard Training Plan' if report.relevant_prior_learning is False else 'RPL decision not recorded')
    story += [p('Section 05 - Recognition of Prior Learning Assessment', section), p('Assessor-recorded decision', heading), p(decision, body), p('Approved RPL assessment justification', heading), p(report.rpl_justification if report.rpl_reviewed_at else 'No approved justification recorded.', body)]
    planned = training.get('plannedHours')
    if not reduction:
        story += [p('Training Plan adjustment', heading), p(f'Full standard Training Plan. Original planned hours: {_text(planned, "Not available")}. No training reduction approved.', body)]
    else:
        story += [p('Training Plan adjustment', heading)]
        rows = [[p('MODULE', cell_bold), p('ORIGINAL HOURS', cell_bold), p('APPROVED REDUCTION', cell_bold), p('ADJUSTED HOURS', cell_bold)]]
        for row in report.reductions.all():
            original = float(row.planned_hours)
            reduced = float(row.reduced_hours)
            rows.append([p(row.module_title), p(f'{original:g}'), p(f'{reduced:g}'), p(f'{original - reduced:g}')])
        story += [table(rows, [72 * mm, 34 * mm, 34 * mm, 34 * mm])]
        story += [p(f'Total planned hours: {_text(planned, "Not available")}. Approved reduction is recorded only for the affected complete modules.', small)]
    story += [PageBreak()]

    story += [p('Section 06 - Declarations and Sign-off', section)]
    story += [p('This issued report is the immutable finalised assessment record. Electronic signatures are recorded separately against this exact issued PDF version.', body)]
    signatures = Table([
        [p('ASSESSOR / ADMIN', cell_bold), p('LEARNER', cell_bold), p('EMPLOYER / LINE MANAGER', cell_bold)],
        [p('Name:\n\nSignature:\n\nDate:'), p('Name:\n\nSignature:\n\nDate:'), p('Name:\n\nSignature:\n\nDate:')],
    ], colWidths=[58 * mm, 58 * mm, 58 * mm], rowHeights=[10 * mm, 38 * mm])
    signatures.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), lavender), ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#c9bce4')), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 5), ('TOPPADDING', (0, 0), (-1, -1), 5)]))
    story += [signatures]

    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=30 * mm, bottomMargin=18 * mm, title='Suitability & Recognition of Prior Learning Report', author='Kent Business College').build(story, onFirstPage=header, onLaterPages=header)
    return output.getvalue()
