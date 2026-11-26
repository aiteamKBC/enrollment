"""Synthetic PDF generator. Fictional identifiers only; no original LRS files."""
from io import BytesIO
from reportlab.pdfgen.canvas import Canvas

EVENT_COLUMNS = [('Provider', 25), ('Awarding Body', 115), ('Type', 210), ('Reference Number', 260), ('Aim Description', 330), ('Start Date', 475), ('End Date', 530), ('Grade', 585), ('Completion Status', 630), ('Outcome Status', 700), ('Award Date', 765)]
ACH_COLUMNS = [('Provider', 25), ('Awarding Body', 115), ('Type', 210), ('Qualification Level', 275), ('Title', 360), ('Reference Number', 515), ('Achieved Date', 610), ('Achieved Grade', 690), ('Language for Assessment', 770)]


def synthetic_pdf(pages=None, uln='1234509876', name='Morgan Synthetic Learner', dob='12 Mar 1994', displayed=None,
                  totals_override=None, repeat_banners=True, printed_counts=True, event_columns=None, horizontal_watermark=False,
                  repeat_identity=True, line_spacing=9, row_padding=10):
    pages = pages if pages is not None else [('Learning Events', [
        ['Fictional Academy\nNorth Campus', 'Example Awards', 'Award', '9912345678', 'Award in Fictional\nPayroll Practice (RQF)', '03 Jan 2020', '12 Feb 2020', 'L1', 'Completed', 'Achieved', ''],
        ['Imaginary College', 'Example Awards', 'Certificate', 'SYN-002', 'Digital Practice', '04 Mar 2021', '30 Jun 2021', 'Not Applicable', 'Withdrawn', 'No Achievement', ''],
    ]), ('Achievements', [['Example Institute', 'Fictional Awards', 'Diploma', '3', 'Synthetic Diploma\nin Example Studies', 'SYN-003', '20 Jul 2022', '0', 'English']])]
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(842, 595))
    totals = {section: sum(len(rows) for name, rows in pages if name == section) for section, _ in pages}
    totals.update(totals_override or {})
    for page_index, (section, rows) in enumerate(pages):
        canvas.setFont('Helvetica-Bold', 14)
        canvas.drawString(320, 545, 'Personal Learning Record')
        if repeat_identity or page_index == 0:
            canvas.drawString(320, 526, name)
            canvas.drawString(320, 508, dob)
            if uln:
                canvas.drawString(320, 490, uln)
        canvas.saveState(); canvas.setFillAlpha(.08); canvas.translate(100, 80); canvas.rotate(45)
        canvas.setFont('Helvetica', 45); canvas.drawString(0, 0, 'Learning Records Service'); canvas.restoreState()
        canvas.setFont('Helvetica-Bold', 12)
        count = totals[section]
        if repeat_banners or page_index == 0:
            canvas.drawString(25, 455, f'{section} ({displayed if displayed is not None else count}/{count})' if printed_counts else section)
        columns = (event_columns or EVENT_COLUMNS) if section == 'Learning Events' else ACH_COLUMNS
        canvas.setFont('Helvetica', 6.5)
        for heading, x in columns:
            parts = heading.split(' ')
            canvas.drawString(x, 433, parts[0])
            if len(parts) > 1: canvas.drawString(x, 424, ' '.join(parts[1:]))
        y = 403
        for index, row in enumerate(rows):
            height = max(len(cell.split('\n')) for cell in row) * line_spacing + row_padding
            if index % 2:
                canvas.setFillColorRGB(.85, .85, .85); canvas.rect(25, y - height + 9, 790, height, fill=1, stroke=0)
                canvas.setFillColorRGB(0, 0, 0)
            for cell, (_, x) in zip(row, columns):
                for offset, text in enumerate(cell.split('\n')): canvas.drawString(x, y - offset * line_spacing, text)
            y -= height
        if not rows: canvas.drawString(25, y, 'No records')
        if horizontal_watermark: canvas.drawString(25, y - 20, 'Learning Records Service')
        canvas.setFont('Helvetica', 8)
        canvas.drawString(260, 35, 'Created on Tuesday, November 24, 2026')
        canvas.drawString(330, 23, '(PROTECT - PERSONAL)')
        canvas.drawString(25, 12, 'SYNTHETIC TEST FIXTURE - NOT A REAL LRS REPORT')
        canvas.showPage()
    canvas.save()
    return stream.getvalue()


def synthetic_boundary_cases():
    """Previously rejected layouts, generated in memory for API regressions."""
    first = ['Synthetic College', '', 'Award', 'SYN-A', 'Synthetic award', '', '', '', '', '', '']
    second = ['', '', 'Award', 'SYN-B', 'Separate award', '', '', '', '', '', '']
    yield 'tight_rows', synthetic_pdf([('Learning Events', [first, second])], row_padding=1.5), [
        ('SYN-A', 'Synthetic award'), ('SYN-B', 'Separate award')]
    wrapped = first.copy(); wrapped[3] = 'SYN-\n001'
    yield 'wrapped_reference', synthetic_pdf([('Learning Events', [wrapped])]), [('SYN- 001', 'Synthetic award')]
    spaced = first.copy(); spaced[4] = 'Synthetic award\ncontinued title'
    yield 'spaced_continuation', synthetic_pdf([('Learning Events', [spaced])], line_spacing=14), [
        ('SYN-A', 'Synthetic award continued title')]


if __name__ == '__main__':
    from pathlib import Path
    target = Path(__file__).parent / 'fixtures' / 'synthetic_plr.pdf'
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(synthetic_pdf())
