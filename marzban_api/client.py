from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta
from typing import Any

import httpx


class MarzbanError(RuntimeError):
    pass


class MarzbanClient:
    """
    Marzban API examples used by this client:
    - POST /api/admin/token {"username": "...", "password": "..."}
    - POST /api/user {"username": "...", "expire": <unix>, "data_limit": <bytes>}
    - PUT  /api/user/{username}
    - DELETE /api/user/{username}
    - GET /api/user/{username}
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        insecure_tls: bool = False,
        vless_inbound_tag: str = "VLESS-Reality",
        vless_flow: str = "xtls-rprx-vision",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.insecure_tls = insecure_tls
        self.vless_inbound_tag = vless_inbound_tag
        self.vless_flow = vless_flow
        self._token: str | None = None

    async def get_inbounds(self) -> dict[str, Any]:
        return await self._request("GET", "/api/inbounds")

    async def list_nodes(self) -> list[dict[str, Any]]:
        """
        Try to fetch nodes/servers from Marzban. Endpoint names can vary by version.
        Returns a list of dicts (best-effort). Empty list if unsupported.
        """
        for path in ("/api/nodes", "/api/node", "/api/servers", "/api/hosts"):
            try:
                payload = await self._request("GET", path)
            except MarzbanError:
                continue
            nodes = self._normalize_nodes_payload(payload)
            if nodes:
                return nodes
        return []

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(verify=not self.insecure_tls, timeout=20) as client:
            if not self._token:
                await self.authenticate()
            headers = {"Authorization": f"Bearer {self._token}"}
            response = await client.request(method, f"{self.base_url}{path}", headers=headers, json=json)
            if response.status_code == 401:
                await self.authenticate()
                headers = {"Authorization": f"Bearer {self._token}"}
                response = await client.request(method, f"{self.base_url}{path}", headers=headers, json=json)
            if response.status_code >= 400:
                raise MarzbanError(f"Marzban API error {response.status_code}: {response.text}")
            if not response.content:
                return {}
            return response.json()

    async def authenticate(self) -> None:
        payload = {"username": self.username, "password": self.password}
        async with httpx.AsyncClient(verify=not self.insecure_tls, timeout=20) as client:
            response = await client.post(f"{self.base_url}/api/admin/token", data=payload)
            if response.status_code >= 400:
                raise MarzbanError(f"Auth failed: {response.status_code} {response.text}")
            self._token = response.json().get("access_token")
            if not self._token:
                raise MarzbanError("No access token returned by Marzban")

    async def create_or_update_user(
        self,
        username: str,
        duration_days: int,
        traffic_gb: int,
        inbounds: dict[str, list[str]] | None = None,
        proxies: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        now_unix = int(datetime.utcnow().timestamp())
        purchase_limit_bytes = int(traffic_gb * 1024 * 1024 * 1024)
        expire_unix = int((datetime.utcnow() + timedelta(days=duration_days)).timestamp())
        if not inbounds:
            inbounds = {"vless": [self.vless_inbound_tag]}
        # Normalize protocols/tags.
        norm_inbounds: dict[str, list[str]] = {}
        for proto, tags in (inbounds or {}).items():
            if not isinstance(proto, str) or not proto.strip():
                continue
            if not isinstance(tags, list):
                continue
            clean_tags = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
            if clean_tags:
                norm_inbounds[proto.strip().lower()] = clean_tags
        if not norm_inbounds:
            norm_inbounds = {"vless": [self.vless_inbound_tag]}

        if proxies is None:
            proxies = {}
            for proto in norm_inbounds.keys():
                if proto == "vless":
                    proxies["vless"] = {"flow": self.vless_flow}
                else:
                    proxies[proto] = {}

        excluded_inbounds = {proto: [] for proto in norm_inbounds.keys()}
        payload = {
            "username": username,
            "status": "active",
            "expire": expire_unix,
            "data_limit": purchase_limit_bytes,
            "proxies": proxies,
            "inbounds": norm_inbounds,
            "excluded_inbounds": excluded_inbounds,
        }
        try:
            created = await self._request("POST", "/api/user", payload)
            if self._payload_has_empty_proxies(created):
                # Defensive fix: remove malformed record and recreate with explicit proxies.
                try:
                    await self.delete_user(username)
                except MarzbanError:
                    pass
                created = await self._request("POST", "/api/user", payload)
            return created
        except MarzbanError as create_error:
            err_text = str(create_error)
            if self._is_422_error(create_error):
                # Try to auto-resolve missing tags (Marzban config changed).
                resolved = await self._resolve_existing_tags(payload.get("inbounds") or {})
                if resolved:
                    payload["inbounds"] = resolved
                    payload["excluded_inbounds"] = {proto: [] for proto in resolved.keys()}
                    if "vless" in resolved:
                        self.vless_inbound_tag = resolved["vless"][0]
                    created = await self._request("POST", "/api/user", payload)
                    if self._payload_has_empty_proxies(created):
                        try:
                            await self.delete_user(username)
                        except MarzbanError:
                            pass
                        created = await self._request("POST", "/api/user", payload)
                    return created
            # For non-conflict creation errors, do not blindly call GET/UPDATE.
            # Try to read existing user; if absent, retry a minimal create payload.
            # This handles Marzban variations that may reject optional fields.
            existing: dict[str, Any] | None = None
            try:
                existing = await self.get_user(username)
            except MarzbanError as get_error:
                get_error_text = str(get_error)
                # Recover from broken users that exist in DB but crash GET /api/user/{username}
                # with "Each user needs at least one proxy".
                if self._is_empty_proxy_validation_error(get_error):
                    try:
                        await self.delete_user(username)
                    except MarzbanError:
                        pass
                    return await self._request("POST", "/api/user", payload)

                if " 404:" in str(get_error):
                    minimal_payload = {
                        "username": username,
                        "status": "active",
                        "expire": expire_unix,
                        "data_limit": purchase_limit_bytes,
                        "proxies": {"vless": {"flow": self.vless_flow}},
                        "inbounds": {"vless": [self.vless_inbound_tag]},
                        "excluded_inbounds": {"vless": []},
                    }
                    created = await self._request("POST", "/api/user", minimal_payload)
                    if self._payload_has_empty_proxies(created):
                        try:
                            await self.delete_user(username)
                        except MarzbanError:
                            pass
                        created = await self._request("POST", "/api/user", payload)
                    return created
                # If lookup failed for another reason, surface original create error.
                raise MarzbanError(
                    f"Create failed and lookup failed ({get_error_text}). Original create error: {err_text}"
                ) from get_error

            if existing is None:
                raise create_error
            existing_expire = int(existing.get("expire") or 0)
            base_expire = max(existing_expire, now_unix)
            new_expire = base_expire + int(duration_days * 86400)

            existing_limit = int(existing.get("data_limit") or 0)
            new_limit = existing_limit + purchase_limit_bytes

            update_payload = dict(existing)
            update_payload["username"] = username
            update_payload["status"] = "active"
            update_payload["expire"] = new_expire
            update_payload["data_limit"] = new_limit
            # Preserve selected inbounds/proxies (multi-protocol support).
            update_payload["inbounds"] = norm_inbounds
            update_payload["excluded_inbounds"] = {proto: [] for proto in norm_inbounds.keys()}
            if not update_payload.get("proxies") or not isinstance(update_payload.get("proxies"), dict):
                update_payload["proxies"] = proxies
            else:
                # Ensure required per-protocol proxy fields exist.
                for proto in norm_inbounds.keys():
                    if proto == "vless":
                        v = update_payload["proxies"].get("vless")
                        if not isinstance(v, dict):
                            update_payload["proxies"]["vless"] = {}
                        update_payload["proxies"]["vless"]["flow"] = self.vless_flow
                    else:
                        if proto not in update_payload["proxies"] or not isinstance(update_payload["proxies"].get(proto), dict):
                            update_payload["proxies"][proto] = {}

            updated = await self._request("PUT", f"/api/user/{username}", update_payload)
            if self._payload_has_empty_proxies(updated):
                try:
                    await self.delete_user(username)
                except MarzbanError:
                    pass
                updated = await self._request("POST", "/api/user", payload)
            return updated

    async def get_user(self, username: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/user/{username}")

    async def delete_user(self, username: str) -> None:
        await self._request("DELETE", f"/api/user/{username}")

    async def set_status(self, username: str, status: str) -> dict[str, Any]:
        user = await self.get_user(username)
        user["status"] = status
        return await self._request("PUT", f"/api/user/{username}", user)

    async def get_subscription_url(self, username: str) -> str | None:
        user = await self.get_user(username)
        return user.get("subscription_url")

    @staticmethod
    def extract_vless_link(data: dict[str, Any]) -> str | None:
        links = data.get("links")
        if isinstance(links, list):
            for link in links:
                if isinstance(link, str) and link.startswith("vless://"):
                    return link

        # Some Marzban setups may store links in nested fields.
        for key in ("subscription_url",):
            val = data.get(key)
            if isinstance(val, str) and val.startswith("vless://"):
                return val
        return None

    @staticmethod
    def _extract_vless_from_text(text: str) -> str | None:
        for line in text.splitlines():
            candidate = line.strip()
            if candidate.startswith("vless://"):
                return candidate

        raw = text.strip()
        if not raw:
            return None

        # Many subscription endpoints return base64 with links list.
        try:
            padded = raw + "=" * ((4 - len(raw) % 4) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            for line in decoded.splitlines():
                candidate = line.strip()
                if candidate.startswith("vless://"):
                    return candidate
        except Exception:
            pass

        try:
            padded = raw + "=" * ((4 - len(raw) % 4) % 4)
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
            for line in decoded.splitlines():
                candidate = line.strip()
                if candidate.startswith("vless://"):
                    return candidate
        except Exception:
            pass

        return None

    async def get_vless_link(self, username: str) -> str | None:
        user = await self.get_user(username)
        direct = self.extract_vless_link(user)
        if direct:
            return direct

        sub_url = user.get("subscription_url")
        if not isinstance(sub_url, str) or not sub_url:
            return None

        try:
            async with httpx.AsyncClient(verify=not self.insecure_tls, timeout=20) as client:
                response = await client.get(sub_url)
                if response.status_code >= 400:
                    return None
                text = response.text or ""
                return self._extract_vless_from_text(text)
        except Exception:
            # Do not fail whole purchase flow if subscription URL fetch fails.
            return None

    async def get_best_access_link(self, username: str) -> str | None:
        user = await self.get_user(username)
        vless = self.extract_vless_link(user)
        if vless:
            return vless
        vless = await self.get_vless_link(username)
        if vless:
            return vless
        sub_url = user.get("subscription_url")
        return sub_url if isinstance(sub_url, str) else None

    async def list_users(self, limit: int = 1000, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        return await self._request("GET", f"/api/users?limit={limit}&offset={offset}")

    async def cleanup_invalid_users(self, limit: int = 1000) -> dict[str, Any]:
        scanned = 0
        deleted = 0
        failed: list[str] = []

        offset = 0
        while True:
            page = await self.list_users(limit=limit, offset=offset)
            users = page.get("users")
            if not isinstance(users, list) or not users:
                break

            for user in users:
                if not isinstance(user, dict):
                    continue
                scanned += 1
                username = user.get("username")
                if not isinstance(username, str) or not username:
                    continue
                if self._payload_has_empty_proxies(user):
                    try:
                        await self.delete_user(username)
                        deleted += 1
                    except MarzbanError:
                        failed.append(username)

            if len(users) < limit:
                break
            offset += limit

        return {"scanned": scanned, "deleted": deleted, "failed": failed}

    async def clear_all_users(self, limit: int = 1000) -> dict[str, Any]:
        deleted = 0
        failed: list[str] = []

        while True:
            page = await self.list_users(limit=limit, offset=0)
            users = page.get("users")
            if not isinstance(users, list) or not users:
                break

            usernames: list[str] = []
            for user in users:
                if isinstance(user, dict):
                    username = user.get("username")
                    if isinstance(username, str) and username:
                        usernames.append(username)
            if not usernames:
                break

            deleted_this_batch = 0
            for username in usernames:
                try:
                    await self.delete_user(username)
                    deleted += 1
                    deleted_this_batch += 1
                except MarzbanError:
                    failed.append(username)

            # Safety: avoid endless loop if nothing could be deleted.
            if deleted_this_batch == 0:
                break

        return {"deleted": deleted, "failed": failed}

    async def _resolve_existing_vless_tag(self) -> str | None:
        try:
            payload = await self._request("GET", "/api/inbounds")
        except MarzbanError:
            return None
        vless_inbounds = payload.get("vless")
        if not isinstance(vless_inbounds, list):
            return None
        tags: list[str] = []
        for inbound in vless_inbounds:
            if isinstance(inbound, dict):
                tag = inbound.get("tag")
                if isinstance(tag, str) and tag.strip():
                    tags.append(tag.strip())
        if not tags:
            return None
        if self.vless_inbound_tag in tags:
            return self.vless_inbound_tag
        return tags[0]

    async def _resolve_existing_tags(self, desired: dict[str, Any]) -> dict[str, list[str]] | None:
        """
        Given a desired inbounds mapping, replace missing tags with the first available per protocol.
        Returns None if nothing could be resolved.
        """
        try:
            payload = await self.get_inbounds()
        except MarzbanError:
            return None
        changed = False
        out: dict[str, list[str]] = {}
        for proto, tags in desired.items():
            if not isinstance(proto, str):
                continue
            proto_key = proto.strip().lower()
            want = []
            if isinstance(tags, list):
                want = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
            have_list = payload.get(proto_key)
            have_tags: list[str] = []
            if isinstance(have_list, list):
                for inbound in have_list:
                    if isinstance(inbound, dict):
                        tag = inbound.get("tag")
                        if isinstance(tag, str) and tag.strip():
                            have_tags.append(tag.strip())
            if not have_tags:
                continue
            if not want:
                out[proto_key] = [have_tags[0]]
                changed = True
                continue
            if want[0] in have_tags:
                out[proto_key] = [want[0]]
                continue
            out[proto_key] = [have_tags[0]]
            changed = True
        return out if changed and out else None

    @staticmethod
    def _normalize_nodes_payload(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("nodes", "items", "data", "servers", "hosts"):
                val = payload.get(key)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
        return []

    @staticmethod
    def extract_best_link(user_payload: Any, preferred_protocols: list[str] | None = None) -> str | None:
        if not isinstance(user_payload, dict):
            return None
        links = user_payload.get("links")
        if not isinstance(links, list):
            return None
        link_strs = [x for x in links if isinstance(x, str) and x.strip()]
        if not link_strs:
            return None
        if preferred_protocols:
            prefs = [p.strip().lower() for p in preferred_protocols if isinstance(p, str) and p.strip()]
            for p in prefs:
                prefix = f"{p}://"
                for l in link_strs:
                    if l.lower().startswith(prefix):
                        return l
        return link_strs[0]

    @staticmethod
    def is_not_found_error(exc: Exception) -> bool:
        text = str(exc).lower()
        if "not found" in text:
            return True
        m = re.search(r"\b(\d{3})\b", text)
        return bool(m and m.group(1) == "404")

    @classmethod
    def is_invalid_user_record_error(cls, exc: Exception) -> bool:
        return cls._is_empty_proxy_validation_error(exc)

    @staticmethod
    def _is_empty_proxy_validation_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "each user needs at least one proxy" in text

    @staticmethod
    def _payload_has_empty_proxies(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        proxies = payload.get("proxies")
        if proxies in (None, {}, []):
            return True
        if isinstance(proxies, list) and len(proxies) == 0:
            return True
        return False

    @staticmethod
    def _is_422_error(exc: Exception) -> bool:
        text = str(exc)
        return bool(re.search(r"\b422\b", text))
