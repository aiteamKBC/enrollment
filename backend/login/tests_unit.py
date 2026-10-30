"""Unit tests for the login app's pure logic.

Separate from ``tests.py``, which drives the HTTP endpoints and needs the Neon
tables. Almost everything here runs without touching a database, so it is the
fast suite: run it while working on ``security.py``, ``sessions.py`` or
``email_azure.py`` and get an answer in about a second.

    python manage.py test login.tests_unit

The two classes that do need the database (session issue/revoke, and the
throttle counters that read the audit table) are marked with ``databases`` and
are still cheap — they touch one table each.

What is being pinned here is behaviour that is easy to break silently: the exact
backoff schedule, the fact that a token hash is not the token, which settings
count as "mail is configured", and that the fallback never prints a live link in
production.
"""
from __future__ import annotations

import io
import uuid
from datetime import timedelta
from unittest import mock

from django.core import signing
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import email_azure, identity, invitations, microsoft_sso
from .models import (
    ROLE_ADMIN,
    ROLE_EMPLOYER,
    ROLE_LEARNER,
    ROLE_STAFF,
    LoginAccount,
    LoginAudit,
    LoginSession,
)
from .security import (
    INVITATION_TTL,
    LOCKOUT_BASE_MINUTES,
    LOCKOUT_MAX_MINUTES,
    LOCKOUT_THRESHOLD,
    MIN_PASSWORD_LENGTH,
    RESET_TTL,
    SESSION_TTL,
    SESSION_TTL_REMEMBER,
    THROTTLE_MAX_FAILURES_PER_IP,
    PasswordPolicyError,
    client_ip,
    generate_token,
    hash_password,
    hash_token,
    lock_duration_for,
    looks_like_email,
    normalize_email,
    tokens_equal,
    user_agent,
    validate_password_strength,
    verify_password,
)
from .services import InvitePermissionError, _authorise


# ---------------------------------------------------------------------------
# Pure helpers — no database
# ---------------------------------------------------------------------------

class NormalisationTests(SimpleTestCase):
    def test_email_is_lowercased_and_trimmed(self):
        self.assertEqual(normalize_email("  Person@KBC.Test \n"), "person@kbc.test")

    def test_email_normalisation_handles_none_and_empty(self):
        self.assertEqual(normalize_email(None), "")
        self.assertEqual(normalize_email(""), "")

    def test_dots_and_plus_tags_are_preserved(self):
        """Provider-specific aliasing must not be collapsed — see normalize_email.

        Treating a.b@ and ab@ as one identity would be wrong for the corporate
        mail systems this deployment uses.
        """
        self.assertEqual(normalize_email("A.B+tag@Kbc.test"), "a.b+tag@kbc.test")

    def test_looks_like_email_accepts_ordinary_addresses(self):
        for address in ("a@b.co", "first.last@sub.domain.org", " Person@KBC.Test "):
            self.assertTrue(looks_like_email(address), address)

    def test_looks_like_email_rejects_malformed_input(self):
        for address in ("", None, "no-at-sign", "@nolocal.test", "a@b", "a b@c.test"):
            self.assertFalse(looks_like_email(address), repr(address))


class TokenPrimitiveTests(SimpleTestCase):
    def test_tokens_are_unique_and_url_safe(self):
        tokens = {generate_token() for _ in range(500)}
        self.assertEqual(len(tokens), 500)
        for token in list(tokens)[:20]:
            # token_urlsafe output must survive being pasted into a query string.
            self.assertNotIn("+", token)
            self.assertNotIn("/", token)
            self.assertNotIn("=", token)

    def test_token_is_long_enough_to_be_unguessable(self):
        # 32 bytes base64url-encoded ≈ 43 characters.
        self.assertGreaterEqual(len(generate_token()), 40)

    def test_hash_is_a_sha256_hex_digest_and_not_the_token(self):
        token = generate_token()
        digest = hash_token(token)
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, token)
        self.assertNotIn(token, digest)

    def test_hashing_is_deterministic(self):
        token = generate_token()
        self.assertEqual(hash_token(token), hash_token(token))

    def test_different_tokens_hash_differently(self):
        self.assertNotEqual(hash_token(generate_token()), hash_token(generate_token()))

    def test_tokens_equal_compares_correctly(self):
        digest = hash_token("abc")
        self.assertTrue(tokens_equal(digest, hash_token("abc")))
        self.assertFalse(tokens_equal(digest, hash_token("abd")))
        self.assertFalse(tokens_equal(digest, None))
        self.assertFalse(tokens_equal(None, digest))


class PasswordHashingTests(SimpleTestCase):
    def test_hash_does_not_contain_the_password(self):
        hashed = hash_password("Vaulted-Harbour-92!")
        self.assertNotIn("Vaulted-Harbour-92!", hashed)

    def test_same_password_hashes_differently_each_time(self):
        """Per-hash salt: two users with the same password must not collide."""
        a = hash_password("Vaulted-Harbour-92!")
        b = hash_password("Vaulted-Harbour-92!")
        self.assertNotEqual(a, b)
        self.assertTrue(verify_password("Vaulted-Harbour-92!", a))
        self.assertTrue(verify_password("Vaulted-Harbour-92!", b))

    def test_uses_a_recognised_strong_algorithm(self):
        algorithm = hash_password("Vaulted-Harbour-92!").split("$")[0]
        self.assertIn(algorithm, {"argon2", "pbkdf2_sha256", "bcrypt_sha256"})

    def test_wrong_password_is_rejected(self):
        hashed = hash_password("Vaulted-Harbour-92!")
        for wrong in ("", "vaulted-harbour-92!", "Vaulted-Harbour-92", "x"):
            self.assertFalse(verify_password(wrong, hashed), wrong)

    def test_no_password_set_means_no_password_works(self):
        """An invited-but-not-onboarded account must never authenticate."""
        for candidate in ("", "anything", "None"):
            self.assertFalse(verify_password(candidate, ""))


class PasswordPolicyEdgeTests(SimpleTestCase):
    def test_minimum_length_boundary(self):
        just_under = "Ab1!" + "x" * (MIN_PASSWORD_LENGTH - 5)
        just_over = "Ab1!" + "x" * (MIN_PASSWORD_LENGTH - 4)
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength(just_under)
        self.assertTrue(validate_password_strength(just_over))

    def test_absurdly_long_password_is_refused(self):
        """Bounds the work handed to the KDF."""
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("a1B!" * 40)  # 160 chars

    def test_non_string_input_is_refused_not_crashed(self):
        for value in (None, 12345678, [], {}):
            with self.assertRaises(PasswordPolicyError):
                validate_password_strength(value)

    def test_name_check_is_case_insensitive(self):
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("XxSMITHxx-2026", display_name="John Smith")

    def test_short_name_fragments_do_not_block_a_good_password(self):
        """"Jo" is too short to be a meaningful match — see the >= 4 guard."""
        self.assertTrue(
            validate_password_strength("Vaulted-Harbour-92!", display_name="Jo Ng")
        )

    def test_email_check_ignores_the_domain(self):
        """Only the local part is checked; everyone shares the domain."""
        self.assertTrue(
            validate_password_strength("Vaulted-Harbour-92!", email="pat@kbc.test")
        )

    def test_password_equal_to_email_local_part_is_refused(self):
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("jsmith-jsmith", email="jsmith@kbc.test")


class LockoutScheduleTests(SimpleTestCase):
    def test_no_lock_below_the_threshold(self):
        for attempts in range(0, LOCKOUT_THRESHOLD):
            self.assertEqual(lock_duration_for(attempts), 0, attempts)

    def test_first_lock_at_the_threshold(self):
        self.assertEqual(lock_duration_for(LOCKOUT_THRESHOLD), LOCKOUT_BASE_MINUTES)

    def test_backoff_grows_with_further_failures(self):
        first = lock_duration_for(LOCKOUT_THRESHOLD)
        second = lock_duration_for(LOCKOUT_THRESHOLD + 1)
        self.assertGreater(second, first)

    def test_backoff_is_capped(self):
        """An attacker cannot lock an account out for a week."""
        self.assertEqual(lock_duration_for(LOCKOUT_THRESHOLD + 10_000), LOCKOUT_MAX_MINUTES)

    def test_schedule_is_monotonic(self):
        durations = [lock_duration_for(n) for n in range(0, 40)]
        self.assertEqual(durations, sorted(durations))


