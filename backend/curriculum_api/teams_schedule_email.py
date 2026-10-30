"""Render a learner's complete schedule. Pure formatting; no Django, DB or mail."""
import re
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from .teams_calendar_checks import safe_teams_join_url, utc_datetime

TEMPLATE = Path(__file__).with_name('templates') / 'teams_schedule_email.html'
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec')
CELL = 'font-family:Arial,Helvetica,sans-serif;border-bottom:1px solid #e9edf3;vertical-align:top;'


def date_label(value):
    return f'{DAYS[value.weekday()]}, {value.day:02} {MONTHS[value.month - 1]} {value.year}'


def clock_label(value):
    return f'{value.hour % 12 or 12:02}:{value.minute:02} {"AM" if value.hour < 12 else "PM"}'


def slot_label(start, end, zone):
    """One session's day and clock, the way the schedule table prints it."""
    start, end = utc_datetime(start).astimezone(zone), utc_datetime(end).astimezone(zone)
    ends = f' – {date_label(end)}, {clock_label(end)}' if start.date() != end.date() else f' – {clock_label(end)}'
    return f'{date_label(start)}, {clock_label(start)}{ends}'


def session_name(number, titles):
    """A session's name in the email: its live-session component's title, else its number."""
    title = ' '.join(str((titles or {}).get(number) or '').split())
    if title:
        return f'<span style="font-size:12px;color:#5b6477;line-height:18px;">{escape(title)}</span>', title
    return f'<span style="font-size:10px;color:#7b8192;line-height:16px;">SESSION {number:02}</span>', f'Session {number}'


def schedule_rows(occurrences, zone, titles=None):
    """Validated table rows, text rows and distinct join links, in session order.

    ``titles`` maps a session number to the title of the live-session component
    it belongs to; a session without one is named by its number.
    """
    if not 1 <= len(occurrences) <= 52:
        raise ValueError('A module title and 1–52 verified sessions are required.')
    sessions = sorted(occurrences, key=lambda row: utc_datetime(row['scheduled_start']))
    numbers, links, rows, text_rows = set(), [], [], []
    previous_end = None
    for row in sessions:
        start = utc_datetime(row['scheduled_start']).astimezone(zone)
        end = utc_datetime(row['scheduled_end']).astimezone(zone)
        number = int(row['session_number'])
        link = str(row.get('join_url') or '')
        if (number < 1 or number in numbers or utc_datetime(end) <= utc_datetime(start) or not safe_teams_join_url(link)
                or (previous_end and utc_datetime(start) < previous_end)):
            raise ValueError('Session dates, numbering or Teams links could not be verified.')
        numbers.add(number)
        previous_end = utc_datetime(end)
        if link not in links:
            links.append(link)
        minutes = int((utc_datetime(end) - utc_datetime(start)).total_seconds() / 60)
        if not 15 <= minutes <= 1440:
            raise ValueError('Invalid session duration.')
        overnight = f'<br><span style="font-size:11px;">Ends {escape(date_label(end))}</span>' if start.date() != end.date() else ''
        background = '#ffffff' if len(rows) % 2 == 0 else '#f8f9fc'
        name_html, name_text = session_name(number, titles)
        rows.append(f'''<tr bgcolor="{background}">
<td align="left" valign="top" width="54%" style="width:54%;padding:14px 12px;font-family:Arial,Helvetica,sans-serif;border-bottom:1px solid #e9edf3;vertical-align:top;">{name_html}<br><strong style="font-size:13px;line-height:21px;color:#243247;">{escape(date_label(start))}</strong></td>
<td align="left" valign="top" width="46%" style="width:46%;padding:14px 12px;font-family:Arial,Helvetica,sans-serif;border-bottom:1px solid #e9edf3;vertical-align:top;"><span style="font-size:13px;line-height:21px;font-weight:bold;color:#243247;"><span style="white-space:nowrap;">{clock_label(start)}</span> &ndash; <span style="white-space:nowrap;">{clock_label(end)}</span></span>{overnight}<br><span style="font-size:11px;line-height:18px;color:#7b8192;">{minutes} min</span>__ROW_LINK_{number}__</td></tr>''')
        text_rows.append(f'{name_text}: {date_label(start)}, {clock_label(start)} – {date_label(end)}, {clock_label(end)} ({minutes} min)\n{link}')
    multiple = len(links) > 1
    for index, row in enumerate(sessions):
        link = escape(row['join_url'], quote=True)
        row_link = f'<br><a href="{link}" style="font-size:12px;line-height:24px;color:#5b21b6;">Join this session</a>' if multiple else ''
        rows[index] = rows[index].replace(f'__ROW_LINK_{int(row["session_number"])}__', row_link)
    return sessions, links, rows, text_rows


