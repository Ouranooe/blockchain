import requests

from .config import settings


GATEWAY_TIMEOUT_SECONDS = 20
GATEWAY_READY_TIMEOUT_SECONDS = 5


def _hospital_to_org(hospital_name: str) -> str:
    normalized = (hospital_name or "").strip().lower()
    if normalized in {"hospitala", "hospital_a", "org1", "hospital a"}:
        return "org1"
    if normalized in {"hospitalb", "hospital_b", "org2", "hospital b"}:
        return "org2"
    return "org1"


def _gateway_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or response.reason
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("detail") or payload)
    return str(payload)


def _request(method: str, path: str, **kwargs) -> dict:
    url = f"{settings.GATEWAY_URL}{path}"
    try:
        response = requests.request(method, url, timeout=GATEWAY_TIMEOUT_SECONDS, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as exc:
        response = exc.response
        if response is not None:
            raise RuntimeError(
                f"调用 Gateway 失败({response.status_code}): {_gateway_error_detail(response)}"
            ) from exc
        raise RuntimeError(f"调用 Gateway 失败: {exc}") from exc
    except requests.Timeout as exc:
        raise RuntimeError(f"调用 Gateway 超时: {url}") from exc
    except requests.ConnectionError as exc:
        raise RuntimeError(f"无法连接 Gateway: {url}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"调用 Gateway 失败: {exc}") from exc


def _post(path: str, payload: dict) -> dict:
    return _request("POST", path, json=payload)


def _get(path: str) -> dict:
    return _request("GET", path)


def check_gateway_ready() -> dict:
    ready_url = settings.GATEWAY_URL.rsplit("/api", 1)[0] + "/ready"
    try:
        response = requests.get(ready_url, timeout=GATEWAY_READY_TIMEOUT_SECONDS)
        payload = response.json()
        if response.ok:
            return payload
        raise RuntimeError(f"Gateway 未就绪({response.status_code}): {payload}")
    except requests.Timeout as exc:
        raise RuntimeError(f"Gateway readiness 超时: {ready_url}") from exc
    except requests.ConnectionError as exc:
        raise RuntimeError(f"无法连接 Gateway readiness: {ready_url}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Gateway readiness 检查失败: {exc}") from exc


def create_record_evidence(
    *,
    hospital_name: str,
    record_id: int,
    patient_id: int,
    data_hash: str,
    created_at: str,
) -> dict:
    return _post(
        "/records/evidence",
        {
            "org": _hospital_to_org(hospital_name),
            "recordId": str(record_id),
            "patientId": str(patient_id),
            "uploaderHospital": hospital_name,
            "dataHash": data_hash,
            "createdAt": created_at,
        },
    )


def create_access_request(
    *,
    hospital_name: str,
    request_id: int,
    record_id: int,
    patient_id: int,
    reason_hash: str,
    created_at: str,
) -> dict:
    return _post(
        "/access-requests",
        {
            "org": _hospital_to_org(hospital_name),
            "requestId": str(request_id),
            "recordId": str(record_id),
            "applicantHospital": hospital_name,
            "patientId": str(patient_id),
            "reasonHash": reason_hash,
            "status": "PENDING",
            "createdAt": created_at,
        },
    )


def approve_access_request(
    *,
    hospital_name: str,
    request_id: int,
    reviewed_at: str,
    duration_days: int,
    max_reads: int,
) -> dict:
    """迭代 5：审批批准必须携带有效期天数与最大读取次数。"""
    return _post(
        f"/access-requests/{request_id}/approve",
        {
            "org": _hospital_to_org(hospital_name),
            "reviewedAt": reviewed_at,
            "durationDays": int(duration_days),
            "maxReads": int(max_reads),
        },
    )


def reject_access_request(*, hospital_name: str, request_id: int, reviewed_at: str) -> dict:
    return _post(
        f"/access-requests/{request_id}/reject",
        {"org": _hospital_to_org(hospital_name), "reviewedAt": reviewed_at},
    )


def revoke_access_request(
    *,
    org_hint: str,
    request_id: int,
    patient_id: int,
    revoked_at: str,
) -> dict:
    return _post(
        f"/access-requests/{request_id}/revoke",
        {
            "org": _hospital_to_org(org_hint),
            "patientId": str(patient_id),
            "revokedAt": revoked_at,
        },
    )


def access_record_consume(
    *,
    hospital_name: str,
    request_id: int,
    accessed_at: str,
) -> dict:
    """迭代 5：链上授权消费。链码会校验状态/过期/次数/MSP 并扣减 remainingReads。"""
    return _post(
        f"/access-requests/{request_id}/access",
        {
            "org": _hospital_to_org(hospital_name),
            "accessedAt": accessed_at,
        },
    )


def query_access_request(request_id: int) -> dict:
    return _get(f"/access-requests/{request_id}")


# ---------- 迭代 2：病历版本链 ----------

def revise_record_evidence(
    *,
    hospital_name: str,
    record_id: int,
    new_data_hash: str,
    updated_at: str,
) -> dict:
    return _post(
        f"/records/evidence/{record_id}/revise",
        {
            "org": _hospital_to_org(hospital_name),
            "newDataHash": new_data_hash,
            "updatedAt": updated_at,
        },
    )


def query_record_version(record_id: int, version: int) -> dict:
    return _get(f"/records/evidence/{record_id}/version/{version}")


def query_record_latest(record_id: int) -> dict:
    return _get(f"/records/evidence/{record_id}")


# ---------- 迭代 3：Fabric 原生历史查询 ----------

def query_record_history(record_id: int) -> dict:
    return _get(f"/records/evidence/{record_id}/history")


def query_access_request_history(request_id: int) -> dict:
    return _get(f"/access-requests/{request_id}/history")


# ---------- 迭代 7：CouchDB 富查询 ----------

def query_records_by_hospital(
    *,
    uploader_hospital: str,
    page_size: int = 20,
    bookmark: str = "",
) -> dict:
    from urllib.parse import urlencode

    params = {
        "uploaderHospital": uploader_hospital,
        "pageSize": str(int(page_size)),
        "bookmark": bookmark or "",
    }
    return _get(f"/records/query/by-hospital?{urlencode(params)}")


def query_records_by_date(
    *,
    date_from: str,
    date_to: str,
    page_size: int = 20,
    bookmark: str = "",
) -> dict:
    from urllib.parse import urlencode

    params = {
        "from": date_from,
        "to": date_to,
        "pageSize": str(int(page_size)),
        "bookmark": bookmark or "",
    }
    return _get(f"/records/query/by-date?{urlencode(params)}")


def query_pending_requests_for_patient(
    *,
    patient_id: int,
    page_size: int = 20,
    bookmark: str = "",
) -> dict:
    from urllib.parse import urlencode

    params = {
        "patientId": str(int(patient_id)),
        "pageSize": str(int(page_size)),
        "bookmark": bookmark or "",
    }
    return _get(f"/access-requests/query/pending-for-patient?{urlencode(params)}")
