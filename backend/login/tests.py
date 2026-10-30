"""Tests for the login app's security-relevant behaviour.

These run against the real Neon database (the `login` schema), because the
models are unmanaged and the test runner cannot create them on SQLite — the
same constraint every other Neon-backed app in this project has. Each test
cleans up the rows it creates.

Run (the custom runner is required — it creates the unmanaged tables in the
test database, which Django's own runner cannot do for managed = False models):

    python manage.py test login --testrunner=login.test_runner.EnrolmentTestRunner

Neon keeps pooled connections open briefly after a run, so the final "destroying
test database" step sometimes reports that the database is still in use. That
happens after the results line and does not affect them; the next run recreates
the database anyway.

The focus is deliberately narrow: the things that would be a security bug if
they regressed. Not covered here are the HTML of the emails and the exact
wording of messages, which are allowed to change.
"""
import json
import uuid
from datetime import timedelta
from unittest import mock

from django.core import signing
from django.test import Client, TestCase
from django.utils import timezone

from learner_api.models import StaffUser

from . import identity, microsoft_sso
from .invitations import accept_invitation, create_invitation, create_reset, complete_reset
from .models import Invitation, LoginAccount, LoginAudit, LoginSession, PasswordReset
from .security import (
    LOCKOUT_THRESHOLD,
    PasswordPolicyError,
    generate_token,
    hash_password,
    hash_token,
    normalize_email,
    validate_password_strength,
    verify_password,
)

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

#: Distinguishes "caller did not say" from "caller wants no access at all",
#: which is a meaningful state now that access decides the role.
_UNSET = object()


class LoginTestBase(TestCase):
    # These models live on the Neon connection, not `default`.
    databases = {"default", "enrolment"}

    def setUp(self):
        super().setUp()
        self.client = Client(SERVER_NAME="localhost")
        # A unique address per test so parallel/repeat runs cannot collide.
        self.email = f"qa-{uuid.uuid4().hex[:12]}@kbc.invalid"
        # Deliberately shares no word with the account's display name or email
        # local part — validate_password_strength rejects passwords that do,
        # and a fixture that trips its own policy tests nothing useful.
        self.password = "Vaulted-Harbour-92!"
        self._staff = None
        self._accounts = []

    def tearDown(self):
        for account in self._accounts:
            LoginSession.objects.filter(account_id=account.id).delete()
            Invitation.objects.filter(account_id=account.id).delete()
            PasswordReset.objects.filter(account_id=account.id).delete()
            LoginAccount.objects.filter(pk=account.id).delete()
        LoginAudit.objects.filter(email=self.email).delete()
        if self._staff is not None:
            StaffUser.objects.filter(pk=self._staff.id).delete()
        super().tearDown()

    def make_account(self, *, position="Admin", access=_UNSET, with_password=True, active=True):
        """Create a staff row and its login account.

        ``access`` is what decides the role now — Position is only a job title
        (see identity.role_for_staff). It defaults so that the historic
        ``position="Admin"`` still yields an administrator and every other
        position yields staff, which is what each caller means; pass ``access``
        explicitly to test a particular grant.
        """
        if access is _UNSET:
            access = "super-admin" if (position or "").strip().lower() == "admin" else "enrolment"
        self._staff = StaffUser.objects.create(
            username="Test Person", email=self.email, position=position,
            access=access, type="Admin", status="FullUser",
        )
        account, _ = identity.ensure_account("staff", self._staff.id, subject=self._staff)
        if with_password:
            account.password_hash = hash_password(self.password)
            account.password_set_at = timezone.now()
        account.is_active = active
        account.save()
        self._accounts.append(account)
        return account

    def post(self, url, payload, **extra):
        return self.client.post(
            url, data=json.dumps(payload), content_type="application/json", **{**XHR, **extra}
        )


class PasswordPolicyTests(TestCase):
    def test_rejects_short_password(self):
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("Ab1!x")

    def test_rejects_common_password(self):
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("password123")

    def test_rejects_password_containing_email_local_part(self):
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("jsmith-is-here", email="jsmith@example.com")

    def test_rejects_single_repeated_character(self):
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("aaaaaaaaaaaa")

    def test_accepts_a_reasonable_passphrase(self):
        self.assertTrue(validate_password_strength("correct-horse-battery"))

    def test_hash_is_not_the_password_and_verifies(self):
        hashed = hash_password("correct-horse-battery")
        self.assertNotIn("correct-horse-battery", hashed)
        self.assertTrue(verify_password("correct-horse-battery", hashed))
        self.assertFalse(verify_password("wrong", hashed))

    def test_empty_hash_never_verifies(self):
        """An invited-but-not-onboarded account must not be signable-into."""
        self.assertFalse(verify_password("", ""))
        self.assertFalse(verify_password("anything", ""))