def link_content(sessions, links):
    if len(links) > 1:
        return '<p style="margin:0;font-size:13px;line-height:22px;color:#655575;">Your schedule uses more than one Teams link. Use <strong>Join this session</strong> beside the session you are attending.</p>'
    single_link = escape(links[0], quote=True)
    return f'''
<p style="margin:0 0 18px;font-size:13px;line-height:21px;color:#655575;">Use the same link for all {len(sessions)} sessions listed below.</p>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;border-spacing:0;mso-table-lspace:0pt;mso-table-rspace:0pt;font-family:Arial,Helvetica,sans-serif;"><tr><td bgcolor="#5b21b6" style="background-color:#5b21b6;border-radius:6px;text-align:center;mso-padding-alt:14px 26px;"><a href="{single_link}" style="display:inline-block;padding:14px 26px;border:1px solid #5b21b6;border-radius:6px;font-size:14px;line-height:20px;font-weight:bold;color:#ffffff;text-decoration:none;">Join your Teams session &rarr;</a></td></tr></table>
<p style="margin:15px 0 0;font-size:11px;line-height:18px;color:#776b84;">Button not opening? Copy the meeting link:</p><p style="margin:3px 0 0;font-size:11px;line-height:18px;word-break:break-all;word-wrap:break-word;overflow-wrap:anywhere;"><a href="{single_link}" style="color:#5b21b6;word-break:break-all;word-wrap:break-word;overflow-wrap:anywhere;">{single_link}</a></p>'''


def fill_template(values, has_current):
    html = TEMPLATE.read_text(encoding='utf-8')
    # A calendar with no session left (a cancelled series) has no link to join
    # and no timetable to show, so those sections leave the page entirely.
    html = re.sub(r'\[\[IF_CURRENT\]\](.*?)\[\[END_CURRENT\]\]', (lambda match: match[1]) if has_current else '', html, flags=re.S)
    # Replace template tokens in one pass, so user text cannot introduce tokens.
    return re.sub(r'\[\[([A-Z_]+)\]\]', lambda match: values[match[1]], html)


RECORDING_LABELS = {'none': 'Do not start automatically', 'record': 'Record automatically', 'record-transcribe': 'Record and transcribe'}
LOBBY_LABELS = {'invited': 'People invited to this meeting', 'organization': 'People in my organization',
                'organization-excluding-guests': 'Organization, excluding guests', 'everyone': 'Everyone', 'organizer': 'Only organizers'}
LANGUAGE_LABELS = {'en-GB': 'English (UK)', 'en-US': 'English (US)', 'ar-EG': 'Arabic (Egypt)', 'fr-FR': 'French'}


def meeting_settings(series, time_zone):
    """The saved meeting settings, labelled the way the review dialog shows them."""
    label = lambda labels, value, default: labels.get(str(value or default), str(value or default))
    return [('Time zone', time_zone),
            ('Organizer', str(series.get('organizer_email') or '')),
            ('Recording', label(RECORDING_LABELS, series.get('recording'), 'none')),
            ('Lobby bypass', label(LOBBY_LABELS, series.get('lobby_bypass'), 'invited')),
            ('Language', label(LANGUAGE_LABELS, series.get('spoken_language'), 'en-GB'))]


def settings_section(settings):
    """The organiser copy's meeting settings. Never in a learner's copy."""
    items = ''.join(
        f'<tr bgcolor="{"#ffffff" if index % 2 == 0 else "#f8f9fc"}"><td width="36%" style="width:36%;padding:10px 12px;{CELL}font-size:12px;line-height:20px;color:#667085;">{escape(name)}</td>'
        f'<td width="64%" style="width:64%;padding:10px 12px;{CELL}font-size:13px;line-height:20px;color:#243247;">{escape(value)}</td></tr>'
        for index, (name, value) in enumerate(settings))
    return f'''<tr><td class="pad" style="padding:0 32px 24px;">
<h2 style="margin:0 0 8px;font-size:17px;line-height:26px;color:#243247;">Meeting settings</h2>
<table aria-label="Meeting settings" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border:1px solid #e4e8ef;border-collapse:collapse;border-spacing:0;table-layout:fixed;font-family:Arial,Helvetica,sans-serif;">{items}</table>
</td></tr>
'''


