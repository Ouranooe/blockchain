"""迭代 1：认证相关接口测试（登录 / 注册 / 改密 / 禁用账号）。"""

from app.security import is_hashed


class TestLogin:
    def test_login_success_returns_token_and_user(self, client, make_user):
        make_user("alice", password="secret123", role="patient", real_name="Alice")
        resp = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"]
        assert body["user"]["username"] == "alice"
        assert body["user"]["role"] == "patient"
        assert body["user"]["is_active"] is True

    def test_login_wrong_password(self, client, make_user):
        make_user("bob", password="correct-pw", role="patient")
        resp = client.post(
            "/api/auth/login",
            json={"username": "bob", "password": "wrong-pw"},
        )
        assert resp.status_code == 401

    def test_login_user_not_found(self, client):
        resp = client.post(
            "/api/auth/login",
            json={"username": "ghost", "password": "whatever"},
        )
        assert resp.status_code == 401

    def test_login_disabled_account(self, client, make_user):
        make_user("inactive", password="123456", is_active=False)
        resp = client.post(
            "/api/auth/login",
            json={"username": "inactive", "password": "123456"},
        )
        assert resp.status_code == 403

    def test_legacy_plaintext_password_migrates_to_bcrypt(
        self, client, make_user, db_session
    ):
        # 模拟旧种子数据：明文存储 "123456"
        user = make_user("legacy", password="123456", hashed=False)
        assert not is_hashed(user.password)

        resp = client.post(
            "/api/auth/login",
            json={"username": "legacy", "password": "123456"},
        )
        assert resp.status_code == 200

        db_session.refresh(user)
        assert is_hashed(user.password), "登录成功后应将旧明文密码迁移为 bcrypt 哈希"

        # 哈希后下次仍可用同一明文登录
        resp2 = client.post(
            "/api/auth/login",
            json={"username": "legacy", "password": "123456"},
        )
        assert resp2.status_code == 200


class TestRegister:
    def test_register_patient_success(self, client):
        resp = client.post(
            "/api/auth/register",
            json={
                "username": "newbie",
                "password": "strongpw",
                "real_name": "王五",
                "role": "patient",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "newbie"
        assert body["role"] == "patient"

        # 立即登录应成功
        login = client.post(
            "/api/auth/login",
            json={"username": "newbie", "password": "strongpw"},
        )
        assert login.status_code == 200

    def test_register_duplicate_username_rejected(self, client, make_user):
        make_user("dup")
        resp = client.post(
            "/api/auth/register",
            json={
                "username": "dup",
                "password": "strongpw",
                "real_name": "dup",
                "role": "patient",
            },
        )
        assert resp.status_code == 409

    def test_register_non_patient_role_rejected(self, client):
        resp = client.post(
            "/api/auth/register",
            json={
                "username": "wannabe_hospital",
                "password": "strongpw",
                "real_name": "x",
                "role": "hospital",
            },
        )
        assert resp.status_code == 400

    def test_register_short_password_rejected(self, client):
        resp = client.post(
            "/api/auth/register",
            json={
                "username": "shorty",
                "password": "123",
                "real_name": "x",
                "role": "patient",
            },
        )
        assert resp.status_code == 422  # Pydantic 校验失败

    def test_register_invalid_username_rejected(self, client):
        resp = client.post(
            "/api/auth/register",
            json={
                "username": "bad name!",
                "password": "goodpw1",
                "real_name": "x",
                "role": "patient",
            },
        )
        assert resp.status_code == 422


class TestChangePassword:
    def test_change_password_success(self, client, make_user, login_token):
        make_user("changer", password="oldpass1", role="patient")
        token = login_token("changer", "oldpass1")
        resp = client.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"old_password": "oldpass1", "new_password": "newpass1"},
        )
        assert resp.status_code == 200

        # 老密码应失效、新密码应生效
        bad = client.post(
            "/api/auth/login",
            json={"username": "changer", "password": "oldpass1"},
        )
        assert bad.status_code == 401
        good = client.post(
            "/api/auth/login",
            json={"username": "changer", "password": "newpass1"},
        )
        assert good.status_code == 200

    def test_change_password_wrong_old(self, client, make_user, login_token):
        make_user("changer2", password="oldpass1", role="patient")
        token = login_token("changer2", "oldpass1")
        resp = client.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"old_password": "wrong-old", "new_password": "newpass1"},
        )
        assert resp.status_code == 400

    def test_change_password_same_as_old(self, client, make_user, login_token):
        make_user("changer3", password="samepass", role="patient")
        token = login_token("changer3", "samepass")
        resp = client.post(
            "/api/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"old_password": "samepass", "new_password": "samepass"},
        )
        assert resp.status_code == 400

    def test_change_password_requires_auth(self, client):
        resp = client.post(
            "/api/auth/change-password",
            json={"old_password": "a", "new_password": "bbbbbb"},
        )
        assert resp.status_code in (401, 403)


class TestWhoAmI:
    def test_whoami_returns_current_user(self, client, make_user, login_token):
        make_user(
            "hospA",
            password="123456",
            role="hospital",
            hospital_name="HospitalA",
            msp_org="Org1MSP",
        )
        token = login_token("hospA", "123456")
        resp = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "hospA"
        assert body["hospital_name"] == "HospitalA"
        assert body["msp_org"] == "Org1MSP"

    def test_whoami_rejected_when_disabled_after_token(
        self, client, make_user, login_token, db_session
    ):
        user = make_user("willbe_banned", password="123456", role="patient")
        token = login_token("willbe_banned", "123456")

        user.is_active = False
        db_session.commit()

        resp = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403
