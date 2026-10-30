"""Shared, model-free completed-review PDF presentation.

Adapters supply saved display data and signature order. This module does not
resolve templates, query learners, calculate progress or call external services.
"""
import base64
import binascii
import json
import re
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

ROLE_LABELS = {'advisor': 'Advisor', 'employer': 'Employer', 'participant': 'Participant', 'referrer': 'Referrer'}
IMAGE_PATTERN = re.compile(r'^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$', re.I)

REVIEW_TYPE_MCM = 'mcm'
REVIEW_TYPE_APTEM_MCM = 'aptem_mcm'
REVIEW_TYPE_PROGRESS_REVIEW = 'progress_review'
#: Review Types with a signed export, by the Review Type's stable code -- never
#: by a template's name. Anything else has no PDF at all (404), exactly as
#: before this Progress Review entry was added.
EXPORTABLE_REVIEW_TYPES = {
    REVIEW_TYPE_MCM: 'Monthly-Coaching-Meeting',
    REVIEW_TYPE_APTEM_MCM: 'Monthly-Coaching-Meeting',
    REVIEW_TYPE_PROGRESS_REVIEW: 'Progress-Review',
}


def review_type_code(definition):
    return definition.get('template', {}).get('reviewTypeCode')


def is_mcm_review(definition):
    return review_type_code(definition) in {REVIEW_TYPE_MCM, REVIEW_TYPE_APTEM_MCM}


def imported_table_rows(field):
    configuration = field.get('configuration') or {}
    value = configuration.get('importedTable')
    if value is None and configuration.get('imported') and isinstance(configuration.get('description'), str):
        try:
            value = json.loads(configuration['description'])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(value, list) or len(value) < 2 or not all(isinstance(row, list) for row in value):
        return None
    width = len(value[0])
    if not width or any(len(row) != width for row in value):
        return None
    return [['' if cell is None else str(cell) for cell in row] for row in value]


def programme_dates(definition, information):
    """(start, end) for the Information block.

    A Progress Review states the individual learner's own programme start date
    -- the same date its progress snapshot was calculated from -- so the two
    can never disagree inside one document. Everything else keeps the enrolment
    window it already showed.
    """
    if review_type_code(definition) != REVIEW_TYPE_PROGRESS_REVIEW:
        return information.get('startDate'), information.get('endDate')
    snapshot = definition.get('progressSnapshot') or {}
    start = snapshot.get('calculatedFrom') or information.get('learnerStartDate') or information.get('startDate')
    return start, information.get('learnerEndDate') or information.get('endDate')


def percent_text(value):
    return 'Not recorded' if value is None else f'{round(float(value))}%'


def variance_text(metric):
    """"23% above expected (57%)" -- the reference's above/below annotation,
    read from the stored snapshot rather than recomputed."""
    variance, direction = metric.get('variancePercent'), metric.get('varianceDirection')
    if variance is None or not direction:
        return ''
    expected = metric.get('expectedPercent')
    suffix = '' if expected is None else f' (expected {percent_text(expected)})'
    return f'{abs(round(float(variance)))}% {direction} expected{suffix}'


def display_date(value, *, include_time=False):
    if not value:
        return 'Not recorded'
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if include_time:
            parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            return parsed.astimezone(ZoneInfo('Europe/London')).strftime('%d/%m/%Y %H:%M') + ' (Europe/London)'
        return parsed.strftime('%d/%m/%Y')
    except ValueError:
        return str(value)


def answer_text(value):
    if value is None or value == '' or value == []:
        return 'Not recorded'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, list):
        return '; '.join(answer_text(item) for item in value)
    if isinstance(value, dict):
        return '\n'.join(f'{key}: {answer_text(item)}' for key, item in value.items())
    return str(value)


def visible_fields(fields):
    for field in fields or []:
        yield field
        if field.get('fieldType') == 'boolean_case_block':
            answer = field.get('answer')
            branch = 'yesFields' if answer in ('yes', True) else 'noFields' if answer in ('no', False) else None
            if branch:
                yield from visible_fields(field.get(branch))


def _signature_image(state, *, max_pixels=8_000_000):
    from PIL import Image as PillowImage
    from reportlab.platypus import Image

    match = IMAGE_PATTERN.fullmatch(state.get('signature') or '')
    if not match or len(match[2]) > 2_000_000:
        raise ValueError('The saved signature image cannot be included in the PDF.')
    try:
        content = base64.b64decode(match[2], validate=True)
        with PillowImage.open(BytesIO(content)) as im:
            if im.width * im.height > max_pixels:
                raise ValueError('The saved signature image is too large.')
            im.load()
            output = BytesIO()
            im.convert('RGBA').save(output, format='PNG')
            width, height = im.size
        output.seek(0)
        scale = min(180 / width, 58 / height)
        return Image(output, width=width * scale, height=height * scale, hAlign='LEFT')
    except (OSError, ValueError, binascii.Error) as exc:
        raise ValueError('The saved signature image cannot be included in the PDF.') from exc