class LifetimeTests(SimpleTestCase):
    """The TTLs are security parameters; pin their relative ordering."""

    def test_reset_is_the_shortest_lived_token(self):
        self.assertLess(RESET_TTL, INVITATION_TTL)
        self.assertLessEqual(RESET_TTL, timedelta(hours=24))

    def test_remember_me_extends_but_does_not_remove_expiry(self):
        self.assertGreater(SESSION_TTL_REMEMBER, SESSION_TTL)
        self.assertLess(SESSION_TTL_REMEMBER, timedelta(days=400))

    def test_every_rolling_window_sits_under_its_own_ceiling(self):
        """A window at or above its ceiling would make the ceiling unreachable."""
        from .security import SESSION_MAX_LIFETIME, SESSION_MAX_LIFETIME_REMEMBER

        self.assertLess(SESSION_TTL, SESSION_MAX_LIFETIME)
        self.assertLess(SESSION_TTL_REMEMBER, SESSION_MAX_LIFETIME_REMEMBER)

    def test_no_session_may_live_indefinitely(self):
        from .security import SESSION_MAX_LIFETIME, SESSION_MAX_LIFETIME_REMEMBER

        self.assertLessEqual(SESSION_MAX_LIFETIME, timedelta(days=30))
        self.assertLessEqual(SESSION_MAX_LIFETIME_REMEMBER, timedelta(days=180))

    def test_session_policy_maps_the_choice_to_both_clocks(self):
        from .security import (
            SESSION_MAX_LIFETIME,
            SESSION_MAX_LIFETIME_REMEMBER,
            session_policy,
        )

        self.assertEqual(session_policy(False), (SESSION_TTL, SESSION_MAX_LIFETIME))
        self.assertEqual(
            session_policy(True), (SESSION_TTL_REMEMBER, SESSION_MAX_LIFETIME_REMEMBER)
        )

    def test_invitation_window_is_workable_but_finite(self):
        self.assertGreaterEqual(INVITATION_TTL, timedelta(days=1))
        self.assertLessEqual(INVITATION_TTL, timedelta(days=30))


class ClientIpTests(SimpleTestCase):
    """X-Forwarded-For feeds throttling, so it must not be believed by default."""

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, **meta):
        request = self.factory.get("/")
        request.META.update(meta)
        return request

    @override_settings(TRUST_PROXY_IP_HEADER=False)
    def test_forwarded_header_is_ignored_by_default(self):
        request = self._request(
            HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.1"
        )
        self.assertEqual(client_ip(request), "10.0.0.1")

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_forwarded_header_is_used_when_explicitly_trusted(self):
        request = self._request(
            HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8", REMOTE_ADDR="10.0.0.1"
        )
        self.assertEqual(client_ip(request), "1.2.3.4")

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_malformed_forwarded_header_falls_back_to_remote_addr(self):
        """A spoofed header must not become the throttle key."""
        request = self._request(
            HTTP_X_FORWARDED_FOR="not-an-ip", REMOTE_ADDR="10.0.0.1"
        )
        self.assertEqual(client_ip(request), "10.0.0.1")

    def test_missing_remote_addr_yields_none(self):
        request = self._request()
        # RequestFactory always supplies 127.0.0.1; drop it to model a request
        # that arrived with no usable source address.
        request.META.pop("REMOTE_ADDR", None)
        self.assertIsNone(client_ip(request))

    def test_user_agent_is_truncated(self):
        request = self._request(HTTP_USER_AGENT="A" * 5000)
        self.assertLessEqual(len(user_agent(request)), 400)

    def test_absent_user_agent_is_none(self):
        self.assertIsNone(user_agent(self._request()))


# ---------------------------------------------------------------------------
# Role mapping and payload shape — no database
# ---------------------------------------------------------------------------

class RoleMappingTests(SimpleTestCase):
    def test_only_super_admin_access_grants_admin(self):
        self.assertEqual(identity.role_for_staff("Admin", "super-admin"), ROLE_ADMIN)
        for access in ("enrolment", "curriculum", "coach"):
            self.assertEqual(identity.role_for_staff("Admin", access), ROLE_STAFF, access)

    def test_access_match_is_case_and_whitespace_insensitive(self):
        for spelling in ("super-admin", "SUPER-ADMIN", "  Super-Admin  "):
            self.assertEqual(identity.role_for_staff("Caseowner", spelling), ROLE_ADMIN, spelling)

    def test_position_alone_never_grants_admin(self):
        """The heart of the access model: every account the console creates is
        Position='Admin', so a Position-based grant would make all of them
        platform administrators. Only an explicit access does that."""
        for position in ("Admin", "admin", "  Admin  ", "Caseowner", None, ""):
            self.assertEqual(identity.role_for_staff(position), ROLE_STAFF, repr(position))

    def test_missing_access_is_staff_not_admin(self):
        """Fail closed: an unset Access must never mean administrator."""
        for value in (None, "", "   ", "wizard"):
            self.assertEqual(identity.role_for_staff("Admin", value), ROLE_STAFF, repr(value))

    def test_admin_holds_every_other_role_permission(self):
        admin = set(identity.permissions_for(ROLE_ADMIN))
        for role in (ROLE_STAFF, ROLE_EMPLOYER, ROLE_LEARNER):
            # Employer/learner permissions are their own; only check staff is a
            # subset, which is the escalation path that matters.
            if role == ROLE_STAFF:
                self.assertTrue(set(identity.permissions_for(role)).issubset(admin))

    def test_learner_and_employer_cannot_manage_accounts(self):
        for role in (ROLE_LEARNER, ROLE_EMPLOYER, ROLE_STAFF):
            self.assertNotIn("accounts.manage", identity.permissions_for(role), role)
        self.assertIn("accounts.manage", identity.permissions_for(ROLE_ADMIN))

    def test_unknown_role_gets_no_permissions(self):
        self.assertEqual(identity.permissions_for("wizard"), [])

    def test_permissions_list_is_a_copy(self):
        """A caller mutating the result must not edit the shared table."""
        first = identity.permissions_for(ROLE_ADMIN)
        first.append("everything")
        self.assertNotIn("everything", identity.permissions_for(ROLE_ADMIN))


class InviteAuthorisationTests(SimpleTestCase):
    """The rules that closed the privilege-escalation hole. See services._authorise."""

    def _account(self, role, is_active=True):
        return LoginAccount(
            email="someone@kbc.test", role=role, is_active=is_active,
            subject_type="staff", subject_id=1,
        )

    def test_anonymous_cannot_invite_anyone(self):
        for target in (ROLE_LEARNER, ROLE_EMPLOYER, ROLE_STAFF, ROLE_ADMIN):
            with self.assertRaises(InvitePermissionError):
                _authorise(None, target)

    def test_learner_and_employer_cannot_invite(self):
        for role in (ROLE_LEARNER, ROLE_EMPLOYER):
            with self.assertRaises(InvitePermissionError):
                _authorise(self._account(role), ROLE_LEARNER)

    def test_inactive_staff_cannot_invite(self):
        with self.assertRaises(InvitePermissionError):
            _authorise(self._account(ROLE_STAFF, is_active=False), ROLE_LEARNER)

    def test_staff_may_invite_non_admins(self):
        for target in (ROLE_LEARNER, ROLE_EMPLOYER, ROLE_STAFF):
            self.assertTrue(_authorise(self._account(ROLE_STAFF), target), target)

    def test_staff_may_not_invite_an_admin(self):
        """The self-promotion path: staff creating an "Admin" colleague."""
        with self.assertRaises(InvitePermissionError):
            _authorise(self._account(ROLE_STAFF), ROLE_ADMIN)

    def test_admin_may_invite_anyone(self):
        for target in (ROLE_LEARNER, ROLE_EMPLOYER, ROLE_STAFF, ROLE_ADMIN):
            self.assertTrue(_authorise(self._account(ROLE_ADMIN), target), target)


# ---------------------------------------------------------------------------
# Email transport — no network, no database
# ---------------------------------------------------------------------------

_MAIL_ENV = {
    "AZURE_MAIL_TENANT_ID": "tenant-id",
    "AZURE_MAIL_CLIENT_ID": "client-id",
    "AZURE_MAIL_CLIENT_SECRET": "secret-value",
    "AZURE_MAIL_SENDER": "noreply@kbc.test",
    "AZURE_MAIL_ENABLED": "true",
}

#: The alternate spellings each setting also accepts. Blanked in tests that
#: assert a setting is *missing* — this project's real .env defines them, and
#: os.environ is not cleared, so an unblanked alternate silently satisfies the
#: setting under test.
_BLANKED_ALTERNATES = {
    "AZURE_LOGIN_APP_TENANT_ID": "",
    "AZURE_LOGIN_APP_CLIENT_ID": "",
    "AZURE_LOGIN_APP_CLIENT_SECRET": "",
    "AZURE_EMAIL": "",
}

