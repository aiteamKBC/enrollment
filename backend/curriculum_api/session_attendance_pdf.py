"""Staff attendance report from already saved evidence; no database or network."""
from html import escape
from io import BytesIO
from zoneinfo import ZoneInfo

from .session_results_policy import instant


def report_time(value):
    parsed = instant(value)
    return parsed.astimezone(ZoneInfo('Europe/London')).strftime('%d/%m/%Y %H:%M:%S %Z') if parsed else 'Not recorded'


def build_attendance_pdf(session):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    output = BytesIO()
    width, height = landscape(A4)
    margin = 36
    purple = colors.HexColor('#5925b5')
    body = ParagraphStyle('AttendanceBody', fontName='Helvetica', fontSize=9, leading=13, splitLongWords=True)
    heading = ParagraphStyle('AttendanceHeading', parent=body, fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=purple)
    label = ParagraphStyle('AttendanceLabel', parent=body, fontName='Helvetica-Bold')
    table_heading = ParagraphStyle('AttendanceTableHeading', parent=label, textColor=colors.white)
    paragraph = lambda value, style=body: Paragraph(escape(str(value)).replace('\n', '<br/>'), style)
    story = [paragraph('Session attendance', heading), Spacer(1, 8),
             paragraph(f"{session.get('title') or 'Lecture'} - Session {session['sessionNumber']}", label), Spacer(1, 8),
             paragraph(f"Scheduled: {report_time(session.get('startsAt'))} - {report_time(session.get('endsAt'))}"),
             paragraph('All times use Europe/London (GMT/BST).')]
    for number, run in enumerate(session.get('runs') or [], 1):
        story.append(paragraph(f"Recorded run {number}: {report_time(run['startsAt'])} - {report_time(run['endsAt'])}"))
    if not session.get('runs'):
        story.append(paragraph('Actual meeting times: not recorded.'))
    if not session.get('reportReady'):
        story.append(paragraph('Awaiting a completed Teams attendance report; pending participants are not marked absent.', label))
    story.append(Spacer(1, 14))
    labels = {'present': 'Present', 'absent': 'Absent', 'pending': 'Awaiting report', 'review': 'Identity needs review',
              'absent_excused': 'Absent, excused', 'made_up': 'Made up - catch-up completed'}
    rows = [[paragraph(text, table_heading) for text in ('Learner', 'Joined', 'Left', 'Duration', 'Attendance', 'Recovery')]]
    for person in session.get('attendance') or []:
        duration = f"{int(person['seconds']) // 60}m {int(person['seconds']) % 60}s"
        visits = person.get('intervals') or [None]
        for index, visit in enumerate(visits):
            rows.append([paragraph(f"{person['name']}\n{person.get('email') or 'Unmatched identity'}"),
                         paragraph(report_time(visit['joinedAt']) if visit else 'Not recorded'),
                         paragraph(report_time(visit['leftAt']) if visit else 'Not recorded'),
                         paragraph(duration if index == 0 else 'Included above'),
                         paragraph(labels.get(person.get('rawStatus', person['status']), person.get('rawStatus', person['status']))),
                         paragraph(
                             'Catch-up booked' if person.get('recoveryStatus') == 'catchup_booked'
                             else 'Recovery requested' if person.get('recoveryStatus') == 'requested'
                             else 'Not requested' if person.get('rawStatus', person['status']) == 'absent'
                             else '-'
                         )])
    if len(rows) == 1:
        story.append(paragraph('No participants have been saved for this session.'))
    else:
        available = width - 2 * margin
        table = Table(rows, colWidths=[available * share for share in (.25, .17, .17, .11, .14, .16)], repeatRows=1, splitByRow=1, splitInRow=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), purple), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f6f3fb')]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), .35, colors.HexColor('#ded6ee')),
            ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]))
        story.append(table)
    story += [Spacer(1, 12), paragraph('Durations use saved Teams evidence and avoid counting overlapping visits twice. Watching a recording does not establish live attendance.')]

    def footer(canvas, document):
        canvas.saveState(); canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawString(margin, 19, 'LMS | Saved Teams attendance | Europe/London')
        canvas.drawRightString(width - margin, 19, f'Page {document.page}')
        canvas.restoreState()

    document = SimpleDocTemplate(output, pagesize=(width, height), leftMargin=margin, rightMargin=margin,
                                 topMargin=margin, bottomMargin=36, title='Session attendance', author='LMS')
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