def render_review_pdf(definition, information, *, signature_roles,
                      formal_meeting_summary=None, mcm_summary_block=True,
                      rag_history_block=True, provenance='', keep_signature_blocks=False,
                      signature_image_max_pixels=8_000_000):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak, KeepTogether

    output = BytesIO()
    page_width, page_height = landscape(A4)
    margin = 56
    width = page_width - 2 * margin
    navy, panel = colors.HexColor('#204d66'), colors.HexColor('#f1f3f7')
    body = ParagraphStyle('ReviewBody', fontName='Helvetica', fontSize=9, leading=11, textColor=navy, splitLongWords=True)
    bold = ParagraphStyle('ReviewLabel', parent=body, fontName='Helvetica-Bold')

    def paragraph(value, strong=False):
        return Paragraph(escape(str(value or '')).replace('\n', '<br/>'), bold if strong else body)

    def section(title, rows, *, repeat_header=True):
        table = Table([[paragraph(title, True)], *[[row] for row in rows]], colWidths=[width],
                      repeatRows=1 if repeat_header else 0, splitByRow=1, splitInRow=1)
        table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), .55, navy), ('INNERGRID', (0, 0), (-1, -1), .35, navy),
            ('BACKGROUND', (0, 0), (-1, 0), panel), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, 0), 6), ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ]))
        return table

    instance = definition['instance']
    title = definition['template']['name']
    story = [paragraph(title, True), Spacer(1, 12)]
    start_date, end_date = programme_dates(definition, information)
    info_rows = [
        ('Programme Name', information.get('programme')),
        ('Programme Start Date', display_date(start_date)),
        ('Planned End Date', display_date(end_date)),
        ('Employer', information.get('employer')), ('Manager', information.get('manager')),
        ('Review Planned Date', display_date(instance.get('targetDate'))),
        ('Review Completed Date', display_date(instance.get('completedAt'))),
    ]
    info = Table([[paragraph(information.get('name') or 'Not recorded', True),
                   Paragraph('<br/>'.join(f'<b>{escape(label)}:</b> {escape(str(value or "Not recorded"))}' for label, value in info_rows), body)]],
                 colWidths=[150, width - 164])
    info.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                             ('LINEAFTER', (0, 0), (0, -1), .4, navy),
                             ('LEFTPADDING', (1, 0), (1, -1), 10)]))
    story.extend([section('Information', [info]), Spacer(1, 16)])

    def progress_bar(metric, *, bar_width, fill='#17a89f'):
        """Reference-style fill and independent black target marker."""
        from reportlab.graphics.shapes import Drawing, Line, Rect, String

        drawing = Drawing(bar_width, 45)
        drawing.add(Rect(0, 24, bar_width, 13, fillColor=colors.HexColor('#d7d7d7'),
                         strokeColor=colors.HexColor('#9ca3a8'), strokeWidth=.6))
        actual = metric.get('actualPercent')
        if actual is not None:
            filled = max(0.0, min(float(actual), 100.0)) / 100 * bar_width
            if filled > 0:
                drawing.add(Rect(0, 24, filled, 13, fillColor=colors.HexColor(fill), strokeColor=None))
            label_x = max(0, min(filled, bar_width))
            anchor = 'start' if label_x < 18 else 'end' if label_x > bar_width - 18 else 'middle'
            drawing.add(String(label_x, 9, percent_text(actual), fontName='Helvetica', fontSize=8,
                               textAnchor=anchor, fillColor=colors.HexColor('#30383d')))
        drawing.add(String(0, 9, '0%', fontName='Helvetica', fontSize=8,
                           fillColor=colors.HexColor('#30383d')))
        expected = metric.get('expectedPercent')
        if expected is not None:
            marker = max(0.0, min(float(expected), 100.0)) / 100 * bar_width
            drawing.add(Line(marker, 19, marker, 43, strokeColor=colors.black, strokeWidth=1.8))
        return drawing

    def report_variance(metric):
        value = metric.get('variancePercent')
        if value is None:
            return 'Target unavailable'
        rounded = round(abs(float(value)))
        if rounded == 0:
            return 'On target'
        return f'{rounded}% {"Below" if metric.get("varianceDirection") == "below" else "Above"}'

    def progress_metric(label, metric, *, bar_width, fill='#17a89f'):
        caption = Paragraph(escape(report_variance(metric)), ParagraphStyle(
            'ProgressVariance', parent=body, alignment=1, fontSize=8.5, leading=10,
            textColor=colors.HexColor('#30383d'),
        ))
        table = Table([
            [Paragraph(f'<b>{escape(label)}</b>', body)],
            [progress_bar(metric, bar_width=bar_width, fill=fill)],
            [caption],
        ], colWidths=[bar_width], rowHeights=[None, 45, 12])
        table.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return table

    def progress_donut(metric):
        from reportlab.graphics.shapes import Circle, Drawing, String, Wedge

        drawing = Drawing(190, 205)
        drawing.add(String(95, 187, 'Learning Plan Progress', fontName='Helvetica', fontSize=12,
                           textAnchor='middle', fillColor=colors.HexColor('#174c67')))
        drawing.add(Circle(95, 91, 66, fillColor=colors.HexColor('#dce4ec'), strokeColor=None))
        actual = metric.get('actualPercent')
        if actual is not None and float(actual) > 0:
            extent = 360 * max(0.0, min(float(actual), 100.0)) / 100
            drawing.add(Wedge(95, 91, 66, 90 - extent, 90,
                              fillColor=colors.HexColor('#174ed4'), strokeColor=None))
        drawing.add(Circle(95, 91, 45, fillColor=colors.white, strokeColor=None))
        drawing.add(String(95, 85, percent_text(actual), fontName='Helvetica', fontSize=19,
                           textAnchor='middle', fillColor=colors.HexColor('#111827')))
        return drawing

    def progress_panel(snapshot):
        left_width = width * .33
        right_width = width - left_width
        bar_width = right_width - 32
        ksb = snapshot.get('ksbProgress') or {}
        ksb_title = ksb.get('title') or 'Apprenticeship Standard progress'
        if ksb.get('available') and ksb.get('actualPercent') is not None and ksb.get('expectedPercent') is not None:
            ksb_variance = ksb.get('variancePercent')
            ksb_metric = {
                **ksb,
                'variancePercent': (
                    ksb_variance if ksb_variance is not None
                    else float(ksb['actualPercent']) - float(ksb['expectedPercent'])
                ),
                'varianceDirection': ksb.get('varianceDirection') or (
                    'above' if float(ksb['actualPercent']) >= float(ksb['expectedPercent']) else 'below'
                ),
            }
            ksb_block = progress_metric(ksb_title, ksb_metric, bar_width=bar_width, fill='#174ed4')
        else:
            unavailable = escape(str(ksb.get('reason') or 'KSB progress was not available in this snapshot.'))
            ksb_block = Table([
                [Paragraph(f'<b>{escape(ksb_title)}</b>', body)],
                [Table([['']], colWidths=[bar_width], rowHeights=[13], style=TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#e1e3e5')),
                    ('BOX', (0, 0), (-1, -1), .6, colors.HexColor('#b8bdc1')),
                ]))],
                [Paragraph(unavailable, ParagraphStyle('Unavailable', parent=body, alignment=1, fontSize=8))],
            ], colWidths=[bar_width], style=TableStyle([
                ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
        right = Table([
            [ksb_block],
            [progress_metric('Off-the-job hours progress', snapshot.get('offTheJobHours') or {}, bar_width=bar_width)],
            [progress_metric('Programme progress', snapshot.get('programmeProgress') or {}, bar_width=bar_width)],
        ], colWidths=[right_width], style=TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 16), ('RIGHTPADDING', (0, 0), (-1, -1), 16),
            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        panel_table = Table([[progress_donut(snapshot.get('programmeProgress') or {}), right]],
                            colWidths=[left_width, right_width], rowHeights=[245])
        panel_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), .55, colors.HexColor('#ccd5df')),
            ('LINEAFTER', (0, 0), (0, 0), .55, colors.HexColor('#ccd5df')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [Paragraph(
            '<b>Calculated from:</b> {} &nbsp;&nbsp; <b>Calculated at:</b> {}'.format(
                escape(display_date(snapshot.get('calculatedFrom'))),
                escape(display_date(snapshot.get('calculatedAt'), include_time=True)),
            ), body), Spacer(1, 6), panel_table]

    if review_type_code(definition) == REVIEW_TYPE_PROGRESS_REVIEW:
        # Exactly what was frozen when the coach pressed Calculate. An
        # instance that was never calculated says so, rather than this export
        # quietly reaching for the learner's current figures.
        snapshot = definition.get('progressSnapshot')
        story.extend([
            section('Learning Progress', progress_panel(snapshot) if snapshot
                    else [paragraph('No progress snapshot was calculated for this review.')]),
            Spacer(1, 16),
        ])

    for block in sorted(definition.get('sections', []), key=lambda item: item.get('displayOrder', 0)):
        if not block.get('enabled', True):
            continue
        rows = []
        for field in visible_fields(block.get('fields')):
            # The canonical summary has its dedicated PDF block below. Skip it
            # here so the exact formal answer is rendered once, not duplicated.
            if formal_meeting_summary and field.get('id') == formal_meeting_summary.get('id'):
                continue
            if field.get('fieldType') == 'action_button':
                continue
            imported_rows = imported_table_rows(field)
            if imported_rows:
                column_width = width / len(imported_rows[0])
                imported_table = Table(
                    [[paragraph(cell, row_index == 0) for cell in row] for row_index, row in enumerate(imported_rows)],
                    colWidths=[column_width] * len(imported_rows[0]), repeatRows=1,
                )
                imported_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eee9ff')),
                    ('GRID', (0, 0), (-1, -1), .35, colors.HexColor('#c9c3dc')),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                    ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ]))
                rows.append([paragraph(field.get('title'), True), Spacer(1, 4), imported_table])
                continue
            kind = field.get('fieldType')
            if kind in ('boolean', 'boolean_case_block', 'numeric', 'date', 'list_item', 'email', 'phone'):
                answer = display_date(field.get('answer')) if kind == 'date' else answer_text(field.get('answer'))
                rows.append(Paragraph(f'<b>{escape(str(field.get("title") or ""))}</b> {escape(answer)}', body))
                continue
            row = [paragraph(field.get('title'), True)]
            description = (field.get('configuration') or {}).get('description')
            if description:
                row.append(paragraph(description))
            if field.get('fieldType') != 'title_description':
                row.extend([Spacer(1, 3), paragraph(answer_text(field.get('answer')))])
            rows.append(row)
        if rows:
            story.extend([section(block.get('title', ''), rows), Spacer(1, 16)])

    if review_type_code(definition) == REVIEW_TYPE_PROGRESS_REVIEW and rag_history_block:
        # One row per completed Progress Review this learner has had, each
        # showing the RAG that review itself recorded -- a review that captured
        # none reads "None", the way the legacy export shows it. Never the
        # learner's current coach_rag, which would rewrite signed history.
        history = definition.get('ragHistory') or []
        story.extend([
            section('RAG Status', [
                Paragraph(
                    '<b>{}{}</b><br/>{}'.format(
                        escape(str(entry.get('reviewName') or 'Progress Review')),
                        f" - {escape(display_date(entry.get('targetDate')))}" if entry.get('targetDate') else '',
                        escape(str(entry.get('rag') or 'None')),
                    ), body)
                for entry in history
            ] if history else [paragraph('No completed Progress Reviews recorded.')]),
            Spacer(1, 16),
        ])
    elif is_mcm_review(definition) and mcm_summary_block:
        # The signed document reads only the answer attached to this frozen
        # semantic field. It never reaches the mutable Coach AI artifact.
        summary = formal_meeting_summary.get('answer') if formal_meeting_summary else None
        summary_text = summary.strip() if isinstance(summary, str) and summary.strip() else 'No Summary Generated'
        story.extend([section('Meeting Summary', [paragraph(summary_text)]), Spacer(1, 16)])

    # A dedicated final page follows the PR reference, using the actual saved
    # mark. Never substitute a typed name or another document's signature.
    story.append(PageBreak())
    for role in signature_roles:
        state = definition['signatures'].get(role, {})
        # Frozen requirements are authoritative. An optional party is shown
        # only when they actually signed; the PDF must not invent a learner
        # requirement that Curriculum did not freeze onto this instance.
        if not (state.get('required') or state.get('signed')):
            continue
        image = _signature_image(state, max_pixels=signature_image_max_pixels)
        content = [paragraph(f"Name: {state['signedName']}"), Spacer(1, 6), paragraph('Signature:', True),
                   image, Spacer(1, 6), paragraph(f"Date: {display_date(state['signedAt'], include_time=True)}")]
        block = [section(ROLE_LABELS[role], [content]), Spacer(1, 12)]
        story.extend([KeepTogether(block)] if keep_signature_blocks else block)

    logo = Path(__file__).parent / 'templates' / 'kbc-logo.png'

    def page_header(canvas, document):
        canvas.saveState()
        canvas.setTitle(title)
        canvas.setAuthor('Kent Business College')
        canvas.setFont('Helvetica-Bold', 11)
        canvas.drawCentredString(page_width / 2, page_height - 40, 'Review')
        if logo.is_file():
            canvas.drawImage(str(logo), page_width - margin - 75, page_height - 86, width=75, height=75, preserveAspectRatio=True, mask='auto')
        canvas.setFont('Helvetica', 8)
        canvas.drawCentredString(page_width / 2, 25, f'Page {document.page}')
        if provenance:
            canvas.setFont('Helvetica', 7)
            canvas.drawCentredString(page_width / 2, 12, provenance)
        canvas.restoreState()

    SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=margin, rightMargin=margin,
                      topMargin=85, bottomMargin=43).build(story, onFirstPage=page_header, onLaterPages=page_header)
    return output.getvalue()