class LoginEndpointTests(LoginTestBase):
    def test_successful_login_sets_session_cookie(self):
        self.make_account()
        response = self.post("/login_api/login/", {"email": self.email, "password": self.password})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["role"], "admin")

        cookie = response.cookies.get("kbc_session")
        self.assertIsNotNone(cookie)
        # HttpOnly is the control that stops an XSS bug becoming account theft.
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")

    def test_session_token_is_not_stored_in_plaintext(self):
        self.make_account()
        response = self.post("/login_api/login/", {"email": self.email, "password": self.password})
        token = response.cookies["kbc_session"].value

        self.assertFalse(LoginSession.objects.filter(token_hash=token).exists())
        self.assertTrue(LoginSession.objects.filter(token_hash=hash_token(token)).exists())

    def test_wrong_password_and_unknown_account_are_indistinguishable(self):
        self.make_account()
        wrong = self.post("/login_api/login/", {"email": self.email, "password": "not-it"})
        unknown = self.post(
            "/login_api/login/",
            {"email": f"absent-{uuid.uuid4().hex[:8]}@kbc.invalid", "password": "not-it"},
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        # Identical body: anything else is an account-enumeration oracle.
        self.assertEqual(wrong.json(), unknown.json())

    def test_login_without_xhr_header_is_refused(self):
        """The CSRF defence: a cross-origin form post cannot set this header."""
        self.make_account()
        response = self.client.post(
            "/login_api/login/",
            data=json.dumps({"email": self.email, "password": self.password}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "csrf")

    def test_account_without_password_cannot_sign_in(self):
        self.make_account(with_password=False)
        response = self.post("/login_api/login/", {"email": self.email, "password": ""})
        self.assertEqual(response.status_code, 400)
        response = self.post("/login_api/login/", {"email": self.email, "password": "guess"})
        self.assertEqual(response.status_code, 401)

    def test_inactive_account_cannot_sign_in(self):
        self.make_account(active=False)
        response = self.post("/login_api/login/", {"email": self.email, "password": self.password})
        self.assertEqual(response.status_code, 401)

    def test_repeated_failures_lock_the_account(self):
        account = self.make_account()
        for _ in range(LOCKOUT_THRESHOLD):
            self.post("/login_api/login/", {"email": self.email, "password": "wrong"})

        account.refresh_from_db()
        self.assertGreaterEqual(account.failed_attempts, LOCKOUT_THRESHOLD)
        self.assertIsNotNone(account.locked_until)

        # The correct password is refused while the lock is in force.
        response = self.post("/login_api/login/", {"email": self.email, "password": self.password})
        self.assertEqual(response.status_code, 423)
        self.assertEqual(response.json()["code"], "locked")

    def test_successful_login_clears_failure_count(self):
        account = self.make_account()
        self.post("/login_api/login/", {"email": self.email, "password": "wrong"})
        self.post("/login_api/login/", {"email": self.email, "password": self.password})
        account.refresh_from_db()
        self.assertEqual(account.failed_attempts, 0)
        self.assertIsNone(account.locked_until)

    def test_me_requires_a_session_and_logout_ends_it(self):
        self.make_account()
        self.assertEqual(self.client.get("/login_api/me/").status_code, 401)

        self.post("/login_api/login/", {"email": self.email, "password": self.password})
        self.assertEqual(self.client.get("/login_api/me/").status_code, 200)

        self.assertEqual(self.client.post("/login_api/logout/", **XHR).status_code, 200)
        self.assertEqual(self.client.get("/login_api/me/").status_code, 401)

    def test_me_never_exposes_the_password_hash(self):
        self.make_account()
        self.post("/login_api/login/", {"email": self.email, "password": self.password})
        body = self.client.get("/login_api/me/").content.decode()
        self.assertNotIn("password_hash", body)
        self.assertNotIn("passwordHash", body)
        self.assertNotIn("argon2", body)
        self.assertNotIn("pbkdf2", body)

    def test_login_is_audited(self):
        self.make_account()
        self.post("/login_api/login/", {"email": self.email, "password": "wrong"})
        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        events = LoginAudit.objects.filter(email=self.email, event="login")
        self.assertTrue(events.filter(succeeded=False, reason="bad_password").exists())
        self.assertTrue(events.filter(succeeded=True).exists())


class RollingSessionCookieTests(LoginTestBase):
    """The outbound half of rolling expiry, exercised through real requests.

    The database side is covered in ``tests_unit.RollingRenewalTests``; what
    matters here is the part only a full request/response can show — that the
    browser is told about a renewal, that it is *not* told anything on an
    ordinary request, and above all that the renewal cannot undo a sign-out.
    """

    def _sign_in(self, *, remember=False):
        response = self.post(
            "/login_api/login/",
            {"email": self.email, "password": self.password, "remember": remember},
        )
        self.assertEqual(response.status_code, 200)
        return LoginSession.objects.get(token_hash=hash_token(response.cookies["kbc_session"].value))

    @staticmethod
    def _make_due(session):
        """Age Last_seen_at past the touch throttle, as real idling would."""
        LoginSession.objects.filter(pk=session.pk).update(
            last_seen_at=timezone.now() - timedelta(minutes=10)
        )

    def _assert_browser_session_cookie(self, cookie):
        """Neither Max-Age nor Expires: the browser drops it when it closes.

        Django writes an empty ``expires`` morsel when it is given neither, so
        both read as "" rather than being absent from the morsel altogether.
        """
        self.assertEqual(cookie["max-age"], "", "the cookie was given a Max-Age")
        self.assertEqual(cookie["expires"], "", "the cookie was given an Expires")

    def _assert_persistent_cookie(self, cookie):
        self.assertNotEqual(cookie["max-age"], "", "the cookie has no Max-Age")
        self.assertNotEqual(cookie["expires"], "", "the cookie has no Expires")

    # --- the choice reaches the row -----------------------------------------

    def test_login_persists_whether_remember_me_was_ticked(self):
        self.make_account()
        self.assertFalse(self._sign_in(remember=False).remember)

        self.client.cookies.clear()
        self.assertTrue(self._sign_in(remember=True).remember)

    # --- the choice reaches the browser -------------------------------------

    def test_a_normal_login_gets_a_browser_session_cookie(self):
        """What leaving "remember me" unticked now buys.

        The session itself still lasts twelve rolling hours -- that is
        ``Expires_at``, and it is unchanged. This is only about whether the
        browser keeps the cookie once it closes, which for a console reached
        from shared college machines is the difference between the next person
        finding a live session and finding the sign-in page.
        """
        self.make_account()
        response = self.post(
            "/login_api/login/",
            {"email": self.email, "password": self.password, "remember": False},
        )

        self.assertEqual(response.status_code, 200)
        self._assert_browser_session_cookie(response.cookies["kbc_session"])

    def test_a_remembered_login_gets_a_persistent_cookie(self):
        from .security import SESSION_TTL_REMEMBER

        self.make_account()
        response = self.post(
            "/login_api/login/",
            {"email": self.email, "password": self.password, "remember": True},
        )

        cookie = response.cookies["kbc_session"]
        self._assert_persistent_cookie(cookie)
        self.assertEqual(
            int(cookie["max-age"]), int(SESSION_TTL_REMEMBER.total_seconds())
        )

    # --- renewal reaches the browser ----------------------------------------

    def test_a_renewing_request_extends_a_remembered_cookie(self):
        self.make_account()
        session = self._sign_in(remember=True)
        self._make_due(session)

        response = self.client.get("/login_api/me/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("kbc_session", response.cookies)
        session.refresh_from_db()
        # The cookie is told how long is actually left, not a fresh full window.
        max_age = int(response.cookies["kbc_session"]["max-age"])
        remaining = (session.expires_at - timezone.now()).total_seconds()
        self.assertAlmostEqual(max_age, remaining, delta=30)

    def test_a_renewal_extends_a_normal_session_without_persisting_it(self):
        """Both halves matter.

        The row really is extended, so somebody working is not signed out at
        twelve hours. The cookie carrying it still dies with the browser, which
        is the whole of what leaving "remember me" unticked asked for.
        """
        self.make_account()
        session = self._sign_in()
        before = session.expires_at
        self._make_due(session)

        response = self.client.get("/login_api/me/")

        self.assertIn("kbc_session", response.cookies)
        self._assert_browser_session_cookie(response.cookies["kbc_session"])
        session.refresh_from_db()
        self.assertGreater(session.expires_at, before)

    def _assert_every_renewal(self, session, check):
        """Drive three real renewals; ``check`` inspects the cookie each sent."""
        for round_number in range(1, 4):
            # Idle enough to be due a touch, and far enough short of its window
            # that the renewal certainly moves it -- otherwise a no-op response
            # would carry no cookie at all and prove nothing either way.
            LoginSession.objects.filter(pk=session.pk).update(
                last_seen_at=timezone.now() - timedelta(minutes=10),
                expires_at=timezone.now() + timedelta(hours=1),
            )

            response = self.client.get("/login_api/me/")

            with self.subTest(renewal=round_number):
                self.assertIn("kbc_session", response.cookies)
                check(response.cookies["kbc_session"])

    def test_repeated_renewal_never_makes_a_normal_cookie_persistent(self):
        """The regression this whole change hinges on.

        Renewal re-sends the cookie, and five minutes of ordinary use is enough
        to trigger one. A version of this that took persistence from anything
        but the session row would promote a normal session to a persistent one
        within minutes of signing in -- the choice undone by the very activity
        it was meant to survive, and a single-renewal test would still pass.
        """
        self.make_account()

        self._assert_every_renewal(
            self._sign_in(), self._assert_browser_session_cookie
        )

    def test_repeated_renewal_keeps_a_remembered_cookie_persistent(self):
        """The mirror of the test above: persistence is not lost either."""
        self.make_account()

        self._assert_every_renewal(
            self._sign_in(remember=True), self._assert_persistent_cookie
        )

    def test_the_renewed_cookie_carries_the_same_token(self):
        """Renewal extends a session. It does not rotate or reissue one."""
        self.make_account()
        session = self._sign_in()
        original = self.client.cookies["kbc_session"].value
        self._make_due(session)

        response = self.client.get("/login_api/me/")

        self.assertEqual(response.cookies["kbc_session"].value, original)
        self.assertEqual(LoginSession.objects.filter(account_id=session.account_id).count(), 1)

    def test_the_renewed_cookie_keeps_every_security_flag(self):
        self.make_account()
        session = self._sign_in()
        self._make_due(session)

        renewed = self.client.get("/login_api/me/").cookies["kbc_session"]

        self.assertTrue(renewed["httponly"])
        self.assertEqual(renewed["samesite"], "Lax")
        self.assertEqual(renewed["path"], "/")

    def test_renewed_and_issued_cookies_agree_on_every_flag(self):
        """One helper sets both, so neither can drift from the other."""
        self.make_account()
        issued = self.post(
            "/login_api/login/", {"email": self.email, "password": self.password}
        ).cookies["kbc_session"]
        session = LoginSession.objects.get(token_hash=hash_token(issued.value))
        self._make_due(session)

        renewed = self.client.get("/login_api/me/").cookies["kbc_session"]

        # ``max-age`` and ``expires`` belong in this list for the same reason as
        # the rest: on a normal session both are absent from each, and a renewal
        # that quietly supplied one would be exactly the drift being ruled out.
        for flag in (
            "httponly", "samesite", "path", "secure", "domain", "max-age", "expires",
        ):
            self.assertEqual(renewed[flag], issued[flag], f"{flag} drifted on renewal")

    # --- silence when nothing happened --------------------------------------

    def test_a_request_that_does_not_renew_leaves_the_cookie_alone(self):
        self.make_account()
        self._sign_in()  # Last_seen_at is now, so the throttle holds.

        response = self.client.get("/login_api/me/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("kbc_session", response.cookies)

    def test_an_anonymous_request_sets_no_session_cookie(self):
        self.assertNotIn("kbc_session", self.client.get("/login_api/me/").cookies)

    def test_a_session_at_its_ceiling_is_not_given_a_longer_cookie(self):
        from .security import SESSION_MAX_LIFETIME

        self.make_account()
        session = self._sign_in()
        LoginSession.objects.filter(pk=session.pk).update(
            created_at=timezone.now() - (SESSION_MAX_LIFETIME + timedelta(days=1)),
            expires_at=timezone.now() + timedelta(minutes=5),
            last_seen_at=timezone.now() - timedelta(minutes=10),
        )

        response = self.client.get("/login_api/me/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("kbc_session", response.cookies)

    # --- renewal must never fight the session endpoints ----------------------

    def test_logout_is_not_undone_by_the_response_middleware(self):
        """The regression this guard exists for: a renewed cookie restoring a
        session the same request had just signed out of."""
        self.make_account()
        session = self._sign_in()
        self._make_due(session)  # so the logout request itself renews on the way in

        response = self.client.post("/login_api/logout/", **XHR)

        self.assertEqual(response.status_code, 200)
        # The one Set-Cookie on the response is the deletion, not a renewal.
        self.assertEqual(response.cookies["kbc_session"].value, "")
        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)
        self.assertEqual(self.client.get("/login_api/me/").status_code, 401)

    def test_a_cookie_kept_from_before_logout_does_not_work_afterwards(self):
        self.make_account()
        session = self._sign_in()
        stolen = self.client.cookies["kbc_session"].value
        self._make_due(session)
        self.client.post("/login_api/logout/", **XHR)

        self.client.cookies["kbc_session"] = stolen
        self.assertEqual(self.client.get("/login_api/me/").status_code, 401)

    def test_login_while_already_signed_in_sets_only_the_new_cookie(self):
        self.make_account()
        session = self._sign_in()
        self._make_due(session)  # the old session would renew on the way in

        response = self.post(
            "/login_api/login/", {"email": self.email, "password": self.password}
        )

        fresh = response.cookies["kbc_session"].value
        self.assertNotEqual(fresh, "")
        # The cookie the browser is left holding is the newly issued session.
        self.assertTrue(
            LoginSession.objects.filter(
                token_hash=hash_token(fresh), revoked_at__isnull=True
            ).exists()
        )

    def test_password_change_still_revokes_every_other_session(self):
        self.make_account()
        keep = self._sign_in()

        other = Client(SERVER_NAME="localhost")
        other.post(
            "/login_api/login/",
            data=json.dumps({"email": self.email, "password": self.password}),
            content_type="application/json",
            **XHR,
        )
        self.assertEqual(other.get("/login_api/me/").status_code, 200)

        self._make_due(keep)
        response = self.post(
            "/login_api/change-password/",
            {"currentPassword": self.password, "newPassword": "Lantern-Quarry-51!"},
        )
        self.assertEqual(response.status_code, 200)

        # The other browser is out; this one, which made the change, stays in.
        self.assertEqual(other.get("/login_api/me/").status_code, 401)
        self.assertEqual(self.client.get("/login_api/me/").status_code, 200)

    def test_a_disabled_account_is_refused_and_gets_no_cookie(self):
        account = self.make_account()
        session = self._sign_in()
        self._make_due(session)

        account.is_active = False
        account.save(update_fields=["is_active"])

        response = self.client.get("/login_api/me/")

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("kbc_session", response.cookies)


class RoleTests(LoginTestBase):
    def test_super_admin_access_grants_admin_role(self):
        account = self.make_account(access="super-admin")
        self.assertEqual(account.role, "admin")

    def test_other_accesses_are_staff_not_admin(self):
        for access in ("enrolment", "curriculum", "coach"):
            with self.subTest(access=access):
                account = self.make_account(access=access)
                self.assertEqual(account.role, "staff")
                self.assertNotIn("accounts.manage", identity.permissions_for(account.role))
                self.tearDown()
                self.setUp()

    def test_position_admin_without_access_is_not_an_administrator(self):
        """Every account the console creates is Position='Admin'. If that alone
        granted the role, all of them would arrive as platform administrators —
        which is exactly what the access grant exists to prevent."""
        account = self.make_account(position="Admin", access=None)
        self.assertEqual(account.role, "staff")

    def test_role_follows_an_access_change(self):
        """Demotion must take effect without the account being recreated.

        Keyed on Access, not Position: Position is a job title now and changing
        it must NOT move anybody's role.
        """
        account = self.make_account(position="Admin", access="super-admin")
        self.assertEqual(account.role, "admin")
        self._staff.access = "enrolment"
        self._staff.save(update_fields=["access"])

        refreshed, created = identity.ensure_account("staff", self._staff.id)
        self.assertFalse(created)
        self.assertEqual(refreshed.role, "staff")

    def test_enrolling_existing_staff_as_a_learner_reuses_their_account(self):
        """A staff member who also studies must keep exactly ONE account.

        The unique index is per subject_type, so a `learner` row on an address a
        `staff` row already uses is permitted by the schema -- and minting it
        locked the person out of everything: account_for_email returns None once
        an address has two active accounts, and password sign-in, Microsoft SSO
        and forgot-password all go through it. Sign-in failed as "Incorrect email
        or password" and forgot-password answered its uniform 200 without ever
        sending anything.
        """
        from learner_api.models import EnrolmentUser

        staff_account = self.make_account()

        learner = EnrolmentUser.objects.create(
            username="Test Person", email=self.email,
            status="FullUser", type="User",
        )
        self.addCleanup(lambda: EnrolmentUser.all_learners.filter(pk=learner.pk).delete())

        account, created = identity.ensure_account("learner", learner.pk, subject=learner)

        self.assertFalse(created, "enrolment must not mint a second account")
        self.assertEqual(account.id, staff_account.id)
        # Still the staff identity, with their password intact.
        self.assertEqual(account.subject_type, "staff")
        self.assertTrue(account.has_password)

        self.assertEqual(
            LoginAccount.objects.filter(email=normalize_email(self.email), is_active=True).count(),
            1,
        )
        # The thing that was actually broken: the address still resolves.
        self.assertIsNotNone(identity.account_for_email(self.email))

    def test_an_ambiguous_address_still_resolves_to_nothing(self):
        """The guard above is what prevents this state; the rule itself stands.

        Kept explicit so nobody "fixes" account_for_email by picking one of the
        two -- guessing which identity a password belongs to is how somebody
        signs in as the wrong person.
        """
        account = self.make_account()
        shadow = LoginAccount.objects.create(
            subject_type="learner", subject_id=999_999,
            email=account.email, display_name="Shadow", role="learner",
        )
        self._accounts.append(shadow)

        self.assertIsNone(identity.account_for_email(self.email))


class InvitationTests(LoginTestBase):
    def test_invitation_token_is_stored_only_as_a_hash(self):
        account = self.make_account(with_password=False)
        invitation, token = create_invitation(account)
        self.assertNotEqual(invitation.token_hash, token)
        self.assertEqual(invitation.token_hash, hash_token(token))

    def test_accepting_an_invitation_sets_the_first_password(self):
        account = self.make_account(with_password=False)
        _, token = create_invitation(account)

        response = self.client.get(f"/login_api/invitation/?token={token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], normalize_email(self.email))

        response = self.post(
            "/login_api/accept-invitation/", {"token": token, "password": self.password}
        )
        self.assertEqual(response.status_code, 200)

        account.refresh_from_db()
        self.assertTrue(account.has_password)
        self.assertEqual(
            self.post("/login_api/login/", {"email": self.email, "password": self.password}).status_code,
            200,
        )

    def test_an_invitation_cannot_be_used_twice(self):
        account = self.make_account(with_password=False)
        _, token = create_invitation(account)
        accept_invitation(token, self.password)

        # Through the endpoint rather than the service: accept_invitation is
        # @transaction.atomic, and letting it raise inside TestCase's own atomic
        # block would poison the transaction for the assertions that follow.
        response = self.post(
            "/login_api/accept-invitation/",
            {"token": token, "password": "A-Different-Passphrase!9"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_token")

    def test_a_weak_password_is_refused_on_acceptance(self):
        account = self.make_account(with_password=False)
        _, token = create_invitation(account)
        response = self.post("/login_api/accept-invitation/", {"token": token, "password": "short"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "weak_password")
        account.refresh_from_db()
        self.assertFalse(account.has_password)

    def test_reissuing_supersedes_the_previous_invitation(self):
        account = self.make_account(with_password=False)
        _, first = create_invitation(account)
        _, second = create_invitation(account)

        # The first link must stop working the moment a replacement is issued.
        self.assertEqual(self.client.get(f"/login_api/invitation/?token={first}").status_code, 400)
        self.assertEqual(self.client.get(f"/login_api/invitation/?token={second}").status_code, 200)

    def test_expired_invitation_is_refused(self):
        account = self.make_account(with_password=False)
        invitation, token = create_invitation(account)
        invitation.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        invitation.save(update_fields=["expires_at"])
        self.assertEqual(self.client.get(f"/login_api/invitation/?token={token}").status_code, 400)


class InvitationLinkTests(LoginTestBase):
    """Copyable set-password links, for learners whose employer blocks our email."""

    def _learner(self, email=None):
        from learner_api.models import EnrolmentUser

        learner = EnrolmentUser.objects.create(
            username="Blocked Learner", email=email or f"qa-learner-{uuid.uuid4().hex[:10]}@kbc.invalid",
            learner_type="commercial",
        )
        self.addCleanup(lambda: EnrolmentUser.all_learners.filter(pk=learner.pk).delete())
        return learner

    def _issue(self, learner, inviter):
        from .services import issue_invitation_link

        with mock.patch("learner_api.learner_progression.advance_learner_by_id") as advance:
            outcome = issue_invitation_link("learner", learner.id, inviter=inviter)
        account = identity.account_for_subject("learner", learner.id)
        if account is not None and account not in self._accounts:
            self._accounts.append(account)
        return outcome, account, advance

    def test_staff_get_a_working_link_that_counts_as_the_invitation(self):
        from .invitations import peek_invitation

        staff = self.make_account(position="Enrolment Officer")
        learner = self._learner()
        outcome, account, advance = self._issue(learner, staff)
        self.assertTrue(outcome["accountCreated"])
        self.assertIn("/set-password?token=", outcome["link"])
        token = outcome["link"].split("token=", 1)[1]
        self.assertEqual(peek_invitation(token)["email"], account.email)
        invitation = Invitation.objects.get(account_id=account.id)
        self.assertIsNotNone(invitation.sent_at)
        self.assertEqual(invitation.invited_by, staff.email)
        advance.assert_called_once_with(learner.id)
        self.assertTrue(LoginAudit.objects.filter(account_id=account.id, event="invite_sent", reason__contains="not emailed").exists())

    def test_a_new_link_replaces_the_previous_one(self):
        from .invitations import TokenError, peek_invitation

        staff = self.make_account(position="Enrolment Officer")
        learner = self._learner()
        first, _, _ = self._issue(learner, staff)
        second, _, _ = self._issue(learner, staff)
        with self.assertRaises(TokenError):
            peek_invitation(first["link"].split("token=", 1)[1])
        peek_invitation(second["link"].split("token=", 1)[1])

    def test_no_link_for_someone_who_already_has_a_password(self):
        staff = self.make_account(position="Enrolment Officer")
        learner = self._learner()
        account, _ = identity.ensure_account("learner", learner.id, subject=learner)
        account.password_hash = hash_password(self.password)
        account.password_set_at = timezone.now()
        account.save()
        self._accounts.append(account)
        outcome, _, advance = self._issue(learner, staff)
        self.assertIsNone(outcome["link"])
        self.assertIn("already set a password", outcome["error"])
        self.assertFalse(Invitation.objects.filter(account_id=account.id).exists())
        advance.assert_not_called()

    def test_learners_and_anonymous_callers_cannot_mint_links(self):
        from .services import issue_invitation_link

        learner = self._learner()
        other = self._learner()
        account, _ = identity.ensure_account("learner", other.id, subject=other)
        self._accounts.append(account)
        for inviter in (None, account):
            outcome = issue_invitation_link("learner", learner.id, inviter=inviter)
            self.assertTrue(outcome["forbidden"])
            self.assertIsNone(outcome["link"])
        self.assertIsNone(identity.account_for_subject("learner", learner.id))

    def test_only_learners_get_copyable_links(self):
        from .services import issue_invitation_link

        staff = self.make_account(position="Enrolment Officer")
        outcome = issue_invitation_link("staff", self._staff.id, inviter=staff)
        self.assertIsNone(outcome["link"])
        self.assertIn("only be copied for learners", outcome["error"])


class DefaultLearnerPasswordTests(LoginTestBase):
    """changeme_password: a learner's first sign-in hands back their set-password link, never a session."""

    def _learner(self):
        from learner_api.models import EnrolmentUser

        learner = EnrolmentUser.objects.create(username="Default Learner", email=self.email, learner_type="commercial")
        self.addCleanup(lambda: EnrolmentUser.all_learners.filter(pk=learner.pk).delete())
        return learner

    def _login(self, password):
        # RequestFactory, not the test Client: see PasswordResetTests._forgot_request.
        from django.test import RequestFactory

        from .sessions import COOKIE_NAME
        from .views import login

        request = RequestFactory().post(
            "/login_api/login/", data=json.dumps({"email": self.email, "password": password}),
            content_type="application/json", **XHR,
        )
        with mock.patch("learner_api.learner_progression.advance_learner_by_id"):
            response = login(request)
        self.assertNotIn(COOKIE_NAME, response.cookies)
        return response

    def _track(self, learner):
        account = identity.account_for_subject("learner", learner.id)
        if account is not None and account not in self._accounts:
            self._accounts.append(account)
        return account

    def test_new_learner_is_sent_to_set_their_password_without_a_session(self):
        from .invitations import peek_invitation

        learner = self._learner()
        response = self._login("changeme_password")
        body = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["passwordSetupRequired"])
        self.assertTrue(body["setPasswordPath"].startswith("/set-password?token="))
        self.assertNotIn("user", body)
        account = self._track(learner)
        self.assertEqual(peek_invitation(body["setPasswordPath"].split("token=", 1)[1])["email"], account.email)
        self.assertFalse(LoginSession.objects.filter(account_id=account.id).exists())

    def test_invited_learner_without_a_password_gets_the_same(self):
        learner = self._learner()
        account, _ = identity.ensure_account("learner", learner.id, subject=learner)
        self._accounts.append(account)
        self.assertTrue(json.loads(self._login("changeme_password").content)["passwordSetupRequired"])

    def test_it_stops_working_once_the_learner_has_a_password(self):
        learner = self._learner()
        account, _ = identity.ensure_account("learner", learner.id, subject=learner)
        account.password_hash = hash_password(self.password)
        account.password_set_at = timezone.now()
        account.save()
        self._accounts.append(account)
        self.assertEqual(self._login("changeme_password").status_code, 401)
        self.assertFalse(Invitation.objects.filter(account_id=account.id).exists())

    def test_staff_without_a_password_cannot_use_it(self):
        staff = self.make_account(position="Coach", with_password=False)
        self.assertEqual(self._login("changeme_password").status_code, 401)
        self.assertFalse(Invitation.objects.filter(account_id=staff.id).exists())

    def test_a_wrong_password_still_fails_for_a_new_learner(self):
        learner = self._learner()
        self.assertEqual(self._login("something-else-entirely").status_code, 401)
        self.assertIsNone(self._track(learner))

    def test_the_default_cannot_be_kept_as_the_new_password(self):
        from .security import PasswordPolicyError, validate_password_strength

        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("changeme_password")
        with self.assertRaises(PasswordPolicyError):
            validate_password_strength("ChangeMe_Password")


class PasswordResetTests(LoginTestBase):
    def test_forgot_password_does_not_disclose_whether_an_account_exists(self):
        self.make_account()
        known = self.post("/login_api/forgot-password/", {"email": self.email})
        unknown = self.post(
            "/login_api/forgot-password/",
            {"email": f"absent-{uuid.uuid4().hex[:8]}@kbc.invalid"},
        )
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(known.json(), unknown.json())

    def _enrolled_learner(self):
        from learner_api.models import EnrolmentUser

        learner = EnrolmentUser.objects.create(
            username="Forgot Learner", email=self.email, learner_type="commercial",
        )
        self.addCleanup(lambda: EnrolmentUser.all_learners.filter(pk=learner.pk).delete())
        return learner

    def _forgot_request(self, email):
        # RequestFactory rather than the test Client: the Client's end-of-request
        # signal closes the pooled connection, which the enclosing TestCase
        # transaction then cannot reopen on the Neon test branch.
        from django.test import RequestFactory

        from .views import forgot_password

        request = RequestFactory().post(
            "/login_api/forgot-password/", data=json.dumps({"email": email}),
            content_type="application/json", **XHR,
        )
        return forgot_password(request)

    def _forgot(self):
        response = self._forgot_request(self.email)
        self.assertEqual(response.status_code, 200)
        unknown = self._forgot_request(f"absent-{uuid.uuid4().hex[:8]}@kbc.invalid")
        # Whatever happened behind it, the answer never says.
        self.assertEqual(json.loads(response.content), json.loads(unknown.content))
        return response

    def test_enrolled_learner_without_an_account_is_sent_the_set_password_invitation(self):
        learner = self._enrolled_learner()
        self._forgot()
        account = LoginAccount.objects.get(subject_type="learner", subject_id=learner.id)
        self._accounts.append(account)
        self.assertEqual((account.role, account.is_active, account.has_password), ("learner", True, False))
        self.assertEqual(Invitation.objects.filter(account_id=account.id).count(), 1)
        self.assertFalse(PasswordReset.objects.filter(account_id=account.id).exists())

    def test_invited_learner_who_never_set_a_password_gets_the_invitation_not_a_reset(self):
        learner = self._enrolled_learner()
        account, _ = identity.ensure_account("learner", learner.id, subject=learner)
        self._accounts.append(account)
        self._forgot()
        self.assertEqual(Invitation.objects.filter(account_id=account.id).count(), 1)
        self.assertFalse(PasswordReset.objects.filter(account_id=account.id).exists())

    def test_staff_without_a_password_and_anyone_with_one_still_get_a_reset(self):
        staff = self.make_account(position="Coach", with_password=False)
        self._forgot()
        self.assertTrue(PasswordReset.objects.filter(account_id=staff.id).exists())
        self.assertFalse(Invitation.objects.filter(account_id=staff.id).exists())

    def test_a_deactivated_learner_account_is_never_revived(self):
        learner = self._enrolled_learner()
        account, _ = identity.ensure_account("learner", learner.id, subject=learner)
        account.is_active = False
        account.save(update_fields=["is_active"])
        self._accounts.append(account)
        self._forgot()
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        self.assertFalse(Invitation.objects.filter(account_id=account.id).exists())
        self.assertEqual(LoginAccount.objects.filter(subject_type="learner", subject_id=learner.id).count(), 1)

    def test_set_password_invitations_share_the_reset_email_allowance(self):
        from .security import THROTTLE_MAX_RESETS_PER_EMAIL

        learner = self._enrolled_learner()
        for _ in range(THROTTLE_MAX_RESETS_PER_EMAIL + 2):
            self._forgot_request(self.email)
        account = LoginAccount.objects.get(subject_type="learner", subject_id=learner.id)
        self._accounts.append(account)
        self.assertTrue(LoginAudit.objects.filter(email=self.email, event="login", reason="reset_throttled").exists())
        # Each invitation supersedes the last; what matters is how many were sent.
        self.assertEqual(
            LoginAudit.objects.filter(email=self.email, event="reset_requested").count(),
            THROTTLE_MAX_RESETS_PER_EMAIL,
        )

    def test_completing_a_reset_changes_the_password(self):
        account = self.make_account()
        _, token = create_reset(account)
        new_password = "Replacement-Passphrase!7"

        response = self.post(
            "/login_api/reset-password/", {"token": token, "password": new_password}
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            self.post("/login_api/login/", {"email": self.email, "password": self.password}).status_code,
            401,
        )
        self.assertEqual(
            self.post("/login_api/login/", {"email": self.email, "password": new_password}).status_code,
            200,
        )

    def test_reset_revokes_existing_sessions(self):
        """A password change must not leave sessions opened with the old one."""
        account = self.make_account()
        self.post("/login_api/login/", {"email": self.email, "password": self.password})
        self.assertEqual(self.client.get("/login_api/me/").status_code, 200)

        _, token = create_reset(account)
        complete_reset(token, "Replacement-Passphrase!7")

        self.assertEqual(self.client.get("/login_api/me/").status_code, 401)

    def test_reset_clears_a_lockout(self):
        account = self.make_account()
        for _ in range(LOCKOUT_THRESHOLD):
            self.post("/login_api/login/", {"email": self.email, "password": "wrong"})

        _, token = create_reset(account)
        complete_reset(token, "Replacement-Passphrase!7")

        account.refresh_from_db()
        self.assertEqual(account.failed_attempts, 0)
        self.assertIsNone(account.locked_until)

    def test_a_reset_token_cannot_be_reused(self):
        account = self.make_account()
        _, token = create_reset(account)
        complete_reset(token, "Replacement-Passphrase!7")

        # Via the endpoint — see the note in test_an_invitation_cannot_be_used_twice.
        response = self.post(
            "/login_api/reset-password/",
            {"token": token, "password": "Yet-Another-Passphrase!8"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_token")


class ChangePasswordTests(LoginTestBase):
    def test_change_requires_the_current_password(self):
        self.make_account()
        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        response = self.post(
            "/login_api/change-password/",
            {"currentPassword": "not-the-current-one", "newPassword": "Replacement-Passphrase!7"},
        )
        self.assertEqual(response.status_code, 403)

    def test_change_password_succeeds_and_keeps_the_caller_signed_in(self):
        self.make_account()
        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        response = self.post(
            "/login_api/change-password/",
            {"currentPassword": self.password, "newPassword": "Replacement-Passphrase!7"},
        )
        self.assertEqual(response.status_code, 200)
        # The session used to make the change survives; others are revoked.
        self.assertEqual(self.client.get("/login_api/me/").status_code, 200)

    def test_change_password_requires_authentication(self):
        response = self.post(
            "/login_api/change-password/",
            {"currentPassword": "x", "newPassword": "Replacement-Passphrase!7"},
        )
        self.assertEqual(response.status_code, 401)


class InvitePrivilegeTests(LoginTestBase):
    """Nobody can mint themselves a credential through the creation forms.

    The three `learner_api` creation endpoints are @csrf_exempt and carry no
    auth decorator. Hooking invitations into them turned "anyone can create a
    junk staff row" into "anyone can create an *admin login* and have the
    set-password link emailed to an address they chose". These tests pin the
    authorisation that closes that.
    """

    def _create_staff(self, email, position, client=None, invite=True, access=None):
        """POST the staff creation form.

        ``access`` is what decides the resulting role — Position is only a job
        title now — so a test that means "create an administrator" passes
        access='super-admin'.
        """
        payload = {
            "username": "Created Person",
            "email": email,
            "position": position,
            "inviteToPlatform": invite,
        }
        if access is not None:
            payload["access"] = access
        client = client or Client(SERVER_NAME="localhost")
        return client.post(
            "/learner_api/staff-users/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_anonymous_caller_cannot_mint_an_admin_account(self):
        """Two independent defences stop this; here the outer one fires first.

        The endpoint gate now refuses the request outright, so it never reaches
        the invite logic. ``test_disabling_the_gate_still_does_not_allow_minting_an_admin``
        in LearnerApiGateTests covers the inner defence with the gate switched
        off — both must hold, because either could be relaxed independently.
        """
        target = f"anon-{uuid.uuid4().hex[:10]}@kbc.invalid"
        try:
            response = self._create_staff(target, "Admin")

            self.assertEqual(response.status_code, 401)
            # The decisive assertion: no credential exists to be redeemed, and
            # no staff record was created to hang one off later.
            self.assertFalse(LoginAccount.objects.filter(email=target).exists())
            self.assertFalse(StaffUser.objects.filter(email=target).exists())
        finally:
            LoginAccount.objects.filter(email=target).delete()
            StaffUser.objects.filter(email=target).delete()

    def test_staff_cannot_invite_an_admin(self):
        """Otherwise any staff member could promote themselves."""
        self.make_account(position="Enrolment")
        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        target = f"esc-{uuid.uuid4().hex[:10]}@kbc.invalid"
        try:
            response = self._create_staff(target, "Admin", client=self.client, access="super-admin")
            invitation = response.json().get("invitation")

            self.assertTrue(invitation["forbidden"])
            self.assertFalse(LoginAccount.objects.filter(email=target).exists())
        finally:
            LoginAccount.objects.filter(email=target).delete()
            StaffUser.objects.filter(email=target).delete()

    def test_staff_may_invite_a_non_admin(self):
        """The gate must not break the ordinary case it exists to protect."""
        self.make_account(position="Enrolment")
        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        target = f"ok-{uuid.uuid4().hex[:10]}@kbc.invalid"
        try:
            response = self._create_staff(target, "Enrolment", client=self.client)
            invitation = response.json().get("invitation")

            self.assertFalse(invitation["forbidden"])
            self.assertTrue(invitation["invited"])
            self.assertTrue(LoginAccount.objects.filter(email=target).exists())
        finally:
            LoginAccount.objects.filter(email=target).delete()
            StaffUser.objects.filter(email=target).delete()

    def test_admin_may_invite_an_admin(self):
        self.make_account(position="Admin")
        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        target = f"adm-{uuid.uuid4().hex[:10]}@kbc.invalid"
        try:
            response = self._create_staff(target, "Admin", client=self.client, access="super-admin")
            invitation = response.json().get("invitation")

            self.assertFalse(invitation["forbidden"])
            self.assertTrue(invitation["invited"])
            minted = LoginAccount.objects.get(email=target)
            self.assertEqual(minted.role, "admin")
        finally:
            LoginAccount.objects.filter(email=target).delete()
            StaffUser.objects.filter(email=target).delete()

    def test_invite_endpoint_also_refuses_staff_inviting_an_admin(self):
        """The same rule holds on /login_api/accounts/invite/."""
        admin_target = StaffUser.objects.create(
            username="Some Admin",
            email=f"tgt-{uuid.uuid4().hex[:10]}@kbc.invalid",
            # Access is what makes them an administrator now; Position is a title.
            position="Admin", access="super-admin", type="Admin", status="FullUser",
        )
        self.make_account(position="Enrolment")
        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        try:
            response = self.post(
                "/login_api/accounts/invite/",
                {"subjectType": "staff", "subjectId": admin_target.id},
            )
            self.assertEqual(response.status_code, 403)
            self.assertFalse(
                LoginAccount.objects.filter(
                    subject_type="staff", subject_id=admin_target.id
                ).exists()
            )
        finally:
            LoginAccount.objects.filter(
                subject_type="staff", subject_id=admin_target.id
            ).delete()
            StaffUser.objects.filter(pk=admin_target.id).delete()

    def test_anonymous_learner_creation_does_not_mint_an_account(self):
        """The learner form is the same shape, with a lower-privileged role."""
        from learner_api.models import EnrolmentUser

        target = f"lrn-{uuid.uuid4().hex[:10]}@kbc.invalid"
        try:
            response = Client(SERVER_NAME="localhost").post(
                "/learner_api/enrolment-users/",
                data=json.dumps({
                    "username": "Anon Learner", "email": target,
                    "inviteToPlatform": True, "learnerType": "apprenticeship",
                }),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)
            self.assertFalse(LoginAccount.objects.filter(email=target).exists())
            self.assertFalse(EnrolmentUser.all_learners.filter(email=target).exists())
        finally:
            LoginAccount.objects.filter(email=target).delete()
            EnrolmentUser.all_learners.filter(email=target).delete()


class LearnerApiGateTests(LoginTestBase):
    """The `learner_api` write endpoints require an authenticated staff session.

    These endpoints had no authentication at all. Writes are now gated by
    ``login.permissions.staff_only(writes_only=True)``; reads are deliberately
    still open while the remaining unauthenticated frontend fetches are moved
    over, so the read assertions below pin *current* behaviour rather than the
    desired end state.
    """

    #: (label, url, payload) for every gated write path.
    WRITE_PATHS = (
        ("staff", "/learner_api/staff-users/",
         {"username": "X", "email": "gate-staff@kbc.invalid", "position": "Enrolment"}),
        ("learner", "/learner_api/enrolment-users/",
         {"username": "X", "email": "gate-learner@kbc.invalid"}),
        ("employer", "/learner_api/employers/",
         {"firstName": "X", "surname": "Y", "email": "gate-emp@kbc.invalid",
          "employerGroupIds": []}),
        ("organisation", "/learner_api/organisations/", {"name": "Gate Test Ltd"}),
    )

    def _anon_post(self, url, payload):
        return Client(SERVER_NAME="localhost").post(
            url, data=json.dumps(payload), content_type="application/json"
        )

    def test_anonymous_writes_are_refused(self):
        for label, url, payload in self.WRITE_PATHS:
            with self.subTest(endpoint=label):
                response = self._anon_post(url, payload)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["code"], "unauthenticated")

    def test_anonymous_writes_create_nothing(self):
        """A refused request must not leave a partial record behind."""
        before = {
            "staff": StaffUser.objects.count(),
            "accounts": LoginAccount.objects.count(),
        }
        for _, url, payload in self.WRITE_PATHS:
            self._anon_post(url, payload)

        self.assertEqual(StaffUser.objects.count(), before["staff"])
        self.assertEqual(LoginAccount.objects.count(), before["accounts"])

    def test_a_learner_session_cannot_write(self):
        """Being signed in is not enough — the role has to be staff or admin.

        Uses a real learner subject rather than forcing role='learner' onto a
        staff row: a staff account's role is re-derived from its Access grant on
        every request (sessions._refresh_staff_role), so a hand-edited value
        would be corrected before the assertion ran.
        """
        from learner_api.models import EnrolmentUser

        learner = EnrolmentUser.objects.create(
            username="Gate Learner", email=self.email, learner_type="apprenticeship",
        )
        self.addCleanup(lambda: EnrolmentUser.all_learners.filter(pk=learner.pk).delete())
        account, _ = identity.ensure_account("learner", learner.id, subject=learner)
        account.password_hash = hash_password(self.password)
        account.password_set_at = timezone.now()
        account.save()
        self._accounts.append(account)
        self.assertEqual(account.role, "learner")

        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        for label, url, payload in self.WRITE_PATHS:
            with self.subTest(endpoint=label):
                response = self.client.post(
                    url, data=json.dumps(payload), content_type="application/json", **XHR
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "forbidden")

    def test_a_staff_session_may_write(self):
        self.make_account(position="Enrolment")
        self.post("/login_api/login/", {"email": self.email, "password": self.password})

        target = f"gate-ok-{uuid.uuid4().hex[:8]}@kbc.invalid"
        try:
            response = self.client.post(
                "/learner_api/staff-users/",
                data=json.dumps({
                    "username": "Allowed", "email": target, "position": "Enrolment",
                }),
                content_type="application/json", **XHR,
            )
            self.assertEqual(response.status_code, 201)
        finally:
            StaffUser.objects.filter(email=target).delete()

    def test_reads_remain_open(self):
        """Documents current behaviour: only writes are gated so far."""
        anon = Client(SERVER_NAME="localhost")
        for url in ("/learner_api/staff-users/", "/learner_api/enrolment-users/"):
            with self.subTest(url=url):
                self.assertEqual(anon.get(url).status_code, 200)

    def test_the_gate_can_be_disabled_for_local_development(self):
        """LEARNER_API_REQUIRE_AUTH=0 is the documented escape hatch."""
        target = f"gate-off-{uuid.uuid4().hex[:8]}@kbc.invalid"
        try:
            with mock.patch.dict(
                "os.environ", {"LEARNER_API_REQUIRE_AUTH": "0"}, clear=False
            ):
                response = self._anon_post(
                    "/learner_api/staff-users/",
                    {"username": "Local", "email": target, "position": "Enrolment"},
                )
            self.assertEqual(response.status_code, 201)
        finally:
            StaffUser.objects.filter(email=target).delete()

    def test_disabling_the_gate_still_does_not_allow_minting_an_admin(self):
        """The invite authorisation is independent of the endpoint gate.

        Turning the gate off for local development must not re-open the
        privilege-escalation path — invite_subject checks the caller itself.
        """
        target = f"gate-esc-{uuid.uuid4().hex[:8]}@kbc.invalid"
        try:
            with mock.patch.dict(
                "os.environ", {"LEARNER_API_REQUIRE_AUTH": "0"}, clear=False
            ):
                response = self._anon_post(
                    "/learner_api/staff-users/",
                    {"username": "Local", "email": target,
                     "position": "Admin", "inviteToPlatform": True},
                )
            self.assertEqual(response.status_code, 201)
            self.assertTrue(response.json()["invitation"]["forbidden"])
            self.assertFalse(LoginAccount.objects.filter(email=target).exists())
        finally:
            LoginAccount.objects.filter(email=target).delete()
            StaffUser.objects.filter(email=target).delete()


class PermissionTests(LoginTestBase):
    def test_invite_endpoint_rejects_anonymous_callers(self):
        response = self.post(
            "/login_api/accounts/invite/", {"subjectType": "staff", "subjectId": 1}
        )
        self.assertEqual(response.status_code, 401)

    def test_invite_endpoint_rejects_a_learner_session(self):
        """Role enforcement is server-side, not just hidden in the UI."""
        # A real learner: a staff account's role is re-derived from its Access on
        # every request, so a hand-set role would be corrected before the check.
        from learner_api.models import EnrolmentUser

        staff_target = StaffUser.objects.create(
            username="Target", email=f"tgt-{uuid.uuid4().hex[:10]}@kbc.invalid",
            position="Enrolment", access="enrolment", type="Admin", status="FullUser",
        )
        self.addCleanup(lambda: StaffUser.objects.filter(pk=staff_target.pk).delete())
        learner = EnrolmentUser.objects.create(
            username="Gate Learner", email=self.email, learner_type="apprenticeship",
        )
        self.addCleanup(lambda: EnrolmentUser.all_learners.filter(pk=learner.pk).delete())
        account, _ = identity.ensure_account("learner", learner.id, subject=learner)
        account.password_hash = hash_password(self.password)
        account.password_set_at = timezone.now()
        account.save()
        self._accounts.append(account)

        self.post("/login_api/login/", {"email": self.email, "password": self.password})
        response = self.post(
            "/login_api/accounts/invite/", {"subjectType": "staff", "subjectId": staff_target.id}
        )
        self.assertEqual(response.status_code, 403)


class MicrosoftSsoCallbackTests(LoginTestBase):
    """Sign in with Microsoft: the login table is the gate.

    Microsoft says who the caller is; ``Login_accounts`` says whether that person
    may in. These pin the second half — the first is mocked out, because what a
    real token exchange returns is Microsoft's business and not something this
    suite can or should assert.
    """

    SSO_ENV = {
        "MICROSOFT_SSO_CLIENT_ID": "sso-client",
        "MICROSOFT_SSO_CLIENT_SECRET": "sso-secret",
        "MICROSOFT_SSO_TENANT_ID": "sso-tenant",
        "MICROSOFT_SSO_CALLBACK_URI": "https://lms.kbc.test/login_api/microsoft/callback/",
    }

    def _get(self, params):
        return self.client.get("/login_api/microsoft/callback/", params)

    def _begin(self, next_path=""):
        """Mint a state and put the paired nonce in the test client's cookie jar.

        This is what ``start`` does. The two halves have to travel together —
        the state proves the server minted it, the cookie proves this browser
        asked for it — so a test that wants a *working* callback needs both.
        """
        nonce = generate_token()
        self.client.cookies[microsoft_sso.NONCE_COOKIE] = nonce
        return signing.dumps(
            {"next": next_path, "n": hash_token(nonce)},
            salt=microsoft_sso.STATE_SALT,
            compress=True,
        )

    def _callback(self, *, signed_in_as, next_path=""):
        """Drive the callback as though Microsoft authenticated ``signed_in_as``."""
        state = self._begin(next_path)
        with (
            mock.patch.dict("os.environ", self.SSO_ENV, clear=False),
            mock.patch.object(microsoft_sso, "_exchange_code", return_value="access-token"),
            mock.patch.object(microsoft_sso, "_graph_email", return_value=signed_in_as),
        ):
            return self._get({"code": "auth-code", "state": state})

    def _assert_refused(self, response):
        """No session cookie, and the browser is sent back carrying a reason."""
        self.assertEqual(response.status_code, 302)
        self.assertIn("sso_error=", response["Location"])
        self.assertNotIn("kbc_session", response.cookies)

    # --- the address is in the login table ---

    def test_an_sso_session_is_not_persistent(self):
        """SSO has no "remember me" to tick, and does not assume one.

        Two things are pinned, because the cookie alone would not hold: the
        response carries a browser-session cookie, *and* the row says
        ``Remember = false`` -- which is what keeps every later renewal sending
        the same kind of cookie instead of promoting it.
        """
        self.make_account()

        cookie = self._callback(signed_in_as=self.email).cookies["kbc_session"]

        self.assertEqual(cookie["max-age"], "")
        self.assertEqual(cookie["expires"], "")
        session = LoginSession.objects.get(token_hash=hash_token(cookie.value))
        self.assertFalse(session.remember)

    def test_a_known_address_is_signed_in(self):
        account = self.make_account()
        response = self._callback(signed_in_as=self.email)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("sso_error=", response["Location"])
        token = response.cookies["kbc_session"].value
        self.assertTrue(token)
        self.assertTrue(
            LoginSession.objects.filter(
                account_id=account.id,
                token_hash=hash_token(token),
                revoked_at__isnull=True,
            ).exists()
        )

    def test_the_session_cookie_is_httponly(self):
        """The one control that keeps an XSS bug from becoming account theft.
        Set by the same helper the password path uses; this pins that the SSO
        path did not grow its own weaker copy."""
        self.make_account()
        cookie = self._callback(signed_in_as=self.email).cookies["kbc_session"]
        self.assertTrue(cookie["httponly"])

    def test_the_address_is_matched_case_insensitively(self):
        """Entra returns the UPN in whatever case the directory happens to hold."""
        self.make_account()
        response = self._callback(signed_in_as=self.email.upper())
        self.assertIn("kbc_session", response.cookies)

    def test_success_is_audited_as_an_sso_sign_in(self):
        """A password sign-in and an SSO one must be tellable apart afterwards."""
        account = self.make_account()
        self._callback(signed_in_as=self.email)
        row = LoginAudit.objects.filter(
            event="login", account_id=account.id, succeeded=True
        ).latest("id")
        self.assertEqual(row.reason, "microsoft_sso")

    def test_a_previous_return_path_is_replaced_by_account_home_routing(self):
        self.make_account()
        response = self._callback(signed_in_as=self.email, next_path="/workspace/admin")
        self.assertEqual(response["Location"], f"{microsoft_sso.frontend_base_url()}/")

    def test_an_account_that_never_set_a_password_may_still_sign_in(self):
        """Deliberate: their tenant account is the credential. The password form
        still refuses them, having nothing to verify against."""
        self.make_account(with_password=False)
        self.assertIn("kbc_session", self._callback(signed_in_as=self.email).cookies)

    # --- the address is not in the login table ---

    def test_an_unknown_address_is_refused_and_no_account_is_created(self):
        """The whole point of the feature: authenticating with Microsoft is not
        the same as being registered here."""
        stranger = f"nobody-{uuid.uuid4().hex[:12]}@kbc.invalid"
        before = LoginAccount.objects.count()
        try:
            self._assert_refused(self._callback(signed_in_as=stranger))
            self.assertEqual(LoginAccount.objects.count(), before)
            self.assertFalse(LoginAccount.objects.filter(email=stranger).exists())
        finally:
            LoginAudit.objects.filter(email=stranger).delete()

    def test_a_deactivated_account_is_refused(self):
        """Deactivating somebody has to close every door, not just the one."""
        self.make_account(active=False)
        self._assert_refused(self._callback(signed_in_as=self.email))

    def test_a_locked_account_is_refused(self):
        """Otherwise the password lockout is bypassable by anyone whose tenant
        account still works, which would make it decorative."""
        account = self.make_account()
        account.locked_until = timezone.now() + timedelta(minutes=30)
        account.save(update_fields=["locked_until"])
        self._assert_refused(self._callback(signed_in_as=self.email))

    def test_a_refusal_is_audited(self):
        stranger = f"nobody-{uuid.uuid4().hex[:12]}@kbc.invalid"
        try:
            self._callback(signed_in_as=stranger)
            row = LoginAudit.objects.filter(event="login", email=stranger).latest("id")
            self.assertFalse(row.succeeded)
            self.assertEqual(row.reason, "sso_unknown_account")
        finally:
            LoginAudit.objects.filter(email=stranger).delete()

    # --- the callback itself ---

    def test_a_callback_without_valid_state_is_refused(self):
        """The signed state is this endpoint's CSRF defence — it arrives as a
        top-level redirect and so cannot carry the X-Requested-With header the
        rest of the login API requires."""
        self.make_account()
        with (
            mock.patch.dict("os.environ", self.SSO_ENV, clear=False),
            mock.patch.object(microsoft_sso, "_exchange_code", return_value="access-token"),
            mock.patch.object(microsoft_sso, "_graph_email", return_value=self.email),
        ):
            response = self._get({"code": "auth-code", "state": "forged"})
        self._assert_refused(response)

    def test_an_expired_state_is_refused(self):
        self.make_account()
        stale = self._begin()
        with (
            mock.patch.dict("os.environ", self.SSO_ENV, clear=False),
            mock.patch.object(microsoft_sso, "STATE_MAX_AGE", -1),
            mock.patch.object(microsoft_sso, "_exchange_code", return_value="access-token"),
            mock.patch.object(microsoft_sso, "_graph_email", return_value=self.email),
        ):
            response = self._get({"code": "auth-code", "state": stale})
        self._assert_refused(response)

    def test_a_failed_token_exchange_does_not_leak_the_reason(self):
        """The exception can quote a client secret or an internal URL. It is
        logged in full; the browser is told only that it did not work."""
        self.make_account()
        state = self._begin()
        with (
            mock.patch.dict("os.environ", self.SSO_ENV, clear=False),
            mock.patch.object(
                microsoft_sso,
                "_exchange_code",
                side_effect=RuntimeError("client_secret=sso-secret rejected"),
            ),
        ):
            with self.assertLogs("login.sso", level="ERROR"):
                response = self._get({"code": "auth-code", "state": state})
        self._assert_refused(response)
        self.assertNotIn("sso-secret", response["Location"])

    def test_a_state_from_another_browser_is_refused(self):
        """Login CSRF. A signed state proves this server minted it, not that
        this browser asked for it. Without the pairing, somebody with a platform
        account could start a sign-in, stop the redirect on their own machine
        and hand the finished callback URL to another person — whose browser
        would complete it and be signed in as the attacker, so everything they
        then wrote landed in the attacker's account."""
        self.make_account()
        state = self._begin()
        # The victim's browser holds no nonce, only the URL it was given.
        self.client.cookies.pop(microsoft_sso.NONCE_COOKIE)
        with (
            mock.patch.dict("os.environ", self.SSO_ENV, clear=False),
            mock.patch.object(microsoft_sso, "_exchange_code", return_value="access-token"),
            mock.patch.object(microsoft_sso, "_graph_email", return_value=self.email),
        ):
            response = self._get({"code": "auth-code", "state": state})
        self._assert_refused(response)

    def test_a_mismatched_nonce_is_refused(self):
        """A browser mid-flight for a *different* sign-in must not complete
        this one — the cookie has to match the state, not merely exist."""
        self.make_account()
        state = self._begin()
        self.client.cookies[microsoft_sso.NONCE_COOKIE] = generate_token()
        with (
            mock.patch.dict("os.environ", self.SSO_ENV, clear=False),
            mock.patch.object(microsoft_sso, "_exchange_code", return_value="access-token"),
            mock.patch.object(microsoft_sso, "_graph_email", return_value=self.email),
        ):
            response = self._get({"code": "auth-code", "state": state})
        self._assert_refused(response)

    def test_the_nonce_is_retired_after_a_successful_sign_in(self):
        """One cookie completes exactly one sign-in, so a callback URL that
        leaks from history cannot be replayed on the same machine."""
        self.make_account()
        response = self._callback(signed_in_as=self.email)
        self.assertEqual(response.cookies[microsoft_sso.NONCE_COOKIE].value, "")

    def test_the_nonce_cookie_is_httponly(self):
        """It is the whole binding. Readable from JS, an XSS bug could mint a
        callback URL that works anywhere."""
        state_response = self._start()
        cookie = state_response.cookies[microsoft_sso.NONCE_COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")

    def _start(self):
        with mock.patch.dict("os.environ", self.SSO_ENV, clear=False):
            return self.client.get("/login_api/microsoft/start/")

    def test_start_binds_the_state_to_the_nonce_it_sets(self):
        """The state carries only the hash — it travels through Microsoft and
        sits in browser history, so it should be no more useful than one."""
        from urllib.parse import parse_qs, urlparse

        response = self._start()
        nonce = response.cookies[microsoft_sso.NONCE_COOKIE].value
        url = json.loads(response.content)["authorizationUrl"]
        state = parse_qs(urlparse(url).query)["state"][0]
        payload = signing.loads(state, salt=microsoft_sso.STATE_SALT)

        self.assertEqual(payload["n"], hash_token(nonce))
        self.assertNotIn(nonce, state)

    def test_a_cancelled_consent_is_reported_not_crashed(self):
        with mock.patch.dict("os.environ", self.SSO_ENV, clear=False):
            response = self._get(
                {"error": "access_denied", "error_description": "The user cancelled."}
            )
        self._assert_refused(response)