#: Azure genuinely absent. Blanking AZURE_MAIL_SENDER alone is NOT enough:
#: _SETTING_SOURCES also accepts AZURE_EMAIL, this project's real .env defines
#: it, and os.environ is never cleared — so is_configured() stayed True and the
#: "unconfigured" tests below made live Graph sendMail calls.
_MAIL_ABSENT = dict(_BLANKED_ALTERNATES, AZURE_MAIL_SENDER="")


class MailConfigurationTests(SimpleTestCase):
    def test_fully_configured_is_reported_ready(self):
        with mock.patch.dict("os.environ", _MAIL_ENV, clear=False):
            self.assertTrue(email_azure.is_configured())
            self.assertEqual(email_azure.missing_settings(), [])

    def test_each_missing_setting_is_named(self):
        # Every setting accepts a second spelling (see _SETTING_SOURCES), and
        # this project's .env defines those — blank them too, or the alternate
        # masks the missing setting and the assertion passes for the wrong
        # reason.
        for key in _MAIL_ENV:
            if key == "AZURE_MAIL_ENABLED":
                continue
            env = dict(_MAIL_ENV, **_BLANKED_ALTERNATES, **{key: ""})
            with mock.patch.dict("os.environ", env, clear=False):
                self.assertFalse(email_azure.is_configured(), key)
                # The report names both accepted spellings for the setting.
                self.assertTrue(
                    any(key in entry for entry in email_azure.missing_settings()), key
                )

    def test_accepts_the_azure_login_app_spelling(self):
        """This deployment's .env names the mail app AZURE_LOGIN_APP_* and its
        sender AZURE_EMAIL. Both must resolve without the AZURE_MAIL_* names."""
        env = {
            "AZURE_MAIL_TENANT_ID": "", "AZURE_MAIL_CLIENT_ID": "",
            "AZURE_MAIL_CLIENT_SECRET": "", "AZURE_MAIL_SENDER": "",
            "AZURE_LOGIN_APP_TENANT_ID": "login-tenant",
            "AZURE_LOGIN_APP_CLIENT_ID": "login-client",
            "AZURE_LOGIN_APP_CLIENT_SECRET": "login-secret",
            "AZURE_EMAIL": "lms@kbc.test",
            "AZURE_MAIL_ENABLED": "true",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            config = email_azure.mail_config()
            self.assertTrue(email_azure.is_configured())
        self.assertEqual(config["tenant_id"], "login-tenant")
        self.assertEqual(config["client_id"], "login-client")
        self.assertEqual(config["client_secret"], "login-secret")
        self.assertEqual(config["sender"], "lms@kbc.test")

    def test_azure_mail_names_win_over_the_login_app_names(self):
        """AZURE_MAIL_* is the documented primary; the alternate is a fallback."""
        env = dict(
            _MAIL_ENV,
            AZURE_LOGIN_APP_TENANT_ID="ignored",
            AZURE_LOGIN_APP_CLIENT_ID="ignored",
            AZURE_LOGIN_APP_CLIENT_SECRET="ignored",
            AZURE_EMAIL="ignored@kbc.test",
        )
        with mock.patch.dict("os.environ", env, clear=False):
            config = email_azure.mail_config()
        self.assertEqual(config["tenant_id"], "tenant-id")
        self.assertEqual(config["sender"], "noreply@kbc.test")

    def test_never_falls_back_to_the_calendar_app_credentials(self):
        """MICROSOFT_* belongs to the calendar app, which has no Mail.Send.
        Using it would report configured and then 403 on every send."""
        env = {
            "AZURE_MAIL_TENANT_ID": "", "AZURE_MAIL_CLIENT_ID": "",
            "AZURE_MAIL_CLIENT_SECRET": "", "AZURE_MAIL_SENDER": "",
            "AZURE_LOGIN_APP_TENANT_ID": "", "AZURE_LOGIN_APP_CLIENT_ID": "",
            "AZURE_LOGIN_APP_CLIENT_SECRET": "", "AZURE_EMAIL": "",
            "MICROSOFT_TENANT_ID": "calendar-tenant",
            "MICROSOFT_CLIENT_ID": "calendar-client",
            "MICROSOFT_CLIENT_SECRET": "calendar-secret",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            config = email_azure.mail_config()
            self.assertFalse(email_azure.is_configured())
        self.assertEqual(config["tenant_id"], "")
        self.assertEqual(config["client_id"], "")

    def test_sender_has_no_implicit_default(self):
        """There is no sensible default mailbox to send as."""
        env = dict(_MAIL_ENV, AZURE_MAIL_SENDER="", AZURE_EMAIL="")
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertEqual(email_azure.mail_config()["sender"], "")
            self.assertTrue(
                any("AZURE_MAIL_SENDER" in e for e in email_azure.missing_settings())
            )

    def test_explicitly_disabled_reports_not_configured(self):
        env = dict(_MAIL_ENV, AZURE_MAIL_ENABLED="false")
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertFalse(email_azure.is_configured())

    def test_missing_settings_reports_names_only_never_values(self):
        """The health endpoint publishes this list — it must leak no secrets."""
        env = dict(_MAIL_ENV, AZURE_MAIL_CLIENT_SECRET="")
        with mock.patch.dict("os.environ", env, clear=False):
            reported = " ".join(email_azure.missing_settings())
        self.assertNotIn("secret-value", reported)
        self.assertNotIn("tenant-id", reported)


class MailFallbackTests(SimpleTestCase):
    """When Azure is absent, a live token must not reach production logs."""

    _LINK = "https://host/set-password?token=LIVE-TOKEN-VALUE"

    def _send(self):
        return email_azure.send_mail(
            to="person@kbc.test", subject="Subject",
            html_body="<b>x</b>", text_body=self._LINK,
        )

    def _send_without_azure(self):
        """Send with Azure absent — and prove nothing reached the network.

        The refusing httpx.post is the point of this helper. These tests used
        to blank AZURE_MAIL_SENDER only, AZURE_EMAIL kept the config valid, and
        the sends went to Graph for real. A transport that fails when touched
        turns that from a silent side effect into a red test.
        """
        with mock.patch.dict("os.environ", _MAIL_ABSENT, clear=False):
            with mock.patch("httpx.post", side_effect=AssertionError(
                "send_mail reached the network while Azure was meant to be absent"
            )) as posted:
                result = self._send()
        self.assertFalse(posted.called, "a live Graph call was attempted")
        return result

    def test_unconfigured_send_reports_failure_with_a_reason(self):
        sent, detail = self._send_without_azure()
        self.assertFalse(sent)
        self.assertIn("not-configured", detail)

    @override_settings(DEBUG=True)
    def test_link_is_logged_in_development(self):
        with self.assertLogs("login.email", level="WARNING") as captured:
            self._send_without_azure()
        self.assertIn("LIVE-TOKEN-VALUE", "\n".join(captured.output))

    @override_settings(DEBUG=False)
    def test_link_is_never_logged_in_production(self):
        with self.assertLogs("login.email", level="ERROR") as captured:
            self._send_without_azure()
        output = "\n".join(captured.output)
        self.assertNotIn("LIVE-TOKEN-VALUE", output)
        # Still says something actionable, so the misconfiguration is visible.
        self.assertIn("not configured", output)

    def test_graph_failure_is_reported_not_raised(self):
        """A mail outage must not turn a learner's creation into a 500."""
        with mock.patch.dict("os.environ", _MAIL_ENV, clear=False):
            with mock.patch.object(email_azure, "_access_token", return_value="tok"):
                response = mock.Mock(status_code=503, text="upstream unavailable")
                with mock.patch("httpx.post", return_value=response):
                    sent, detail = self._send()
        self.assertFalse(sent)
        self.assertIn("503", detail)

    def test_graph_202_is_treated_as_sent(self):
        """202 Accepted is sendMail's documented success."""
        with mock.patch.dict("os.environ", _MAIL_ENV, clear=False):
            with mock.patch.object(email_azure, "_access_token", return_value="tok"):
                response = mock.Mock(status_code=202, text="")
                with mock.patch("httpx.post", return_value=response) as posted:
                    sent, detail = self._send()
        self.assertTrue(sent)
        self.assertIsNone(detail)
        # Sends as the configured mailbox, and does not fill Sent Items.
        url, kwargs = posted.call_args[0][0], posted.call_args[1]
        self.assertIn("noreply@kbc.test", url)
        self.assertFalse(kwargs["json"]["saveToSentItems"])

    def test_save_to_sent_opt_in_reaches_graph(self):
        with mock.patch.dict("os.environ", _MAIL_ENV, clear=False):
            with mock.patch.object(email_azure, "_access_token", return_value="tok"):
                response = mock.Mock(status_code=202, text="")
                with mock.patch("httpx.post", return_value=response) as posted:
                    email_azure.send_mail(
                        to="person@kbc.test", subject="Subject",
                        html_body="<b>x</b>", save_to_sent=True,
                    )
        self.assertTrue(posted.call_args[1]["json"]["saveToSentItems"])

    def test_inline_attachment_reaches_graph_with_content_id(self):
        with mock.patch.dict("os.environ", _MAIL_ENV, clear=False):
            with mock.patch.object(email_azure, "_access_token", return_value="tok"):
                response = mock.Mock(status_code=202, text="")
                with mock.patch("httpx.post", return_value=response) as posted:
                    email_azure.send_mail(
                        to="person@kbc.test", subject="Subject", html_body='<img src="cid:brand-logo">',
                        attachments=[{
                            "name": "logo.png", "content_type": "image/png", "content": b"png",
                            "content_id": "brand-logo", "is_inline": True,
                        }],
                    )
        attachment = posted.call_args[1]["json"]["message"]["attachments"][0]
        self.assertEqual(attachment["contentId"], "brand-logo")
        self.assertTrue(attachment["isInline"])
        self.assertEqual(attachment["contentBytes"], "cG5n")

    def test_network_error_is_caught(self):
        import httpx

        with mock.patch.dict("os.environ", _MAIL_ENV, clear=False):
            with mock.patch.object(email_azure, "_access_token", return_value="tok"):
                with mock.patch("httpx.post", side_effect=httpx.ConnectError("down")):
                    sent, detail = self._send()
        self.assertFalse(sent)
        self.assertTrue(detail)


class MessageTemplateTests(SimpleTestCase):
    def test_invitation_message_carries_the_link(self):
        subject, html, text = email_azure.invitation_message(
            display_name="Pat Jones", link="https://host/set-password?token=T", expires_days=7
        )
        self.assertTrue(subject)
        for body in (html, text):
            self.assertIn("https://host/set-password?token=T", body)
        self.assertIn("Pat Jones", html)
        self.assertIn("7", text)

    def test_reset_message_carries_the_link(self):
        subject, html, text = email_azure.reset_message(
            display_name=None, link="https://host/reset-password?token=T", expires_hours=1
        )
        self.assertTrue(subject)
        for body in (html, text):
            self.assertIn("https://host/reset-password?token=T", body)
        # No name available — must still greet without rendering "None".
        self.assertNotIn("None", html)

    def test_templates_are_self_contained_html(self):
        """Mail clients strip <style> blocks and block remote assets."""
        _, html, _ = email_azure.invitation_message(
            display_name="A", link="https://h/x", expires_days=7
        )
        self.assertNotIn("<style", html.lower())
        self.assertNotIn("<script", html.lower())


class BrandedTutorMailTests(SimpleTestCase):
    """The tutor assignment mail carries the crest and keeps its facts."""

    module = {
        "name": "Martech Foundations", "code": "MOD-123", "programme": "Data Analyst",
        "cohort": "Sept 2026", "group": "Group A", "schedule": "Wednesday 09:00-11:00",
        "dates": "2026-09-02 to 2026-10-07", "sessions": "6", "otjh": "12 hours",
        "coach": "Sam Coach",
    }

    def build(self, modules=None, tutor_name="Osama Kord"):
        return email_azure.tutor_assignment_message(
            tutor_name=tutor_name, modules=modules or [self.module],
            workspace_url="https://lms.example/workspace/tutor",
        )

    def test_the_logo_is_referenced_by_cid_not_by_a_url(self):
        """A linked image depends on a host the reader's client may not reach."""
        _subject, html, _text = self.build()
        self.assertIn(f'src="cid:{email_azure.BRAND_LOGO_CID}"', html)
        # No remote asset anywhere: mail clients block those until asked.
        self.assertNotIn('src="http', html)
        # Images off still names the college.
        self.assertIn('alt="Kent Business College"', html)

    def test_the_logo_travels_with_the_message_as_an_inline_part(self):
        attachment = email_azure.brand_logo_attachment()
        self.assertIsNotNone(attachment)
        self.assertEqual(attachment["content_id"], email_azure.BRAND_LOGO_CID)
        self.assertEqual(attachment["content_type"], "image/png")
        # PNG magic, without spelling the non-ASCII first byte.
        self.assertEqual(attachment["content"][1:4], b"PNG")

        part = email_azure._file_attachment(attachment)
        # Inline, so it renders in the body rather than hanging off the mail as
        # a download.
        self.assertTrue(part["isInline"])
        self.assertEqual(part["contentId"], email_azure.BRAND_LOGO_CID)

    def test_an_ordinary_attachment_is_not_made_inline(self):
        part = email_azure._file_attachment({
            "name": "invite.ics", "content_type": "text/calendar", "content": b"BEGIN:VCALENDAR",
        })
        self.assertNotIn("isInline", part)
        self.assertNotIn("contentId", part)

    def test_the_facts_survive_the_redesign(self):
        """Styling may change; what the tutor needs to know may not."""
        _subject, html, text = self.build()
        for fact in ("Martech Foundations", "Group A", "Wednesday 09:00-11:00",
                     "2026-09-02 to 2026-10-07", "Sam Coach"):
            self.assertIn(fact, html)
            self.assertIn(fact, text)
        self.assertIn("https://lms.example/workspace/tutor", html)
        self.assertIn("https://lms.example/workspace/tutor", text)

    def test_the_inbox_preview_line_says_something_the_subject_does_not(self):
        _subject, html, _text = self.build()
        # Gmail would otherwise preview the greeting.
        self.assertIn("Wednesday 09:00-11:00", html.split("</div>")[0])

    def test_it_stays_self_contained_html(self):
        _subject, html, _text = self.build()
        self.assertNotIn("<style", html.lower())
        self.assertNotIn("<script", html.lower())

    def test_several_modules_are_one_message_with_one_card_each(self):
        _subject, html, _text = self.build(modules=[self.module, dict(self.module, name="Second")])
        self.assertIn("You are now teaching 2 modules", html)
        self.assertIn("Second", html)


class LinkBuildingTests(SimpleTestCase):
    def test_links_point_at_the_configured_frontend(self):
        with mock.patch.dict("os.environ", {"FRONTEND_URL": "https://lms.example.net"}):
            self.assertTrue(
                invitations.invitation_link("TOK").startswith(
                    "https://lms.example.net/set-password?token="
                )
            )
            self.assertTrue(
                invitations.reset_link("TOK").startswith(
                    "https://lms.example.net/reset-password?token="
                )
            )

    def test_trailing_slash_does_not_produce_a_double_slash(self):
        with mock.patch.dict("os.environ", {"FRONTEND_URL": "https://lms.example.net/"}):
            self.assertNotIn("//set-password", invitations.invitation_link("T"))

    def test_the_two_flows_use_different_paths(self):
        """A reset token pasted into the invitation page must not resolve."""
        with mock.patch.dict("os.environ", {"FRONTEND_URL": "https://h"}):
            self.assertNotEqual(invitations.invitation_link("T"), invitations.reset_link("T"))


# ---------------------------------------------------------------------------
# Sessions — needs the database, but only the two session tables
# ---------------------------------------------------------------------------

class ExpiryReasonTests(SimpleTestCase):
    """Telling the two clocks apart.

    This is the number ``session_stats`` reports and the one that would justify
    changing the lifetimes, so it is worth pinning rather than trusting: an idle
    expiry is the system working, a ceiling expiry signs somebody out mid-task.
    """

    @staticmethod
    def _session(*, age, expires_in, remember=False):
        """An unsaved row -- nothing here needs the database."""
        now = timezone.now()
        return LoginSession(
            created_at=now - age, expires_at=now + expires_in, remember=remember
        )

    def test_an_idle_expiry_is_named_as_one(self):
        from .sessions import expiry_reason

        # Signed in an hour ago and expired ten minutes ago: nowhere near the
        # seven-day ceiling, so this is somebody who simply stopped working.
        session = self._session(
            age=timedelta(hours=1), expires_in=-timedelta(minutes=10)
        )

        self.assertEqual(expiry_reason(session), "expired_idle")

    def test_a_ceiling_expiry_is_named_as_one(self):
        from .security import SESSION_MAX_LIFETIME
        from .sessions import expiry_reason

        session = self._session(age=SESSION_MAX_LIFETIME, expires_in=timedelta(0))

        self.assertEqual(expiry_reason(session), "expired_ceiling")

    def test_each_kind_is_measured_against_its_own_ceiling(self):
        """Why the policy is read from the row and not assumed.

        One identical row, two answers. A fortnight-old remembered session has
        used two weeks of its ninety-day maximum and merely went idle; read as
        an ordinary session it would look like it had blown through a seven-day
        ceiling, and the ceiling count -- the whole point of the measurement --
        would be wrong.
        """
        from .sessions import expiry_reason

        session = self._session(
            age=timedelta(days=14), expires_in=-timedelta(minutes=1), remember=True
        )
        self.assertEqual(expiry_reason(session), "expired_idle")

        session.remember = False
        self.assertEqual(expiry_reason(session), "expired_ceiling")


class SessionLifecycleTests(TestCase):
    databases = {"default", "enrolment"}

    def setUp(self):
        super().setUp()
        self.account = LoginAccount.objects.create(
            subject_type="staff",
            subject_id=900_000 + (uuid.uuid4().int % 90_000),
            email=f"sess-{uuid.uuid4().hex[:10]}@kbc.invalid",
            display_name="Session Tester",
            role=ROLE_STAFF,
            password_hash=hash_password("Vaulted-Harbour-92!"),
        )

    def tearDown(self):
        LoginSession.objects.filter(account=self.account).delete()
        LoginAccount.objects.filter(pk=self.account.pk).delete()
        super().tearDown()

    def test_issue_stores_only_the_hash(self):
        from .sessions import issue_session

        token, session, ttl = issue_session(self.account)
        self.assertEqual(session.token_hash, hash_token(token))
        self.assertNotEqual(session.token_hash, token)
        self.assertEqual(ttl, SESSION_TTL)

    def test_remember_me_lengthens_the_session(self):
        from .sessions import issue_session

        _, plain, _ = issue_session(self.account, remember=False)
        _, remembered, _ = issue_session(self.account, remember=True)
        self.assertGreater(remembered.expires_at, plain.expires_at)

    def test_resolve_returns_the_session_for_a_valid_token(self):
        from .sessions import issue_session, resolve_session

        token, session, _ = issue_session(self.account)
        resolved = resolve_session(token)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.pk, session.pk)

    def test_resolve_rejects_unknown_empty_and_expired_tokens(self):
        from .sessions import issue_session, resolve_session

        self.assertIsNone(resolve_session(None))
        self.assertIsNone(resolve_session(""))
        self.assertIsNone(resolve_session(generate_token()))

        token, session, _ = issue_session(self.account)
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["expires_at"])
        self.assertIsNone(resolve_session(token))

    def test_revoked_session_stops_resolving(self):
        from .sessions import issue_session, resolve_session, revoke_session

        token, session, _ = issue_session(self.account)
        revoke_session(session)
        self.assertIsNone(resolve_session(token))

    def test_deactivating_the_account_kills_live_sessions(self):
        """No wait for expiry — a disabled account loses access immediately."""
        from .sessions import issue_session, resolve_session

        token, _, _ = issue_session(self.account)
        self.account.is_active = False
        self.account.save(update_fields=["is_active"])
        self.assertIsNone(resolve_session(token))

    def test_revoke_all_ends_every_session(self):
        from .sessions import issue_session, resolve_session, revoke_all_for_account

        tokens = [issue_session(self.account)[0] for _ in range(3)]
        revoke_all_for_account(self.account)
        for token in tokens:
            self.assertIsNone(resolve_session(token))

    def test_revoke_all_can_spare_the_current_session(self):
        """Used by change-password: sign out elsewhere, stay signed in here."""
        from .sessions import issue_session, resolve_session, revoke_all_for_account

        keep_token, keep, _ = issue_session(self.account)
        other_token, _, _ = issue_session(self.account)

        revoke_all_for_account(self.account, except_session_id=keep.pk)

        self.assertIsNotNone(resolve_session(keep_token))
        self.assertIsNone(resolve_session(other_token))

    def test_sessions_of_other_accounts_are_untouched(self):
        from .sessions import issue_session, resolve_session, revoke_all_for_account

        other = LoginAccount.objects.create(
            subject_type="staff",
            subject_id=800_000 + (uuid.uuid4().int % 90_000),
            email=f"other-{uuid.uuid4().hex[:10]}@kbc.invalid",
            role=ROLE_STAFF,
        )
        try:
            other_token, _, _ = issue_session(other)
            revoke_all_for_account(self.account)
            self.assertIsNotNone(resolve_session(other_token))
        finally:
            LoginSession.objects.filter(account=other).delete()
            LoginAccount.objects.filter(pk=other.pk).delete()


