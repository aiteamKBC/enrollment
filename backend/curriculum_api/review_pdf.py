"""A signed MCM export, using the supplied Aptem PDF as the layout reference.

The saved instance supplies the questions, answers and original signatures.
Source PDFs and their learners' answers/signatures are never used as content.
"""
import re
from html import escape
from io import BytesIO
from pathlib import Path

from .review_instances import SIGNATURE_ROLES, meeting_summary_field, required_signature_roles

from .review_pdf_presentation import (
    EXPORTABLE_REVIEW_TYPES, IMAGE_PATTERN, ROLE_LABELS,
    REVIEW_TYPE_MCM, REVIEW_TYPE_APTEM_MCM, REVIEW_TYPE_PROGRESS_REVIEW,
    answer_text, display_date, imported_table_rows, is_mcm_review,
    percent_text, programme_dates, render_review_pdf, review_type_code,
    variance_text, visible_fields, _signature_image,
)


def pdf_availability(definition):
    if review_type_code(definition) not in EXPORTABLE_REVIEW_TYPES:
        return None
    signatures = definition.get('signatures', {})
    required = required_signature_roles(signatures)
    if (definition.get('instance') or {}).get('status') != 'completed' or any(
        not signatures.get(role, {}).get('signed') for role in required
    ):
        return {'available': False, 'reason': 'The PDF is available after the review is completed and all required parties have signed.'}
    for role in required:
        state = signatures[role]
        if not state.get('signedName') or not state.get('signedAt') or not IMAGE_PATTERN.fullmatch(state.get('signature') or ''):
            return {'available': False, 'reason': 'A saved signature image, name or date is missing. The signed PDF cannot be generated.'}
    return {'available': True, 'reason': ''}


def learner_information(source, *, name='', programme=''):
    return {
        'name': getattr(source, 'username', '') or name,
        'programme': getattr(source, 'programme', '') or programme,
        'startDate': getattr(source, 'start_date', None),
        'endDate': getattr(source, 'end_date', None),
        # The learner's OWN programme dates, as distinct from the two above --
        # "Created_users"."Start_date"/"End_date" carry the cohort delivery
        # window (active_users.mirror_learner_placement stamps the profile
        # mirror with it), which is shared by everyone placed in that cohort.
        # Carried separately rather than substituted, so the existing MCM
        # export keeps reading exactly the dates it always has.
        'learnerStartDate': getattr(source, 'learner_start_date', None),
        'learnerEndDate': getattr(source, 'learner_end_date', None),
        'employer': getattr(source, 'employer', ''),
        'manager': getattr(source, 'line_manager', ''),
    }


def build_mcm_pdf(definition, information):
    """Native adapter: keep Native availability and semantic-summary policy."""
    availability = pdf_availability(definition)
    if not availability or not availability['available']:
        raise ValueError((availability or {}).get('reason') or 'A signed review is required.')
    return render_review_pdf(
        definition, information, signature_roles=SIGNATURE_ROLES,
        formal_meeting_summary=meeting_summary_field(definition) if is_mcm_review(definition) else None,
    )


def mcm_pdf_response(definition, information):
    from django.http import HttpResponse, JsonResponse
    availability = pdf_availability(definition)
    if availability is None:
        return JsonResponse({'detail': 'This review type has no signed PDF export.'}, status=404)
    if not availability['available']:
        return JsonResponse({'detail': availability['reason']}, status=409)
    try:
        content = build_mcm_pdf(definition, information)
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=409)
    response = HttpResponse(content, content_type='application/pdf')
    identifier = re.sub(r'[^A-Za-z0-9_-]', '', str(definition['instance']['id']))
    prefix = EXPORTABLE_REVIEW_TYPES[review_type_code(definition)]
    response['Content-Disposition'] = f'attachment; filename="{prefix}-{identifier}.pdf"'
    response['Cache-Control'] = 'private, no-store'
    return response


