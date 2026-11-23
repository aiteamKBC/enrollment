"""Canonical Suitability & RPL PDF renderer.

Every live report preview and every newly issued report is rendered here. The
renderer consumes the report's frozen evidence snapshot plus the assessor's
recorded decisions. Historical issued PDF bytes remain in ``RenderHistory``
and are never passed through this renderer again.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import partial
from io import BytesIO
from math import cos, isfinite, pi, sin
from pathlib import Path
from xml.sax.saxutils import escape


SECTION_TITLES = (
    'Learner & Programme Details',
    'Programme Understanding',
    'Programme Suitability Assessment',
    'Skills Radar - Initial Assessment',
    'CV, Qualifications & PLR Evidence',
    'Recognition of Prior Learning Assessment',
    'Final Assessment & Recommendation',
    'Declarations & Signatures',
)


def _text(value, fallback=''):
    if value is None:
        return fallback
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item).strip() for item in value if str(item).strip()) or fallback
    return str(value).strip() or fallback


def _date(value):
    if not value:
        return ''
    if isinstance(value, (datetime, date)):
        parsed = value
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return raw
    return parsed.strftime('%d %B %Y')


def _hours(value, fallback='Not available'):
    if value in (None, ''):
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value, fallback)
    return f'{number:g} hours'


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def _safe_list(value):
    return value if isinstance(value, list) else []


def _radar_score(value, maximum):
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if isfinite(score) and 1 <= score <= maximum else None


def render_report(report) -> bytes:
    """Render one draft or finalised report with the approved branded design."""
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import (
        BaseDocTemplate, CondPageBreak, Frame, KeepTogether, LongTable,
        NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    snapshot = _safe_dict(getattr(report, 'source_snapshot', {}))
    learner = _safe_dict(snapshot.get('learner'))
    programme = _safe_dict(snapshot.get('programme'))
    understanding = _safe_list(snapshot.get('programmeUnderstanding'))
    suitability = _safe_dict(snapshot.get('programmeSuitability'))
    skills = _safe_dict(snapshot.get('skillsRadar'))
    evidence = _safe_dict(snapshot.get('evidence'))
    training = _safe_dict(snapshot.get('trainingPlan'))

    purple = colors.HexColor('#7043A0')
    purple_dark = colors.HexColor('#522A7E')
    purple_line = colors.HexColor('#E1D3EE')
    purple_wash = colors.HexColor('#F5EEFA')
    ink = colors.HexColor('#1D1920')
    muted = colors.HexColor('#534C58')
    white = colors.white
    page_margin = 18 * mm
    content_width = A4[0] - 2 * page_margin

    styles = getSampleStyleSheet()
    title = ParagraphStyle('rpl-title', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=20, leading=23, textColor=purple_dark, alignment=TA_LEFT, spaceAfter=4)
    subtitle = ParagraphStyle('rpl-subtitle', parent=styles['BodyText'], fontName='Helvetica', fontSize=8, leading=11, textColor=muted, spaceAfter=0)
    section_title = ParagraphStyle('rpl-section', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=ink, spaceAfter=0)
    meta = ParagraphStyle('rpl-meta', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=muted, alignment=TA_RIGHT)
    body = ParagraphStyle('rpl-body', parent=styles['BodyText'], fontName='Helvetica', fontSize=8.3, leading=11.2, textColor=ink, spaceAfter=0)
    small = ParagraphStyle('rpl-small', parent=body, fontSize=7.1, leading=9.2, textColor=muted)
    label = ParagraphStyle('rpl-label', parent=body, fontName='Helvetica-Bold', fontSize=7, leading=9, textColor=purple_dark)
    table_head = ParagraphStyle('rpl-table-head', parent=label, fontSize=6.7, leading=8.2)
    signature_title = ParagraphStyle('rpl-signature-title', parent=label, alignment=TA_CENTER)

    def p(value, style=body, fallback=''):
        return Paragraph(escape(_text(value, fallback)).replace('\n', '<br/>'), style)

    def section(number, heading, note):
        number_style = ParagraphStyle('rpl-section-number', parent=label, fontSize=9, leading=11, alignment=TA_CENTER, textColor=ink)
        number_box = Table([[p(f'{number:02d}', number_style)]], colWidths=[9.7 * mm], rowHeights=[8.4 * mm], cornerRadii=[5] * 4, style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), purple_wash), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        return KeepTogether([
            Spacer(1, 5 * mm),
            Table([[number_box, p(heading, section_title), p(note.upper(), meta)]], colWidths=[13 * mm, content_width - 51 * mm, 38 * mm], style=TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ])),
            Spacer(1, 5 * mm if number == 4 else 2.5 * mm),
        ])

    def data_table(headings, rows, widths, *, first_column_bold=False):
        content = [[p(item, table_head) for item in headings]]
        for row in rows:
            content.append([p(value, label if first_column_bold and index == 0 else body, '—') for index, value in enumerate(row)])
        value = LongTable(content, colWidths=widths, repeatRows=1, hAlign='LEFT', splitByRow=1, splitInRow=1)
        commands = [
            ('BACKGROUND', (0, 0), (-1, 0), purple_wash), ('TEXTCOLOR', (0, 0), (-1, 0), purple_dark),
            ('BOX', (0, 0), (-1, -1), 0.45, purple_line), ('INNERGRID', (0, 0), (-1, -1), 0.3, purple_line),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5), ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]
        if len(content) > 1:
            commands.append(('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, colors.HexColor('#FCFAFE')]))
        value.setStyle(TableStyle(commands))
        return value

    def narrative(heading, text, fallback='Not recorded'):
        card = Table([[p(heading.upper(), label)], [p(text, body, fallback)]], colWidths=[174 * mm], repeatRows=1, splitByRow=1, splitInRow=1, style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), purple_wash), ('BOX', (0, 0), (-1, -1), 0.45, purple_line),
            ('LINEBELOW', (0, 0), (-1, 0), 0.35, purple_line), ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        return [card, Spacer(1, 2.5 * mm)]

    def radar_panels(rows, maximum):
        height = 89 * mm
        chart_width = 84 * mm
        table_x = chart_width + 3.5 * mm
        table_width = content_width - table_x
        table_right = content_width - 4 * mm
        drawing = Drawing(content_width, height)

        def text_at(x, y, value, *, font='Helvetica', size=8, color=ink, anchor='start'):
            drawing.add(String(x, y, value, fontName=font, fontSize=size, fillColor=color, textAnchor=anchor))

        # The table background and header share the same rounded top corners.
        drawing.add(Rect(0, 0, chart_width, height, rx=8, ry=8, strokeColor=purple_line, strokeWidth=0.75, fillColor=white))
        drawing.add(Rect(table_x, 0, table_width, height, rx=8, ry=8, strokeColor=purple_line, strokeWidth=0.75, fillColor=white))
        header_height = 11.5 * mm
        drawing.add(Rect(table_x + 0.5, height - header_height, table_width - 1, header_height - 0.5, rx=8, ry=8, strokeColor=None, fillColor=purple_wash))
        drawing.add(Rect(table_x + 0.5, height - header_height, table_width - 1, 9, strokeColor=None, fillColor=purple_wash))
        text_at(4.5 * mm, height - 7.5 * mm, 'SKILLS RADAR PREVIEW', font='Helvetica-Bold', size=7.5)
        text_at(table_x + 4 * mm, height - 7.5 * mm, 'COMPETENCY', font='Helvetica-Bold', size=7.5)
        text_at(table_right, height - 7.5 * mm, 'SCORE', font='Helvetica-Bold', size=7.5, anchor='end')

        scores = [score for _, score in rows]
        axis_count = max(3, len(rows))
        cx, cy, radius = chart_width / 2, height / 2 + mm, 24.5 * mm

        def point(index, fraction):
            angle = pi / 2 - 2 * pi * index / axis_count
            return cx + cos(angle) * radius * fraction, cy + sin(angle) * radius * fraction

        def polygon(points, **style):
            drawing.add(Polygon([coordinate for xy in points for coordinate in xy], **style))

        for index in range(axis_count):
            drawing.add(Line(cx, cy, *point(index, 1), strokeColor=purple_line, strokeWidth=0.65))
        for ring in range(1, 6):
            polygon([point(index, ring / 5) for index in range(axis_count)], fillColor=None, strokeColor=purple_line, strokeWidth=0.65)
        plotted = [point(index, score / maximum) for index, score in enumerate(scores) if score is not None]
        if len(plotted) == axis_count:
            fill = colors.Color(purple.red, purple.green, purple.blue, alpha=0.18)
            polygon(plotted, fillColor=fill, strokeColor=purple_dark, strokeWidth=2)
        else:
            for x, y in plotted:
                drawing.add(Circle(x, y, 2.4, strokeColor=None, fillColor=purple))
        if any(score is not None for score in scores):
            text_at(4.5 * mm, 9 * mm, f'Saved self-assessment scores on a 1-{maximum:g} scale.', size=7.5, color=muted)
        else:
            text_at(4.5 * mm, 9 * mm, 'No saved self-assessment scores are available.', size=7.5, color=muted)

        row_height = 12.55 * mm
        pill_width = 14.5 * mm
        font_size = 8
        label_width = table_width - pill_width - 12 * mm
        for index, (name, score) in enumerate(rows):
            row_top = height - header_height - index * row_height
            row_bottom = row_top - row_height
            if index % 2:
                drawing.add(Rect(table_x + 1.5, row_bottom, table_width - 3, row_height, strokeColor=None, fillColor=colors.HexColor('#F9F5FC')))
            drawing.add(Line(table_x + 3 * mm, row_bottom, table_right, row_bottom, strokeColor=purple_line, strokeWidth=0.3))
            lines = simpleSplit(name, 'Helvetica', font_size, label_width)
            for line_index, line in enumerate(lines[:2]):
                suffix = '…' if line_index == 1 and len(lines) > 2 else ''
                while stringWidth(line + suffix, 'Helvetica', font_size) > label_width:
                    line, suffix = line[:-1], '…'
                text_at(table_x + 4 * mm, row_top - row_height / 2 + (3 if len(lines) > 1 else -2.5) - line_index * 9, line + suffix, size=font_size)
            drawing.add(Rect(table_right - pill_width, row_bottom + (row_height - 8 * mm) / 2, pill_width, 8 * mm, rx=4, ry=4, strokeColor=purple_line, strokeWidth=0.8, fillColor=white))
            text_at(table_right - pill_width / 2, row_top - row_height / 2 - 2.5,
                    f'{score:g}/{maximum:g}' if score is not None else 'N/A', font='Helvetica-Bold', anchor='middle')
        return drawing

    status = _text(getattr(report, 'status', 'draft'), 'draft').upper()
    reference = _text(getattr(report, 'report_reference', ''), 'DRAFT')
    report_version = _text(getattr(report, 'report_version', 1), '1')
    report_revision = _text(getattr(report, 'revision', 0), '0')
    logo_path = Path(__file__).resolve().parents[1] / 'learner_api' / 'assets' / 'kbc-logo.png'

    def page_frame(canvas, document, *, evidence_page=False):
        canvas.saveState()
        width, height = A4
        right = width - page_margin
        canvas.setFillColor(purple)
        canvas.rect(0, height - 3 * mm, width, 3 * mm, fill=1, stroke=0)
        if document.page == 1:
            try:
                canvas.drawImage(ImageReader(str(logo_path)), page_margin, height - 25 * mm, width=38 * mm, height=16 * mm, preserveAspectRatio=True, anchor='sw', mask='auto')
            except Exception:
                canvas.setFont('Helvetica-Bold', 7.5)
                canvas.setFillColor(purple_dark)
                canvas.drawString(page_margin, height - 17 * mm, 'KENT BUSINESS COLLEGE')
        canvas.setFont('Helvetica-Bold', 9.5)
        canvas.setFillColor(ink)
        report_title = 'SUITABILITY & RECOGNITION OF PRIOR LEARNING REPORT'
        if document.page == 1:
            canvas.setFont('Helvetica-Bold', 7.2)
            canvas.drawRightString(right, height - 14 * mm, report_title)
        else:
            canvas.drawString(page_margin, height - 17 * mm, report_title)
        if evidence_page:
            canvas.setFont('Helvetica', 8)
            canvas.setFillColor(muted)
            canvas.drawString(page_margin, height - 26 * mm, 'SKILLS RADAR & AVAILABLE EVIDENCE')
        canvas.setFillColor(purple_wash)
        pill_y = height - (23 if document.page == 1 else 17.5) * mm
        canvas.roundRect(right - 35 * mm, pill_y, 35 * mm, 7 * mm, 3.5 * mm, fill=1, stroke=0)
        canvas.setFillColor(purple_dark)
        canvas.setFont('Helvetica-Bold', 6.7)
        canvas.drawString(right - 31.5 * mm, pill_y + 2.45 * mm, status)
        canvas.setStrokeColor(purple_line)
        canvas.setLineWidth(0.5)
        header_bottom = height - (31.5 if evidence_page else 28) * mm
        canvas.line(page_margin, header_bottom, right, header_bottom)
        canvas.line(page_margin, 13 * mm, right, 13 * mm)
        canvas.setFillColor(muted)
        canvas.setFont('Helvetica', 6.7)
        canvas.drawString(page_margin, 8 * mm, 'Kent Business College | Suitability & RPL assessment report')
        canvas.setFont('Helvetica-Bold', 6.9)
        canvas.drawRightString(right, 8 * mm, f'Page {document.page}')
        canvas.restoreState()

    story = [
        Spacer(1, 3 * mm), p('Suitability & Recognition\nof Prior Learning Report', title),
        p('ENROLMENT SYSTEM  /  LEARNER ASSESSMENT', subtitle), Spacer(1, 2 * mm),
        Table([[p(f'REPORT {reference}', label), p(f'VERSION {report_version}  |  REVISION {report_revision}', label)]], colWidths=[112 * mm, 62 * mm], style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), purple_wash), ('BOX', (0, 0), (-1, -1), 0.45, purple_line),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
        ])),
    ]

    story += [section(1, SECTION_TITLES[0], 'Auto-populated')]
    story += [data_table(('FIELD', 'RECORDED VALUE'), [
        ('Learner name', learner.get('name')), ('Learner reference', learner.get('reference') or learner.get('id')),
        ('Employer / organisation', learner.get('employer')), ('Job title', learner.get('jobTitle')),
        ('Programme', programme.get('name') or programme.get('id')), ('Programme level', programme.get('level')),
        ('Practical start date', _date(programme.get('startDate') or getattr(report, 'practical_start_date', ''))),
        ('Assessment captured', _date(snapshot.get('capturedAt'))),
    ], [53 * mm, 121 * mm], first_column_bold=True)]

    story += [section(2, SECTION_TITLES[1], 'Learner responses')]
    understanding_rows = [(f'{index}. {_text(item.get("question"), "Programme understanding")}', item.get('answer')) for index, item in enumerate(understanding, start=1) if isinstance(item, dict)]
    story += [data_table(('QUESTION', 'RECORDED RESPONSE'), understanding_rows or [('Programme understanding', 'No saved response')], [68 * mm, 106 * mm])]

    story += [section(3, SECTION_TITLES[2], 'Assessment evidence')]
    suitability_rows = []
    for item in _safe_list(suitability.get('responses')):
        if not isinstance(item, dict):
            continue
        selected = item.get('selectedOptions') or item.get('answers') or []
        labels = [(option.get('label') or option.get('id')) if isinstance(option, dict) else option for option in selected]
        suitability_rows.append((item.get('group') or item.get('question') or 'Programme suitability', labels))
    story += [data_table(('GROUP / QUESTION', 'SAVED ANSWERS'), suitability_rows or [('Programme suitability', 'No saved selection')], [68 * mm, 106 * mm]), Spacer(1, 2.5 * mm)]
    story += narrative('Approved programme suitability justification', getattr(report, 'suitability_justification', ''), 'Awaiting assessor approval') if getattr(report, 'suitability_reviewed_at', None) else narrative('Programme suitability justification', '', 'Awaiting assessor approval')

    scale = _radar_score(skills.get('scaleMaximum'), float('inf')) or 9
    summaries = [item for item in _safe_list(skills.get('summary')) if isinstance(item, dict)]
    items = [item for item in _safe_list(skills.get('items')) if isinstance(item, dict)]
    radar_rows = [
        (' - '.join(filter(None, (_text(item.get('code')), _text(item.get('description'))))) or _text(item.get('id'), 'Saved assessment item'),
         _radar_score(item.get('score'), scale))
        for item in items[:6]
    ] if items else [
        (_text(item.get('label'), 'Saved category'), _radar_score(item.get('score'), scale)) for item in summaries[:6]
    ]
    if not radar_rows:
        radar_rows = [(category, None) for category in ('Knowledge', 'Skills', 'Behaviours')]
    story += [NextPageTemplate('Skills Radar'), PageBreak(), section(4, SECTION_TITLES[3], 'Automatic values'), radar_panels(radar_rows, scale), Spacer(1, 3.5 * mm)]
    story += [p('Skills Radar is initial evidence only. It does not independently prove prior learning.', small)]
    if len(items) > len(radar_rows):
        story += [p('The preview shows the first six saved items. All saved items are listed below the evidence.', small)]

    story += [section(5, SECTION_TITLES[4], 'Available sources')]
    cv = _safe_dict(evidence.get('cv')); qualifications = _safe_dict(evidence.get('qualifications')); plr = _safe_dict(evidence.get('plr'))
    cv_documents = ', '.join(_text(item.get('filename')) for item in _safe_list(cv.get('documents')) if isinstance(item, dict) and _text(item.get('filename')))
    story += narrative('Curriculum Vitae (CV)', '\n'.join(filter(None, [
        f"Status: {_text(cv.get('status'), 'Not provided').replace('_', ' ').title()}",
        f'Documents: {cv_documents}' if cv_documents else '',
        f"Relevant experience: {_text(cv.get('experience'))}" if _text(cv.get('experience')) else '',
    ])), 'Not provided')
    qualification_rows = [(item.get('label'), item.get('value'), item.get('source')) for item in _safe_list(qualifications.get('items')) if isinstance(item, dict)]
    if qualification_rows:
        story += [data_table(('QUALIFICATION', 'RECORDED VALUE', 'SOURCE'), qualification_rows, [48 * mm, 86 * mm, 40 * mm]), Spacer(1, 2.5 * mm)]
    else:
        story += narrative('Qualifications', qualifications.get('status'), 'Not provided')
    story += narrative('Personal Learning Record (PLR)', plr.get('message') or plr.get('status'), 'Not available')

    if summaries:
        story += [data_table(('SKILLS RADAR CATEGORY', f'AVERAGE / {scale:g}', 'ANSWERED'), [
            (item.get('label'), item.get('score') if _radar_score(item.get('score'), scale) is not None else 'N/A',
             f"{item.get('answered', 0)}/{item.get('total', 0)}") for item in summaries
        ], [84 * mm, 44 * mm, 46 * mm]), Spacer(1, 2.5 * mm)]
    if items:
        story += [data_table(('KSB', 'SAVED ASSESSMENT ITEM', f'SCORE / {scale:g}'), [
            (item.get('code') or item.get('id'), item.get('description'),
             item.get('score') if _radar_score(item.get('score'), scale) is not None else 'Not answered') for item in items
        ], [24 * mm, 118 * mm, 32 * mm])]

    story += [CondPageBreak(35 * mm), section(6, SECTION_TITLES[5], 'Admin decision')]
    relevant = getattr(report, 'relevant_prior_learning', None); reduction = relevant is True and getattr(report, 'reduction_justified', None) is True
    decision = ('Relevant Prior Learning - Training Reduction Approved' if reduction else 'Relevant Prior Learning - No Training Reduction Approved' if relevant is True else 'No Relevant Prior Learning - Full Standard Training Plan' if relevant is False else 'RPL decision not recorded')
    story += narrative('Assessor-recorded decision', decision)
    story += narrative('Evidence reviewed by assessor', getattr(report, 'assessor_evidence_review', ''), 'Not recorded')
    if relevant is True and not reduction:
        story += narrative('Reason no training reduction was approved', getattr(report, 'no_reduction_reason', ''), 'Not recorded')
    story += narrative('Approved RPL assessment justification', getattr(report, 'rpl_justification', ''), 'Awaiting assessor approval') if getattr(report, 'rpl_reviewed_at', None) else narrative('RPL assessment justification', '', 'Awaiting assessor approval')
    if reduction:
        reduction_rows = []
        for row in getattr(report, 'reductions').all():
            original = float(row.planned_hours); reduced = float(row.reduced_hours)
            reduction_rows.append((row.module_title, _hours(original), _hours(reduced), _hours(original - reduced), _text(row.ksb_codes, '—'), row.evidence))
        story += [data_table(('MODULE', 'ORIGINAL', 'REDUCTION', 'ADJUSTED', 'MAPPED KSBS', 'VERIFIED EVIDENCE'), reduction_rows, [43 * mm, 23 * mm, 23 * mm, 23 * mm, 25 * mm, 37 * mm])]
    else:
        story += narrative('Training plan adjustment', f'Full standard Training Plan. Original planned hours: {_hours(training.get("plannedHours"))}. No training reduction approved.')

    story += [CondPageBreak(34 * mm), section(7, SECTION_TITLES[6], 'Final assessment')]
    outcome_labels = {'not_assessed': 'Not assessed', 'suitable': 'Suitable', 'further_assessment': 'Further assessment required'}
    outcome = _text(getattr(report, 'suitability_outcome', 'not_assessed'), 'not_assessed')
    story += [data_table(('FIELD', 'RECORDED VALUE'), [
        ('Suitability outcome', outcome_labels.get(outcome, outcome.replace('_', ' ').title())), ('Report status', status.title()),
        ('Report reference', reference), ('Version / revision', f'{report_version} / {report_revision}'),
        ('Approval date', _date(getattr(report, 'finalised_at', ''))),
    ], [53 * mm, 121 * mm], first_column_bold=True), Spacer(1, 2.5 * mm)]
    story += narrative('Final assessment rationale and recommendation', getattr(report, 'final_summary', ''), 'Not recorded')

    story += [CondPageBreak(70 * mm), section(8, SECTION_TITLES[7], 'Assessor + learner + employer')]
    story += [p('The parties confirm that they have reviewed this exact report version. Electronic signatures are recorded against the immutable issued PDF and its checksum.', small), Spacer(1, 3 * mm)]
    signatures = Table([
        [p('ASSESSOR / ADMIN', signature_title), p('LEARNER', signature_title), p('EMPLOYER / LINE MANAGER', signature_title)],
        [p('Name:\n\nSignature:\n\nDate:', small), p('Name:\n\nSignature:\n\nDate:', small), p('Name:\n\nSignature:\n\nDate:', small)],
    ], colWidths=[58 * mm, 58 * mm, 58 * mm], rowHeights=[10 * mm, 38 * mm])
    signatures.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), purple_wash), ('BOX', (0, 0), (-1, -1), 0.45, purple_line),
        ('INNERGRID', (0, 0), (-1, -1), 0.35, purple_line), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    story += [signatures]

    output = BytesIO()
    # Match previews and issuance byte for byte for the same finalised revision.
    document = BaseDocTemplate(output, pagesize=A4, invariant=True, title='Suitability & Recognition of Prior Learning Report', author='Kent Business College', subject='Suitability and recognition of prior learning assessment')
    frame = Frame(page_margin, 18 * mm, content_width, A4[1] - 52 * mm, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    document.addPageTemplates([
        PageTemplate('Report', [frame], onPage=page_frame),
        PageTemplate('Skills Radar', [frame], onPage=partial(page_frame, evidence_page=True), autoNextPageTemplate='Report'),
    ])
    document.build(story)
    return output.getvalue()


def render_finalised_report(report) -> bytes:
    """Compatibility entry point used by the immutable issuance workflow."""
    return render_report(report)