def render_schedule_email(title, occurrences, time_zone, roster=None, settings=None, session_titles=None):
    """Use verified occurrence instants and each occurrence's own join link.

    A learner's copy carries nothing but the shared schedule. The organiser copy
    (``roster`` given, a list of ``(name, email)``) adds the meeting settings
    and every invited learner, which is why the two are rendered separately.
    ``session_titles`` names each session after its live-session component.
    """
    zone = ZoneInfo(time_zone)
    if not title:
        raise ValueError('A module title and 1–52 verified sessions are required.')
    sessions, links, rows, text_rows = schedule_rows(occurrences, zone, session_titles)
    organiser = roster is not None
    values = {'TITLE': escape(title), 'COUNT': str(len(sessions)),
              'PREHEADER': f'Your {len(sessions)} session dates and Teams links, together in one place.',
              'EYEBROW': 'ORGANISER COPY &middot; LIVE ONLINE SESSIONS' if organiser else 'LIVE ONLINE SESSIONS',
              'HEADLINE': 'Your live sessions<br>are planned.',
              'LEAD': 'One place for your meeting link and full session schedule.',
              'INTRO': (f'This is the organiser copy. It lists the meeting settings and the invited learners; each learner receives their own copy without anyone else&rsquo;s details. '
                        f'Here are the upcoming sessions for <strong style="color:#243247;">{escape(title)}</strong>.' if organiser else
                        f'Here are your upcoming sessions for <strong style="color:#243247;">{escape(title)}</strong>. Keep this email handy so you can check your dates and join when each session starts.'),
              'CHANGES': '', 'SCHEDULE_HEADING': 'Your session schedule',
              'ROSTER': (settings_section(settings or []) + roster_section(roster)) if organiser else '',
              'SIGNOFF': 'See you in your session,',
              'FOOTER_NOTE': ('Organiser copy. Learner details in this message are for the calendar&rsquo;s organisers and presenters only.' if organiser
                              else 'This message is for your learning schedule. Other learners&rsquo; email addresses are not included.'),
              'ROWS': ''.join(rows), 'LINK_CONTENT': link_content(sessions, links)}
    # The subject is what the recipient sees in their inbox, so it never carries an
    # internal label: an organiser's copy is told apart by its content, not by its
    # subject line.
    subject = ' '.join(str(title).split())[:160] + ' — your session schedule'
    text = f'{title}\nYour session schedule\n\n' + '\n\n'.join(text_rows)
    if organiser:
        text += '\n\nMeeting settings\n' + '\n'.join(f'{name}: {value}' for name, value in settings or [])
        text += '\n\nInvited learners\n' + '\n'.join(f'{name} <{email}>' if name else email for name, email in roster)
    return subject, fill_template(values, True), text


def schedule_changes(previous, current):
    """What moved, what was cancelled and what was added.

    Paired by start first: a session still starting when it did is the same
    session, even when a session added in front of it renumbered it -- that is
    an addition, not a run of moves. Only what is left pairs by number, which is
    what a genuine move keeps; a cancelled session is then missing from the
    current list and an added one new to it. Unchanged sessions are not changes.
    """
    before, after = list(previous), list(current)
    pairs = []
    for old in list(before):
        new = next((row for row in after if utc_datetime(row['scheduled_start']) == utc_datetime(old['scheduled_start'])), None)
        if new is not None:
            before.remove(old)
            after.remove(new)
            pairs.append((old, new))
    for old in list(before):
        new = next((row for row in after if int(row['session_number']) == int(old['session_number'])), None)
        if new is not None:
            before.remove(old)
            after.remove(new)
            pairs.append((old, new))
    changes = []
    for old, new in pairs:
        if utc_datetime(old['scheduled_end']) != utc_datetime(new['scheduled_end']) or \
                utc_datetime(old['scheduled_start']) != utc_datetime(new['scheduled_start']):
            changes.append(('moved', int(new['session_number']), old, new))
    changes += [('cancelled', int(old['session_number']), old, None) for old in before]
    changes += [('added', int(new['session_number']), None, new) for new in after]
    changes.sort(key=lambda item: utc_datetime((item[2] or item[3])['scheduled_start']))
    return changes


