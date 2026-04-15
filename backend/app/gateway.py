import requests

from .config import settings


def _hospital_to_org(hospital_name: str) -> str:
    normalized = (hospital_name or "").strip().lower()
    if normalized in {"hospitala", "hospital_a", "org1", "hospital a"}:
        return "org1"
    if normalized in {"hospitalb", "hospital_b", "org2", "hospital b"}:
        return "org2"
    return "org1"


def _post(path: str, payload: dict) -> dict:
    url = f"{settings.GATEWAY_URL}{path}"
    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"调用 Gateway 失败: {exc}") from exc


def _get(path: str) -> dict:
    url = f"{settings.GATEWAY_URL}{path}"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"调用 Gateway 失败: {exc}") from exc


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
            "reasonHash": reason_hash,
            "status": "PENDING",
            "createdAt": created_at,
        },
    )


def approve_access_request(*, hospital_name: str, request_id: int, reviewed_at: str) -> dict:
    return _post(
        f"/access-requests/{request_id}/approve",
        {"org": _hospital_to_org(hospital_name), "reviewedAt": reviewed_at},
    )


def reject_access_request(*, hospital_name: str, request_id: int, reviewed_at: str) -> dict:
    return _post(
        f"/access-requests/{request_id}/reject",
        {"org": _hospital_to_org(hospital_name), "reviewedAt": reviewed_at},
    )


def query_access_request(request_id: int) -> dict:
    return _get(f"/access-requests/{request_id}")