def build_historical_review_pdf(review, information):
    """Render a read-only PDF from one imported Aptem review.

    Imported Aptem rows are historical source data, rather than Curriculum
    instances.  They therefore do not have the frozen LMS signature state
    required by :func:`build_mcm_pdf`.  Keeping this renderer separate means
    the signed-export rules for current reviews cannot be weakened while old
    learners still get a useful document from the data that was imported.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer

    output = BytesIO()
    page_width, page_height = landscape(A4)
    margin = 56
    width = page_width - 2 * margin
    navy, panel = colors.HexColor('#204d66'), colors.HexColor('#f1f3f7')
    body = ParagraphStyle('HistoricalReviewBody', fontName='Helvetica', fontSize=9, leading=11,
                          textColor=navy, splitLongWords=True)
    bold = ParagraphStyle('HistoricalReviewLabel', parent=body, fontName='Helvetica-Bold')

    def paragraph(value, strong=False):
        return Paragraph(escape(str(value or '')).replace('\n', '<br/>'), bold if strong else body)

    def block(title, rows):
        table_rows = [[paragraph(title, True)], *rows]
        table = Table(table_rows, colWidths=[width], repeatRows=1, splitByRow=1, splitInRow=1)
        table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), .55, navy),
            ('INNERGRID', (0, 0), (-1, -1), .35, navy),
            ('BACKGROUND', (0, 0), (-1, 0), panel),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, 0), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
        ]))
        return table

    def field_rows(section):
        rows = []
        fields = section.get('fields') or []
        for field in fields:
            if not isinstance(field, dict):
                continue
            label = field.get('label') or field.get('title') or 'Response'
            value = field['value'] if 'value' in field else field.get('answer')
            rows.append([paragraph(label, True), paragraph(answer_text(value))])
        for table in section.get('tables') or []:
            if not isinstance(table, dict):
                continue
            table_rows = table.get('rows')
            if not isinstance(table_rows, list):
                continue
            table_title = table.get('title') or 'Imported table'
            rows.append([paragraph(table_title, True), paragraph('')])
            for row in table_rows:
                values = row if isinstance(row, list) else [row]
                rows.append([paragraph('', False), paragraph(' | '.join(answer_text(value) for value in values))])
        raw_text = section.get('rawText') or section.get('raw_text')
        if raw_text and raw_text != 'EMPTY_STRING':
            rows.append([paragraph('Imported text', True), paragraph(raw_text)])
        return rows

    title = review.get('name') or review.get('type') or 'Imported Aptem review'
    story = [paragraph(title, True), Spacer(1, 8),
             paragraph('Historical record regenerated from the imported Aptem data.', False),
             Spacer(1, 12)]
    info_rows = [
        ('Programme Name', information.get('programme')),
        ('Review Type', review.get('type')),
        ('Reviewer', review.get('reviewerName')),
        ('Planned Date', display_date(review.get('plannedDate'))),
        ('Completed Date', display_date(review.get('completedDate'))),
        ('Status', review.get('status')),
    ]
    info_table = Table([[paragraph(information.get('name') or review.get('learnerName') or 'Not recorded', True),
                         Paragraph('<br/>'.join(
                             f'<b>{escape(label)}:</b> {escape(str(value or "Not recorded"))}'
                             for label, value in info_rows
                         ), body)]], colWidths=[150, width - 164])
    info_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), .55, navy),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEAFTER', (0, 0), (0, -1), .4, navy),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (1, 0), (1, -1), 10),
    ]))
    story.extend([block('Information', [[info_table]]), Spacer(1, 14)])

    sections = review.get('sections') or []
    rendered = False
    for section in sections:
        if not isinstance(section, dict):
            continue
        rows = field_rows(section)
        if not rows:
            continue
        rendered = True
        story.extend([block(section.get('name') or 'Review section', rows), Spacer(1, 14)])
    if not rendered:
        story.append(block('Review details', [[paragraph('No detailed responses were imported for this review.')]]))

    logo = Path(__file__).parent / 'templates' / 'kbc-logo.png'

    def page_header(canvas, document):
        canvas.saveState()
        canvas.setTitle(str(title))
        canvas.setAuthor('Kent Business College')
        canvas.setFont('Helvetica-Bold', 11)
        canvas.drawCentredString(page_width / 2, page_height - 40, 'Historical Aptem Review')
        if logo.is_file():
            canvas.drawImage(str(logo), page_width - margin - 75, page_height - 86, width=75, height=75,
                             preserveAspectRatio=True, mask='auto')
        canvas.setFont('Helvetica', 8)
        canvas.drawCentredString(page_width / 2, 25, f'Page {document.page}')
        canvas.restoreState()

    SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=margin, rightMargin=margin,
                      topMargin=85, bottomMargin=43).build(story, onFirstPage=page_header,
                                                           onLaterPages=page_header)
    return output.getvalue()


def historical_pdf_response(review, information, *, identifier, original_content=None):
    """Return the verified original PDF for an imported Aptem review.

    Aptem reviews are historical records.  The LMS must return the exact PDF
    stored by Aptem when one is available; it must not silently create a new
    document that could differ from the source record.
    """
    from django.http import HttpResponse, JsonResponse

    if not original_content:
        return JsonResponse(
            {'detail': 'The original Aptem PDF is unavailable for this review.'},
            status=404,
        )
    content = original_content
    safe_identifier = re.sub(r'[^A-Za-z0-9_-]', '', str(identifier)) or 'review'
    response = HttpResponse(content, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Aptem-Review-{safe_identifier}.pdf"'
    response['X-Review-PDF-Source'] = 'aptem-original'
    response['Cache-Control'] = 'private, no-store'
    return response