def change_rows(changes, zone, titles=None):
    rows, text_rows = [], []
    for index, (kind, number, old, new) in enumerate(changes):
        background = '#ffffff' if index % 2 == 0 else '#f8f9fc'
        name_html, name_text = session_name(number, titles)
        was = escape(slot_label(old['scheduled_start'], old['scheduled_end'], zone)) if old else ''
        now = escape(slot_label(new['scheduled_start'], new['scheduled_end'], zone)) if new else ''
        if kind == 'moved':
            detail = (f'<span style="font-size:11px;line-height:18px;color:#7b8192;">Was</span><br><span style="font-size:13px;line-height:21px;color:#8a5a5a;text-decoration:line-through;">{was}</span><br>'
                      f'<span style="font-size:11px;line-height:18px;color:#7b8192;">Now</span><br><strong style="font-size:13px;line-height:21px;color:#1f6f43;">{now}</strong>')
            text_rows.append(f'{name_text}: was {slot_label(old["scheduled_start"], old["scheduled_end"], zone)}; now {slot_label(new["scheduled_start"], new["scheduled_end"], zone)}')
        elif kind == 'cancelled':
            detail = (f'<span style="font-size:13px;line-height:21px;color:#8a5a5a;text-decoration:line-through;">{was}</span><br>'
                      '<strong style="font-size:12px;line-height:20px;color:#b42318;">Cancelled</strong>')
            text_rows.append(f'{name_text}: {slot_label(old["scheduled_start"], old["scheduled_end"], zone)} — cancelled')
        else:
            detail = (f'<strong style="font-size:13px;line-height:21px;color:#1f6f43;">{now}</strong><br>'
                      '<span style="font-size:11px;line-height:18px;color:#7b8192;">New session</span>')
            text_rows.append(f'{name_text}: new session on {slot_label(new["scheduled_start"], new["scheduled_end"], zone)}')
        rows.append(f'''<tr bgcolor="{background}">
<td align="left" valign="top" width="24%" style="width:24%;padding:14px 12px;{CELL}">{name_html}</td>
<td align="left" valign="top" width="76%" style="width:76%;padding:14px 12px;{CELL}">{detail}</td></tr>''')
    return rows, text_rows


def roster_section(roster):
    """The organiser copy's list of invited learners. Never in a learner's copy."""
    items = ''.join(
        f'<tr bgcolor="{"#ffffff" if index % 2 == 0 else "#f8f9fc"}"><td style="padding:10px 12px;{CELL}font-size:13px;line-height:20px;color:#243247;">'
        f'{escape(name) if name else ""}{"<br>" if name else ""}<span style="font-size:12px;color:#667085;">{escape(email)}</span></td></tr>'
        for index, (name, email) in enumerate(roster))
    body = items or f'<tr><td style="padding:10px 12px;{CELL}font-size:13px;color:#667085;">No learners are invited to this calendar.</td></tr>'
    return f'''<tr><td class="pad" style="padding:0 32px 24px;">
<h2 style="margin:0 0 8px;font-size:17px;line-height:26px;color:#243247;">Invited learners</h2>
<p style="margin:0 0 12px;font-size:13px;line-height:21px;color:#667085;">{len(roster)} learner{"" if len(roster) == 1 else "s"}. Each learner receives their own copy of this email, without anyone else&rsquo;s details.</p>
<table aria-label="Invited learners" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border:1px solid #e4e8ef;border-collapse:collapse;border-spacing:0;table-layout:fixed;font-family:Arial,Helvetica,sans-serif;">{body}</table>
</td></tr>
'''


