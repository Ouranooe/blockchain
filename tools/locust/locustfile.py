"""迭代 8：Locust 100 并发 API 压测。

运行：
  cd tools/locust
  pip install locust==2.31.5
  locust -f locustfile.py --host http://localhost:8000 -u 100 -r 20 -t 2m --headless --csv locust-report

目标：
  - 100 并发下 P95 < 500 ms
  - 错误率 < 1%
"""

import random
import uuid

from locust import HttpUser, between, task


SEED_PATIENTS = ["patient1", "patient2"]
SEED_PASSWORD = "123456"


class MedShareUser(HttpUser):
    wait_time = between(0.5, 2.0)
    token = None

    def on_start(self):
        username = random.choice(SEED_PATIENTS)
        resp = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": SEED_PASSWORD},
            name="POST /auth/login",
        )
        if resp.status_code == 200:
            self.token = resp.json()["token"]

    def _auth_headers(self):
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    @task(5)
    def list_records(self):
        self.client.get(
            "/api/patient/records",
            headers=self._auth_headers(),
            name="GET /patient/records",
        )

    @task(3)
    def pending_requests_chain(self):
        self.client.get(
            "/api/access-requests/chain/pending",
            headers=self._auth_headers(),
            name="GET /access-requests/chain/pending",
        )

    @task(2)
    def whoami(self):
        self.client.get(
            "/api/auth/me",
            headers=self._auth_headers(),
            name="GET /auth/me",
        )

    @task(1)
    def audit_events(self):
        self.client.get(
            "/api/audit/events?limit=20",
            headers=self._auth_headers(),
            name="GET /audit/events",
        )


class HospitalUser(HttpUser):
    """更重的"医院视角"：偶尔上传一条文本病历。"""

    wait_time = between(1.0, 4.0)
    weight = 1  # 患者 3 : 医院 1
    token = None
    hospital_username = None

    def on_start(self):
        self.hospital_username = random.choice(["hospital_a", "hospital_b"])
        resp = self.client.post(
            "/api/auth/login",
            json={"username": self.hospital_username, "password": SEED_PASSWORD},
            name="POST /auth/login",
        )
        if resp.status_code == 200:
            self.token = resp.json()["token"]

    def _auth_headers(self):
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    @task(5)
    def list_records(self):
        self.client.get(
            "/api/records", headers=self._auth_headers(), name="GET /records"
        )

    @task(2)
    def chain_by_hospital(self):
        self.client.get(
            "/api/records/chain/by-hospital?page_size=50",
            headers=self._auth_headers(),
            name="GET /records/chain/by-hospital",
        )

    @task(1)
    def upload_text_record(self):
        self.client.post(
            "/api/records",
            headers=self._auth_headers(),
            json={
                "patient_id": 2,
                "title": f"压测 #{uuid.uuid4().hex[:8]}",
                "diagnosis": "压测数据",
                "content": "Locust-generated",
            },
            name="POST /records",
        )
