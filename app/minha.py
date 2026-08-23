from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .channel_store import ChannelStore, ChannelStoreError


class MinHaUnavailable(RuntimeError):
    pass


class MinHaClient:
    def __init__(
        self, base_url: str, auth_token: str = "", client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.client = client

    def _request(self, method: str, path: str, *, timeout: float = 3) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}
        try:
            requester = self.client.request if self.client else httpx.request
            return requester(method, f"{self.base_url}{path}", headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise MinHaUnavailable from exc

    def _get(self, path: str) -> httpx.Response:
        return self._request("GET", path)

    def _json_request(
        self, method: str, path: str, *, timeout: float = 3,
    ) -> dict[str, Any]:
        response = self._request(method, path, timeout=timeout)
        if response.status_code >= 400:
            raise MinHaUnavailable
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinHaUnavailable from exc
        if not isinstance(payload, dict):
            raise MinHaUnavailable
        return payload

    def list_profiles(self) -> list[dict[str, Any]]:
        response = self._get("/api/profiles")
        if response.status_code != 200:
            raise MinHaUnavailable
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinHaUnavailable from exc
        if not isinstance(payload, list):
            raise MinHaUnavailable
        return payload

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        response = self._get(f"/api/profiles/{quote(profile_id, safe='')}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise MinHaUnavailable
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinHaUnavailable from exc
        return payload if isinstance(payload, dict) else None

    def profile_status(self, profile_id: str) -> dict[str, Any]:
        return self._json_request("GET", f"/api/profiles/{quote(profile_id, safe='')}/status")

    def probe_tiktok(self, profile_id: str) -> dict[str, Any]:
        return self._json_request(
            "POST", f"/api/profiles/{quote(profile_id, safe='')}/tiktok-probe", timeout=90,
        )

    def launch_profile(self, profile_id: str) -> dict[str, Any]:
        return self._json_request(
            "POST", f"/api/profiles/{quote(profile_id, safe='')}/launch", timeout=60,
        )

    def stop_profile(self, profile_id: str) -> dict[str, Any]:
        return self._json_request(
            "POST", f"/api/profiles/{quote(profile_id, safe='')}/stop", timeout=30,
        )


PROFILE_FIELDS = (
    "id", "name", "tiktok_username", "tiktok_uid", "expected_tiktok_uid",
    "tiktok_account_match",
)


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {field: profile.get(field) for field in PROFILE_FIELDS}


def resolve_channel_publish_target(
    channel_id: str, channel_store: ChannelStore, minha_client: MinHaClient,
) -> dict[str, Any]:
    try:
        channel = next(
            item for item in channel_store.list() if item.channel_id == channel_id
        )
    except StopIteration:
        return {"status": "CHANNEL_NOT_FOUND", "channel_id": channel_id}
    except ChannelStoreError:
        return {"status": "CHANNEL_NOT_FOUND", "channel_id": channel_id}

    profile_id = channel.minha_profile_id
    result = {
        "status": "MINHA_PROFILE_UNASSIGNED",
        "channel_id": channel_id,
        "minha_profile_id": profile_id,
    }
    if not profile_id:
        return result
    try:
        profile = minha_client.get_profile(profile_id)
    except MinHaUnavailable:
        result["status"] = "MINHA_UNAVAILABLE"
        return result
    if not profile:
        result["status"] = "MINHA_PROFILE_NOT_FOUND"
        return result

    states = {
        "MATCH": "OK",
        "UNLOCKED": "UID_UNLOCKED",
        "MISMATCH": "ACCOUNT_MISMATCH",
        "NOT_LOGGED_IN": "NOT_LOGGED_IN",
        "NOT_DETECTED": "UID_NOT_DETECTED",
        "ERROR": "PROBE_ERROR",
    }
    result["status"] = states.get(profile.get("tiktok_account_match"), "PROBE_ERROR")
    result["profile"] = public_profile(profile)
    return result