def render_change_email(title, previous, current, time_zone, roster=None, session_titles=None):
    """Tell people what their schedule was and what it is now.

    ``previous`` is the saved schedule before the change, ``current`` the one
    Microsoft now holds (possibly empty, when the whole calendar was
    cancelled). A learner's copy carries nothing but the shared schedule. The
    organiser copy is the same message plus ``roster``, a list of
    ``(name, email)`` for the invited learners, which is exactly why the two are
    rendered separately and must never be swapped.
    """
    zone = ZoneInfo(time_zone)
    if not title:
        raise ValueError('A module title is required.')
    changes = schedule_changes(previous, current)
    if not changes:
        raise ValueError('No session date changed, so there is nothing to tell attendees.')
    if len(changes) > 52:
        raise ValueError('A change notice covers at most 52 sessions.')
    change_html, change_text = change_rows(changes, zone, session_titles)
    cancelling = all(kind == 'cancelled' for kind, *_ in changes)
    has_current = bool(current)
    if has_current:
        sessions, links, rows, text_rows = schedule_rows(current, zone, session_titles)
    else:
        sessions, links, rows, text_rows = [], [], [], []
    count = len(changes)
    organiser = roster is not None
    if cancelling and not has_current:
        headline, lead, what = 'Your live sessions<br>are cancelled.', 'Every remaining session in this calendar has been cancelled.', 'have been cancelled'
    elif cancelling:
        headline, lead = f'{"A session is" if count == 1 else "Sessions are"}<br>cancelled.', 'Your remaining sessions and meeting link are below.'
        what = f'{"one session has" if count == 1 else f"{count} sessions have"} been cancelled'
    else:
        headline, lead = 'Your session<br>schedule changed.', 'What your dates were, what they are now, and your full updated schedule.'
        what = f'{"one session has" if count == 1 else f"{count} sessions have"} changed'
    audience = ('This is the organiser copy. It lists the invited learners; each learner receives their own copy without anyone else&rsquo;s details. '
                if organiser else '')
    changes_section = f'''<tr><td class="pad" style="padding:0 32px 26px;">
<h2 style="margin:0 0 8px;font-size:20px;line-height:28px;color:#243247;">What changed</h2>
<p style="margin:0 0 12px;font-size:13px;line-height:21px;color:#667085;">{count} session{"" if count == 1 else "s"} &middot; times in {escape(time_zone)}</p>
<table aria-label="Schedule changes" dir="ltr" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border:1px solid #e4e8ef;border-collapse:collapse;border-spacing:0;table-layout:fixed;mso-table-lspace:0pt;mso-table-rspace:0pt;font-family:Arial,Helvetica,sans-serif;direction:ltr;text-align:left;">
<tbody>{"".join(change_html)}</tbody></table>
</td></tr>
'''
    values = {'TITLE': escape(title), 'COUNT': str(len(sessions)),
              'PREHEADER': f'{escape(title)}: {what}. Your previous and new dates are inside.',
              'EYEBROW': 'ORGANISER COPY &middot; SCHEDULE CHANGE' if organiser else 'LIVE ONLINE SESSIONS &middot; SCHEDULE CHANGE',
              'HEADLINE': headline, 'LEAD': lead,
              'INTRO': f'{audience}For <strong style="color:#243247;">{escape(title)}</strong>, {what}. The table below shows each session as it was and as it is now.',
              'CHANGES': changes_section, 'SCHEDULE_HEADING': 'Your updated schedule',
              'ROSTER': roster_section(roster) if organiser else '',
              'SIGNOFF': 'See you in your session,' if has_current else 'With thanks,',
              'FOOTER_NOTE': ('Organiser copy. Learner details in this message are for the calendar&rsquo;s organisers and presenters only.' if organiser
                              else 'This message is for your learning schedule. Other learners&rsquo; details are not included.'),
              'ROWS': ''.join(rows), 'LINK_CONTENT': link_content(sessions, links) if has_current else ''}
    kind_label = ('sessions cancelled' if count > 1 else 'session cancelled') if cancelling else 'your session schedule has changed'
    # The subject is what the recipient sees in their inbox, so it never carries an
    # internal label: an organiser's copy is told apart by its content, not by its
    # subject line.
    subject = ' '.join(str(title).split())[:160] + f' — {kind_label}'
    text = f'{title}\nWhat changed\n\n' + '\n'.join(change_text)
    if text_rows:
        text += '\n\nYour updated schedule\n\n' + '\n\n'.join(text_rows)
    if organiser:
        text += '\n\nInvited learners\n' + '\n'.join(f'{name} <{email}>' if name else email for name, email in roster)
    return subject, fill_template(values, has_current), text