class RollingRenewalTests(TestCase):
    """Rolling expiry: what ``touch_session`` may and may not move.

    Time is controlled through the two columns the renewal reads rather than by
    patching the clock — ``Last_seen_at`` drives the throttle and ``Created_at``
    anchors the ceiling, so ageing a session is a matter of writing them. Both
    go through ``queryset.update`` because ``Created_at`` is ``auto_now_add``
    and would otherwise be silently rewritten to now.
    """

    databases = {"default", "enrolment"}

    def setUp(self):
        super().setUp()
        self.account = LoginAccount.objects.create(
            subject_type="staff",
            subject_id=800_000 + (uuid.uuid4().int % 90_000),
            email=f"roll-{uuid.uuid4().hex[:10]}@kbc.invalid",
            display_name="Rolling Tester",
            role=ROLE_STAFF,
            password_hash=hash_password("Vaulted-Harbour-92!"),
        )

    def tearDown(self):
        LoginSession.objects.filter(account=self.account).delete()
        LoginAccount.objects.filter(pk=self.account.pk).delete()
        super().tearDown()

    def _session(self, *, remember=False, age=None, idle=None):
        """A session, optionally aged and made due for a touch.

        ``age`` backdates ``Created_at``, moving the session towards its
        ceiling. It also pulls ``Expires_at`` in to a minute away, because that
        is the only shape an old session can really have: one that has been
        rolling forward all along, currently near the end of its window. Leaving
        a full freshly-issued window on a week-old row would describe a session
        already past its own ceiling, which nothing can produce.

        ``idle`` backdates ``Last_seen_at``, making the session due a touch.
        """
        from .sessions import issue_session

        _, session, _ = issue_session(self.account, remember=remember)
        fields = {}
        if age is not None:
            fields["created_at"] = timezone.now() - age
            fields["expires_at"] = timezone.now() + timedelta(minutes=1)
        if idle is not None:
            fields["last_seen_at"] = timezone.now() - idle
        if fields:
            LoginSession.objects.filter(pk=session.pk).update(**fields)
            session.refresh_from_db()
        return session

    #: Comfortably past the 300-second touch throttle.
    DUE = timedelta(minutes=10)

    # --- the persisted choice ------------------------------------------------

    def test_a_normal_session_is_stored_as_not_remembered(self):
        session = self._session()
        self.assertFalse(session.remember)
        self.assertFalse(LoginSession.objects.get(pk=session.pk).remember)

    def test_a_remember_me_session_is_stored_as_remembered(self):
        session = self._session(remember=True)
        self.assertTrue(session.remember)
        self.assertTrue(LoginSession.objects.get(pk=session.pk).remember)

    def test_the_initial_expiry_matches_the_rolling_window(self):
        from .security import SESSION_TTL, SESSION_TTL_REMEMBER

        normal = self._session()
        remembered = self._session(remember=True)

        self.assertAlmostEqual(
            (normal.expires_at - normal.created_at).total_seconds(),
            SESSION_TTL.total_seconds(),
            delta=30,
        )
        self.assertAlmostEqual(
            (remembered.expires_at - remembered.created_at).total_seconds(),
            SESSION_TTL_REMEMBER.total_seconds(),
            delta=30,
        )

    # --- renewal -------------------------------------------------------------

    def test_an_active_normal_session_renews_its_expiry(self):
        from .sessions import touch_session

        session = self._session(idle=self.DUE)
        before = session.expires_at

        renewed = touch_session(session)

        self.assertIsNotNone(renewed)
        self.assertGreater(renewed, before)
        session.refresh_from_db()
        self.assertEqual(session.expires_at, renewed)

    def test_an_active_remember_me_session_renews_its_expiry(self):
        from .sessions import touch_session

        session = self._session(remember=True, idle=self.DUE)
        before = session.expires_at

        renewed = touch_session(session)

        self.assertIsNotNone(renewed)
        self.assertGreater(renewed, before)

    def test_renewal_uses_the_policy_the_session_was_issued_under(self):
        """The whole reason the column exists: the two kinds renew differently."""
        from .security import SESSION_TTL, SESSION_TTL_REMEMBER
        from .sessions import touch_session

        normal = touch_session(self._session(idle=self.DUE))
        remembered = touch_session(self._session(remember=True, idle=self.DUE))
        now = timezone.now()

        self.assertAlmostEqual(
            (normal - now).total_seconds(), SESSION_TTL.total_seconds(), delta=60
        )
        self.assertAlmostEqual(
            (remembered - now).total_seconds(),
            SESSION_TTL_REMEMBER.total_seconds(),
            delta=60,
        )

    def test_renewal_does_not_move_created_at(self):
        """``Created_at`` anchors the ceiling; rewriting it would remove it."""
        from .sessions import touch_session

        session = self._session(age=timedelta(days=2), idle=self.DUE)
        created = session.created_at

        touch_session(session)

        session.refresh_from_db()
        self.assertEqual(session.created_at, created)

    # --- the throttle --------------------------------------------------------

    def test_a_recently_touched_session_is_not_written_again(self):
        from .sessions import touch_session

        session = self._session(idle=timedelta(seconds=30))
        before = session.expires_at

        self.assertIsNone(touch_session(session))

        session.refresh_from_db()
        self.assertEqual(session.expires_at, before)

    def test_the_throttle_governs_renewal_and_last_seen_together(self):
        """One write covers both, so a busy dashboard costs one round trip."""
        from .sessions import touch_session

        session = self._session(idle=timedelta(seconds=30))
        seen = session.last_seen_at

        touch_session(session)

        session.refresh_from_db()
        self.assertEqual(session.last_seen_at, seen)

    def test_activity_is_recorded_even_when_the_expiry_cannot_move(self):
        from .security import SESSION_MAX_LIFETIME
        from .sessions import touch_session

        session = self._session(age=SESSION_MAX_LIFETIME + timedelta(days=1), idle=self.DUE)
        before = session.expires_at

        self.assertIsNone(touch_session(session))

        session.refresh_from_db()
        self.assertEqual(session.expires_at, before)
        self.assertGreater(session.last_seen_at, timezone.now() - timedelta(minutes=1))

    # --- the ceiling ---------------------------------------------------------

    def test_renewal_is_clamped_to_the_absolute_ceiling(self):
        from .security import SESSION_MAX_LIFETIME
        from .sessions import touch_session

        # Old enough that a full rolling window would reach past the ceiling.
        age = SESSION_MAX_LIFETIME - timedelta(hours=2)
        session = self._session(age=age, idle=self.DUE)

        renewed = touch_session(session)

        self.assertIsNotNone(renewed)
        self.assertEqual(renewed, session.created_at + SESSION_MAX_LIFETIME)
        self.assertLess(renewed, timezone.now() + SESSION_TTL)

    def test_a_remember_me_session_is_clamped_to_its_own_ceiling(self):
        from .security import SESSION_MAX_LIFETIME_REMEMBER
        from .sessions import touch_session

        age = SESSION_MAX_LIFETIME_REMEMBER - timedelta(days=1)
        session = self._session(remember=True, age=age, idle=self.DUE)

        renewed = touch_session(session)

        self.assertEqual(renewed, session.created_at + SESSION_MAX_LIFETIME_REMEMBER)

    def test_no_amount_of_activity_pushes_past_the_ceiling(self):
        """Repeated renewal converges on the ceiling; it never walks through it."""
        from .security import SESSION_MAX_LIFETIME
        from .sessions import touch_session

        session = self._session(age=SESSION_MAX_LIFETIME - timedelta(hours=1))
        ceiling = session.created_at + SESSION_MAX_LIFETIME

        for _ in range(5):
            LoginSession.objects.filter(pk=session.pk).update(
                last_seen_at=timezone.now() - self.DUE
            )
            session.refresh_from_db()
            touch_session(session)

        session.refresh_from_db()
        self.assertLessEqual(session.expires_at, ceiling)

    def test_a_session_at_its_ceiling_eventually_stops_resolving(self):
        """The ceiling ends the session through the existing expiry path."""
        from .sessions import issue_session, resolve_session, touch_session

        token, session, _ = issue_session(self.account)
        LoginSession.objects.filter(pk=session.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        session.refresh_from_db()

        self.assertIsNone(resolve_session(token))
        self.assertIsNone(touch_session(session))

    # --- what may never be renewed -------------------------------------------

    def test_an_expired_session_is_never_revived(self):
        from .sessions import touch_session

        session = self._session(idle=self.DUE)
        expired_at = timezone.now() - timedelta(minutes=1)
        LoginSession.objects.filter(pk=session.pk).update(expires_at=expired_at)
        session.refresh_from_db()

        self.assertIsNone(touch_session(session))

        session.refresh_from_db()
        self.assertEqual(session.expires_at, expired_at)

    def test_a_revoked_session_is_never_renewed(self):
        from .sessions import revoke_session, touch_session

        session = self._session(idle=self.DUE)
        before = session.expires_at
        revoke_session(session)

        self.assertIsNone(touch_session(session))

        session.refresh_from_db()
        self.assertEqual(session.expires_at, before)

    def test_a_session_revoked_by_password_change_is_never_renewed(self):
        from .sessions import revoke_all_for_account, touch_session

        session = self._session(idle=self.DUE)
        before = session.expires_at
        revoke_all_for_account(self.account)

        # The in-memory row is stale — it still looks live. The conditional
        # UPDATE is what refuses it, which is the case that matters: another
        # request revoked it after this one resolved.
        self.assertIsNone(touch_session(session))

        session.refresh_from_db()
        self.assertEqual(session.expires_at, before)

    def test_a_disabled_account_stops_resolving_before_any_renewal(self):
        """Validation runs first, so a disabled account never reaches a touch."""
        from .sessions import issue_session, resolve_session

        token, session, _ = issue_session(self.account)
        LoginSession.objects.filter(pk=session.pk).update(
            last_seen_at=timezone.now() - self.DUE
        )
        before = LoginSession.objects.get(pk=session.pk).expires_at

        self.account.is_active = False
        self.account.save(update_fields=["is_active"])

        self.assertIsNone(resolve_session(token))
        self.assertEqual(LoginSession.objects.get(pk=session.pk).expires_at, before)

    # --- concurrency ---------------------------------------------------------

    def test_a_stale_request_cannot_shorten_an_expiry_another_one_set(self):
        """Two tabs. The later target stands; the earlier one no-ops."""
        from .sessions import touch_session

        from .security import SESSION_TTL

        session = self._session(idle=self.DUE)
        stale = LoginSession.objects.get(pk=session.pk)

        # Tab A leaves the row further out than a fresh rolling window reaches.
        far = timezone.now() + SESSION_TTL + timedelta(hours=1)
        LoginSession.objects.filter(pk=session.pk).update(expires_at=far)

        # Tab B is still holding the row it read before that, so its own target
        # is nearer than what is already stored. It must not pull the expiry back.
        self.assertIsNone(touch_session(stale))

        self.assertEqual(LoginSession.objects.get(pk=session.pk).expires_at, far)

    def test_concurrent_renewals_converge_and_respect_the_ceiling(self):
        from .security import SESSION_MAX_LIFETIME
        from .sessions import touch_session

        session = self._session(age=SESSION_MAX_LIFETIME - timedelta(minutes=30))
        ceiling = session.created_at + SESSION_MAX_LIFETIME

        # Several requests holding the same pre-renewal row, as tabs would.
        copies = [LoginSession.objects.get(pk=session.pk) for _ in range(4)]
        for copy in copies:
            copy.last_seen_at = timezone.now() - self.DUE

        results = [touch_session(copy) for copy in copies]

        final = LoginSession.objects.get(pk=session.pk).expires_at
        self.assertLessEqual(final, ceiling)
        # Whoever won, the row never went backwards from what any of them saw.
        for renewed in results:
            if renewed is not None:
                self.assertLessEqual(renewed, final)

    def test_renewal_creates_no_additional_sessions(self):
        from .sessions import touch_session

        session = self._session(idle=self.DUE)
        touch_session(session)

        self.assertEqual(LoginSession.objects.filter(account=self.account).count(), 1)


class PruneSessionsTests(TestCase):
    """``prune_login_sessions``: what it removes, and what it must not.

    The command deletes across the whole table, so every assertion here is about
    specific rows rather than counts -- another test's leftovers would make a
    count meaningless.
    """

    databases = {"default", "enrolment"}

    def setUp(self):
        super().setUp()
        self.account = LoginAccount.objects.create(
            subject_type="staff",
            subject_id=900_000 + (uuid.uuid4().int % 90_000),
            email=f"prune-{uuid.uuid4().hex[:10]}@kbc.invalid",
            display_name="Prune Tester",
            role=ROLE_STAFF,
            password_hash=hash_password("Vaulted-Harbour-92!"),
        )

    def tearDown(self):
        LoginSession.objects.filter(account=self.account).delete()
        LoginAccount.objects.filter(pk=self.account.pk).delete()
        super().tearDown()

    def _session(self, *, expires_in=None, revoked_ago=None):
        session = LoginSession.objects.create(
            account=self.account,
            token_hash=hash_token(generate_token()),
            expires_at=timezone.now() + (expires_in or timedelta(hours=1)),
            last_seen_at=timezone.now(),
        )
        if revoked_ago is not None:
            LoginSession.objects.filter(pk=session.pk).update(
                revoked_at=timezone.now() - revoked_ago
            )
        return session

    @staticmethod
    def _prune(**kwargs):
        from django.core.management import call_command

        call_command("prune_login_sessions", stdout=io.StringIO(), **kwargs)

    def _assert_kept(self, session):
        self.assertTrue(
            LoginSession.objects.filter(pk=session.pk).exists(),
            "a session that should have been kept was deleted",
        )

    def _assert_deleted(self, session):
        self.assertFalse(
            LoginSession.objects.filter(pk=session.pk).exists(),
            "a session that should have been deleted survived",
        )

    def test_a_live_session_is_never_touched(self):
        """The one outcome that would be an incident rather than a bug."""
        live = self._session(expires_in=timedelta(hours=12))

        self._prune(days=0)

        self._assert_kept(live)

    def test_a_long_dead_session_is_deleted(self):
        old = self._session(expires_in=-timedelta(days=40))

        self._prune()

        self._assert_deleted(old)

    def test_a_recently_dead_session_is_kept(self):
        """Retention is the point: it is what answers "why was I signed out?"."""
        recent = self._session(expires_in=-timedelta(days=2))

        self._prune()

        self._assert_kept(recent)

    def test_a_recent_revocation_of_an_old_session_is_kept(self):
        """The case a naive "expired long ago" filter gets wrong.

        Expiry passed six weeks back, but the revocation is yesterday's -- and
        the revocation is the event somebody would be asking about.
        """
        revoked = self._session(
            expires_in=-timedelta(days=42), revoked_ago=timedelta(days=1)
        )

        self._prune()

        self._assert_kept(revoked)

    def test_an_old_revocation_is_deleted(self):
        revoked = self._session(
            expires_in=-timedelta(days=90), revoked_ago=timedelta(days=60)
        )

        self._prune()

        self._assert_deleted(revoked)

    def test_a_dry_run_deletes_nothing(self):
        old = self._session(expires_in=-timedelta(days=40))

        self._prune(dry_run=True)

        self._assert_kept(old)

    def test_a_negative_retention_is_refused(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._prune(days=-1)


class SessionEventTests(TestCase):
    """The structured events on ``login.sessions``.

    Only the ceiling one is really load-bearing: it is the sole advance warning
    that somebody working right now will be signed out and cannot prevent it.
    """

    databases = {"default", "enrolment"}

    def setUp(self):
        super().setUp()
        self.account = LoginAccount.objects.create(
            subject_type="staff",
            subject_id=900_000 + (uuid.uuid4().int % 90_000),
            email=f"event-{uuid.uuid4().hex[:10]}@kbc.invalid",
            display_name="Event Tester",
            role=ROLE_STAFF,
            password_hash=hash_password("Vaulted-Harbour-92!"),
        )

    def tearDown(self):
        LoginSession.objects.filter(account=self.account).delete()
        LoginAccount.objects.filter(pk=self.account.pk).delete()
        super().tearDown()

    def _session(self, *, age=None, idle=None):
        from .sessions import issue_session

        _token, session, _ttl = issue_session(self.account)
        fields = {}
        if age is not None:
            fields["created_at"] = timezone.now() - age
            fields["expires_at"] = timezone.now() + timedelta(minutes=1)
        if idle is not None:
            fields["last_seen_at"] = timezone.now() - idle
        if fields:
            LoginSession.objects.filter(pk=session.pk).update(**fields)
            session.refresh_from_db()
        return session

    @staticmethod
    def _events(captured):
        return [
            record.event for record in captured.records if hasattr(record, "event")
        ]

    def test_signing_in_is_announced(self):
        from .sessions import issue_session

        with self.assertLogs("login.sessions", level="INFO") as captured:
            issue_session(self.account, remember=True)

        self.assertIn("session.issued", self._events(captured))
        record = next(r for r in captured.records if r.event == "session.issued")
        self.assertTrue(record.remember)
        # No token, no hash, no email -- the allowlist in
        # config.observability is what enforces it, this is what notices if the
        # call site starts trying to smuggle one through.
        self.assertFalse(hasattr(record, "token"))

    def test_a_session_at_its_ceiling_announces_itself(self):
        from .security import SESSION_MAX_LIFETIME
        from .sessions import touch_session

        session = self._session(
            age=SESSION_MAX_LIFETIME, idle=timedelta(minutes=10)
        )

        with self.assertLogs("login.sessions", level="INFO") as captured:
            # No renewal is possible: this is the point of the event.
            self.assertIsNone(touch_session(session))

        self.assertIn("session.at_ceiling", self._events(captured))

    def test_an_ordinary_renewal_does_not_cry_ceiling(self):
        from .sessions import touch_session

        session = self._session(idle=timedelta(minutes=10))

        with self.assertLogs("login.sessions", level="INFO") as captured:
            self.assertIsNotNone(touch_session(session))

        events = self._events(captured)
        self.assertIn("session.renewed", events)
        self.assertNotIn("session.at_ceiling", events)

    def test_an_expired_session_is_rejected_with_the_reason_why(self):
        from .sessions import issue_session, resolve_session

        token, session, _ttl = issue_session(self.account)
        LoginSession.objects.filter(pk=session.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        with self.assertLogs("login.sessions", level="INFO") as captured:
            self.assertIsNone(resolve_session(token))

        record = next(r for r in captured.records if r.event == "session.rejected")
        self.assertEqual(record.reason, "expired_idle")

    def test_revoking_every_session_reports_how_many(self):
        from .sessions import issue_session, revoke_all_for_account

        issue_session(self.account)
        issue_session(self.account)

        with self.assertLogs("login.sessions", level="INFO") as captured:
            revoke_all_for_account(self.account)

        record = next(
            r for r in captured.records if r.event == "session.revoked_bulk"
        )
        self.assertEqual(record.row_count, 2)


class SessionStatsCommandTests(TestCase):
    """A smoke test: the command reads real columns and must keep matching them."""

    databases = {"default", "enrolment"}

    def test_it_reports_the_ceiling_split(self):
        from django.core.management import call_command

        out = io.StringIO()
        call_command("session_stats", stdout=out)
        printed = out.getvalue()

        self.assertIn("Live sessions", printed)
        self.assertIn("expired at the absolute ceiling", printed)
        self.assertIn("Sign-ins per day", printed)

    def test_a_zero_window_is_refused(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("session_stats", days=0, stdout=io.StringIO())


class ThrottleCounterTests(TestCase):
    """The sliding window is read from the audit table, so it needs rows."""

    databases = {"default", "enrolment"}

    def setUp(self):
        super().setUp()
        self.ip = f"203.0.113.{uuid.uuid4().int % 200}"
        self.email = f"thr-{uuid.uuid4().hex[:10]}@kbc.invalid"

    def tearDown(self):
        LoginAudit.objects.filter(ip_address=self.ip).delete()
        LoginAudit.objects.filter(email=self.email).delete()
        super().tearDown()

    def _fail(self, when=None):
        row = LoginAudit.objects.create(
            event="login", email=self.email, succeeded=False,
            reason="bad_password", ip_address=self.ip,
        )
        if when is not None:
            LoginAudit.objects.filter(pk=row.pk).update(created_at=when)
        return row

    def test_a_quiet_ip_is_not_throttled(self):
        from .security import ip_is_throttled

        self.assertFalse(ip_is_throttled(self.ip))

    def test_an_ip_is_never_throttled_however_many_failures(self):
        """The per-IP throttle is deliberately disabled -- see ip_is_throttled.

        The college shares one egress address, so counting failures per source
        refused sign-in to everybody in the building whenever anyone nearby
        mistyped a password often enough. Per-account lockout (covered by the
        LockoutTests above) is what still stops password guessing.
        """
        from .security import ip_is_throttled

        for _ in range(THROTTLE_MAX_FAILURES_PER_IP + 5):
            self._fail()
        self.assertFalse(ip_is_throttled(self.ip))

    def test_old_failures_fall_out_of_the_window(self):
        from .security import ip_is_throttled

        stale = timezone.now() - timedelta(days=1)
        for _ in range(THROTTLE_MAX_FAILURES_PER_IP + 5):
            self._fail(when=stale)
        self.assertFalse(ip_is_throttled(self.ip))

    def test_successful_logins_do_not_count_towards_the_limit(self):
        from .security import ip_is_throttled

        for _ in range(THROTTLE_MAX_FAILURES_PER_IP + 5):
            LoginAudit.objects.create(
                event="login", email=self.email, succeeded=True, ip_address=self.ip,
            )
        self.assertFalse(ip_is_throttled(self.ip))

    def test_no_ip_is_never_throttled(self):
        """A missing REMOTE_ADDR must not lock every anonymous caller out."""
        from .security import ip_is_throttled

        self.assertFalse(ip_is_throttled(None))


#: A complete Microsoft sign-in configuration, for tests that want one.
_SSO_ENV = {
    "MICROSOFT_SSO_CLIENT_ID": "sso-client",
    "MICROSOFT_SSO_CLIENT_SECRET": "sso-secret",
    "MICROSOFT_SSO_TENANT_ID": "sso-tenant",
    "MICROSOFT_SSO_CALLBACK_URI": "https://lms.kbc.test/login_api/microsoft/callback/",
}


class MicrosoftSsoConfigTests(SimpleTestCase):
    """Which environment names the sign-in flow reads, and which it must not."""

    def test_dedicated_names_win(self):
        env = dict(
            _SSO_ENV,
            MICROSOFT_CLIENT_ID="calendar-client",
            MICROSOFT_CLIENT_SECRET="calendar-secret",
        )
        with mock.patch.dict("os.environ", env, clear=False):
            config = microsoft_sso.config()
        self.assertEqual(config["client_id"], "sso-client")
        self.assertEqual(config["client_secret"], "sso-secret")
        self.assertEqual(config["tenant"], "sso-tenant")

    def test_falls_back_to_the_shared_delegated_app(self):
        """One delegated app registration may serve both sign-in and calendar."""
        env = {
            "MICROSOFT_SSO_CLIENT_ID": "", "MICROSOFT_SSO_CLIENT_SECRET": "",
            "MICROSOFT_SSO_TENANT_ID": "",
            "MICROSOFT_SSO_CALLBACK_URI": _SSO_ENV["MICROSOFT_SSO_CALLBACK_URI"],
            "MICROSOFT_CLIENT_ID": "calendar-client",
            "MICROSOFT_CLIENT_SECRET": "calendar-secret",
            "MICROSOFT_TENANT_ID": "real-directory-guid",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            config = microsoft_sso.config()
            self.assertTrue(microsoft_sso.is_configured())
        self.assertEqual(config["client_id"], "calendar-client")
        self.assertEqual(config["tenant"], "real-directory-guid")

    def test_tenant_never_borrows_the_calendar_audience(self):
        """MICROSOFT_TENANT is set to 'common' so learners can attach a personal
        Outlook calendar. Reusing it here would offer sign-in to personal
        accounts that can never hold a platform account."""
        env = dict(_SSO_ENV, MICROSOFT_SSO_TENANT_ID="", MICROSOFT_TENANT="common")
        with mock.patch.dict("os.environ", env, clear=False):
            env2 = dict(env, MICROSOFT_TENANT_ID="")
            with mock.patch.dict("os.environ", env2, clear=False):
                self.assertEqual(microsoft_sso.config()["tenant"], "organizations")

    def test_callback_never_borrows_the_calendar_callback(self):
        """MICROSOFT_CALLBACK_URI points at the calendar's own handler; a
        sign-in redirected there would be swallowed by the wrong view."""
        env = dict(
            _SSO_ENV,
            MICROSOFT_SSO_CALLBACK_URI="",
            MICROSOFT_CALLBACK_URI="https://lms.kbc.test/learner_api/calendar/oauth/microsoft/",
        )
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertEqual(microsoft_sso.config()["callback"], "")
            self.assertIn("MICROSOFT_SSO_CALLBACK_URI", microsoft_sso.missing_settings())

    def test_missing_settings_reports_names_only_never_values(self):
        """/login_api/health/ publishes this list — it must leak no secrets."""
        env = dict(_SSO_ENV, MICROSOFT_SSO_CLIENT_SECRET="", MICROSOFT_CLIENT_SECRET="")
        with mock.patch.dict("os.environ", env, clear=False):
            reported = " ".join(microsoft_sso.missing_settings())
        self.assertIn("MICROSOFT_SSO_CLIENT_SECRET", reported)
        self.assertNotIn("sso-secret", reported)
        self.assertNotIn("sso-client", reported)


class MicrosoftSsoStartTests(SimpleTestCase):
    """The authorize URL handed to the browser. No database is touched."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def _start(self, **params):
        with mock.patch.dict("os.environ", _SSO_ENV, clear=False):
            return microsoft_sso.start(self.factory.get("/login_api/microsoft/start/", params))

    def _authorize_url(self, **params):
        import json as _json

        return _json.loads(self._start(**params).content)["authorizationUrl"]

    def test_unconfigured_deployment_is_refused_not_half_built(self):
        env = dict(_SSO_ENV, MICROSOFT_SSO_CLIENT_ID="", MICROSOFT_CLIENT_ID="")
        with mock.patch.dict("os.environ", env, clear=False):
            response = microsoft_sso.start(self.factory.get("/login_api/microsoft/start/"))
        self.assertEqual(response.status_code, 503)

    def test_url_targets_the_configured_tenant_and_client(self):
        url = self._authorize_url()
        self.assertTrue(url.startswith("https://login.microsoftonline.com/sso-tenant/oauth2/v2.0/authorize?"))
        self.assertIn("client_id=sso-client", url)
        self.assertIn("response_type=code", url)

    def test_no_refresh_token_is_requested(self):
        """This flow wants an identity once. A refresh token it never uses is a
        stored credential with nothing to gain from holding it."""
        self.assertNotIn("offline_access", self._authorize_url())

    def test_state_is_signed_without_a_saved_return_path(self):
        from urllib.parse import parse_qs, urlparse

        state = parse_qs(urlparse(self._authorize_url(next="/workspace/admin")).query)["state"][0]
        payload = signing.loads(state, salt=microsoft_sso.STATE_SALT, max_age=microsoft_sso.STATE_MAX_AGE)
        self.assertNotIn("next", payload)
        self.assertTrue(payload["n"])

    def test_state_from_a_different_flow_is_rejected(self):
        """The calendar connection flow signs its own state; salts must not be
        interchangeable or one flow's state would authorise the other."""
        forged = signing.dumps({"next": "/"}, salt="learner-personal-calendar-oauth")
        with self.assertRaises(signing.BadSignature):
            signing.loads(forged, salt=microsoft_sso.STATE_SALT)

    def test_only_fixed_android_intent_survives_in_signed_state(self):
        from urllib.parse import parse_qs, urlparse
        for value in ("android", "https://evil.test", "//evil.test", "/workspace/admin"):
            state = parse_qs(urlparse(self._authorize_url(download=value)).query)["state"][0]
            payload = signing.loads(state, salt=microsoft_sso.STATE_SALT)
            self.assertEqual(payload.get("download"), "android" if value == "android" else None)

    def test_only_fixed_lecture_feedback_intent_survives_in_signed_state(self):
        from urllib.parse import parse_qs, urlparse
        for value in ('lecture-feedback', 'https://evil.test', '//evil.test'):
            state = parse_qs(urlparse(self._authorize_url(intent=value)).query)['state'][0]
            payload = signing.loads(state, salt=microsoft_sso.STATE_SALT)
            self.assertEqual(payload.get('intent'), 'lecture-feedback' if value == 'lecture-feedback' else None)

    def test_absolute_next_is_refused(self):
        """An attacker-supplied return target would make this an open redirect."""
        from urllib.parse import parse_qs, urlparse

        for hostile in ("https://evil.test/steal", "//evil.test/steal", "javascript:alert(1)"):
            state = parse_qs(urlparse(self._authorize_url(next=hostile)).query)["state"][0]
            payload = signing.loads(state, salt=microsoft_sso.STATE_SALT)
            self.assertNotIn("next", payload, f"{hostile} survived in the state")
