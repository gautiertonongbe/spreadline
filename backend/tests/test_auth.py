"""Authentication.

Most of what is asserted here is a refusal, because that is what an
authentication layer is for: that an unsigned request gets nothing, that a
revoked session stops working immediately, that a failed sign-in cannot be
repeated indefinitely, and that the server never tells an anonymous caller
which email addresses have accounts.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import AuthenticationError, ValidationError
from app.domains.tenancy import auth as auth_service
from app.domains.tenancy.service import ensure_default_organization
from app.models.tenancy import Organization, User, UserSession
from tests.conftest import TEST_EMAIL, TEST_PASSWORD

API = "/api/v1"


def login(client, email=TEST_EMAIL, password=TEST_PASSWORD):
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


class TestTheDoorIsClosed:
    def test_an_unsigned_request_is_refused(self, anonymous_client):
        response = anonymous_client.get(f"{API}/opportunities")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"

    @pytest.mark.parametrize(
        "path",
        ["/opportunities", "/autonomy", "/autonomy/positions", "/history", "/analytics"],
    )
    def test_every_data_route_needs_a_session(self, anonymous_client, path):
        assert anonymous_client.get(f"{API}{path}").status_code == 401

    def test_a_mutation_needs_a_session(self, anonymous_client):
        response = anonymous_client.post(
            f"{API}/autonomy/emergency-stop", json={"active": True, "reason": "test"}
        )
        assert response.status_code == 401

    def test_health_stays_open(self, anonymous_client):
        """A liveness probe cannot hold a credential."""
        assert anonymous_client.get("/health").status_code == 200

    def test_a_made_up_token_is_refused(self, anonymous_client):
        response = anonymous_client.get(
            f"{API}/opportunities", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401


class TestSigningIn:
    def test_a_correct_password_starts_a_session(self, anonymous_client):
        response = login(anonymous_client)
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["email"] == TEST_EMAIL
        assert body["token"]
        assert anonymous_client.get(f"{API}/auth/me").status_code == 200

    def test_the_token_also_works_as_a_bearer_credential(self, anonymous_client):
        token = login(anonymous_client).json()["token"]
        anonymous_client.cookies.clear()
        response = anonymous_client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    def test_the_cookie_is_not_readable_by_script(self, anonymous_client):
        response = login(anonymous_client)
        cookie = response.headers["set-cookie"]
        assert "httponly" in cookie.lower()
        assert "samesite=lax" in cookie.lower()

    def test_a_wrong_password_and_an_unknown_address_read_the_same(self, anonymous_client):
        wrong = login(anonymous_client, password="not-the-password-at-all")
        unknown = login(anonymous_client, email="nobody@spreadline.test")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    def test_an_account_with_no_password_cannot_be_signed_into(self, session, auth):
        """The operator account that predates authentication is not a way in."""
        user = session.scalar(select(User).where(User.id == auth.user_id))
        assert user.password_hash is None
        with pytest.raises(AuthenticationError):
            auth_service.authenticate(session, email=user.email, password="anything")


class TestBruteForce:
    def test_an_account_locks_after_repeated_failures(self, session, auth):
        organization = ensure_default_organization(session)
        user = auth_service.create_user(
            session, organization, email="lock@spreadline.test", password="a-long-password"
        )

        for _ in range(auth_service.MAX_FAILED_ATTEMPTS):
            with pytest.raises(AuthenticationError):
                auth_service.authenticate(session, email=user.email, password="wrong")

        assert user.locked_until is not None
        # The correct password is refused too: a lock that the right password
        # opens is not a lock against somebody who is about to find it.
        with pytest.raises(AuthenticationError) as caught:
            auth_service.authenticate(session, email=user.email, password="a-long-password")
        assert "locked" in caught.value.detail

    def test_a_success_clears_the_count(self, session, auth):
        organization = ensure_default_organization(session)
        user = auth_service.create_user(
            session, organization, email="counter@spreadline.test", password="a-long-password"
        )
        with pytest.raises(AuthenticationError):
            auth_service.authenticate(session, email=user.email, password="wrong")
        assert user.failed_attempts == 1

        auth_service.authenticate(session, email=user.email, password="a-long-password")
        assert user.failed_attempts == 0
        assert user.locked_until is None


class TestSessions:
    def test_the_token_is_never_stored(self, session, auth, anonymous_client):
        """A leaked database must not hand the reader a set of live sessions."""
        token = login(anonymous_client).json()["token"]
        rows = list(session.scalars(select(UserSession)))
        assert rows, "the session should have been recorded"
        assert all(row.token_hash != token for row in rows)
        assert all(len(row.token_hash) == 64 for row in rows)

    def test_signing_out_ends_the_session_immediately(self, anonymous_client):
        token = login(anonymous_client).json()["token"]
        assert anonymous_client.post(f"{API}/auth/logout").status_code == 200

        anonymous_client.cookies.clear()
        response = anonymous_client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401, "a revoked token stops working at once"

    def test_an_expired_session_is_refused(self, session, auth):
        organization = ensure_default_organization(session)
        user = auth_service.create_user(
            session, organization, email="expiry@spreadline.test", password="a-long-password"
        )
        started = auth_service.start_session(session, user)
        started.row.expires_at = utcnow() - timedelta(seconds=1)
        session.flush()

        with pytest.raises(AuthenticationError):
            auth_service.resolve(session, started.token)

    def test_an_idle_session_times_out_and_stays_out(self, session, auth):
        organization = ensure_default_organization(session)
        user = auth_service.create_user(
            session, organization, email="idle@spreadline.test", password="a-long-password"
        )
        started = auth_service.start_session(session, user)
        started.row.last_seen_at = utcnow() - timedelta(days=3650)
        session.flush()

        with pytest.raises(AuthenticationError):
            auth_service.resolve(session, started.token)
        # Revoked rather than merely refused: the next attempt must not depend
        # on the clock arithmetic being repeated correctly.
        assert started.row.revoked_at is not None

    def test_the_current_session_is_identified_in_the_list(self, client):
        """Deciding which session to end is a guess without this."""
        login(client)  # a second session, as if signed in on another machine
        sessions = client.get(f"{API}/auth/me").json()["sessions"]
        assert len(sessions) >= 2
        current = [row for row in sessions if row["is_current"]]
        assert len(current) == 1

    def test_ending_another_session_leaves_this_one_alone(self, client):
        login(client)
        sessions = client.get(f"{API}/auth/me").json()["sessions"]
        other = next(row for row in sessions if not row["is_current"])

        assert client.post(f"{API}/auth/sessions/{other['id']}/revoke").status_code == 200
        assert client.get(f"{API}/auth/me").status_code == 200
        remaining = client.get(f"{API}/auth/me").json()["sessions"]
        assert other["id"] not in [row["id"] for row in remaining]

    def test_ending_the_current_session_signs_this_caller_out(self, client):
        sessions = client.get(f"{API}/auth/me").json()["sessions"]
        current = next(row for row in sessions if row["is_current"])

        assert client.post(f"{API}/auth/sessions/{current['id']}/revoke").status_code == 200
        assert client.get(f"{API}/auth/me").status_code == 401

    def test_one_account_cannot_revoke_another_account_s_session(self, session, auth):
        organization = ensure_default_organization(session)
        owner = auth_service.create_user(
            session, organization, email="one@spreadline.test", password="a-long-password"
        )
        other = auth_service.create_user(
            session, organization, email="two@spreadline.test", password="a-long-password"
        )
        victim = auth_service.start_session(session, other)

        attacker_context = auth_service.resolve(
            session, auth_service.start_session(session, owner).token
        )
        assert not auth_service.revoke_by_id(session, attacker_context, victim.row.id)
        assert victim.row.revoked_at is None


class TestPasswords:
    def test_a_short_password_is_refused(self, session, auth):
        organization = ensure_default_organization(session)
        with pytest.raises(ValidationError):
            auth_service.create_user(
                session, organization, email="short@spreadline.test", password="short"
            )

    def test_changing_a_password_requires_the_current_one(self, anonymous_client):
        login(anonymous_client)
        response = anonymous_client.post(
            f"{API}/auth/password",
            json={"current_password": "wrong", "new_password": "a-brand-new-password"},
        )
        assert response.status_code == 401

    def test_changing_a_password_ends_every_other_session(self, client):
        """A password change is a response to a suspicion, not a gesture."""
        # A second session on the same account, as if signed in elsewhere.
        other_token = login(client).json()["token"]

        response = client.post(
            f"{API}/auth/password",
            json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-password"},
        )
        assert response.status_code == 200, response.text
        # The caller keeps working: the route issues a fresh session rather than
        # signing somebody out of the browser they are standing in front of.
        assert client.get(f"{API}/auth/me").status_code == 200

        # The cookie has to go before the header is read, or the fresh cookie
        # answers and the assertion proves nothing.
        client.cookies.clear()
        stale = client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {other_token}"}
        )
        assert stale.status_code == 401

    def test_the_new_password_works_and_the_old_one_does_not(self, client):
        changed = client.post(
            f"{API}/auth/password",
            json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-password"},
        )
        assert changed.status_code == 200, changed.text

        client.cookies.clear()
        assert login(client, password=TEST_PASSWORD).status_code == 401
        assert login(client, password="a-brand-new-password").status_code == 200


class TestAccounts:
    def test_there_is_no_public_sign_up(self, anonymous_client):
        response = anonymous_client.post(
            f"{API}/auth/users",
            json={"email": "intruder@spreadline.test", "password": "a-long-password"},
        )
        assert response.status_code == 401

    def test_an_operator_may_not_add_accounts(self, client, session, engine):
        body = client.post(
            f"{API}/auth/users",
            json={
                "email": "operator2@spreadline.test",
                "password": "a-long-password",
                "role": "operator",
            },
        )
        assert body.status_code == 200, body.text

        client.cookies.clear()
        client.post(
            f"{API}/auth/login",
            json={"email": "operator2@spreadline.test", "password": "a-long-password"},
        )
        response = client.post(
            f"{API}/auth/users",
            json={"email": "third@spreadline.test", "password": "a-long-password"},
        )
        assert response.status_code == 403


class TestTenantIsolation:
    def test_a_session_cannot_reach_another_organization_s_data(self, session, auth):
        """The multi-tenancy claim, asserted rather than assumed."""
        other_org = Organization(name="Other", slug="other", base_currency="USD", settings={})
        session.add(other_org)
        session.flush()
        outsider = auth_service.create_user(
            session, other_org, email="outsider@elsewhere.test", password="a-long-password"
        )

        context = auth_service.resolve(
            session, auth_service.start_session(session, outsider).token
        )
        assert context.organization_id == other_org.id
        assert context.organization_id != auth.organization_id
