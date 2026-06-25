from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
MAX_BODY_BYTES = 16_384


@dataclass(frozen=True)
class AppConfig:
    api_key: str
    api_secret: str
    livekit_url: str
    max_viewers: int = 12
    room_ttl_seconds: int = 4 * 60 * 60
    viewer_ttl_seconds: int = 45
    token_ttl_seconds: int = 2 * 60 * 60

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            api_key=os.getenv("LIVEKIT_API_KEY", "devkey"),
            api_secret=os.getenv(
                "LIVEKIT_API_SECRET",
                "devsecretdevsecretdevsecretdevsecret",
            ),
            livekit_url=os.getenv("LIVEKIT_URL", "auto"),
            max_viewers=int(os.getenv("MAX_VIEWERS", "12")),
            room_ttl_seconds=int(os.getenv("ROOM_TTL_SECONDS", str(4 * 60 * 60))),
            viewer_ttl_seconds=int(os.getenv("VIEWER_TTL_SECONDS", "45")),
            token_ttl_seconds=int(os.getenv("TOKEN_TTL_SECONDS", str(2 * 60 * 60))),
        )


@dataclass
class ViewerLease:
    viewer_id: str
    viewer_key: str
    display_name: str
    last_seen: float


@dataclass
class RoomSession:
    pin: str
    room_name: str
    host_key: str
    host_name: str
    created_at: float
    expires_at: float
    viewers: dict[str, ViewerLease] = field(default_factory=dict)


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class RoomStore:
    def __init__(self, config: AppConfig):
        self.config = config
        self._rooms: dict[str, RoomSession] = {}
        self._lock = threading.RLock()

    def create_room(self, host_name: str) -> RoomSession:
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            pin = self._new_pin_locked()
            room = RoomSession(
                pin=pin,
                room_name=f"classcast-{pin}-{uuid.uuid4().hex[:8]}",
                host_key=secrets.token_urlsafe(32),
                host_name=clean_name(host_name, "Teacher"),
                created_at=now,
                expires_at=now + self.config.room_ttl_seconds,
            )
            self._rooms[pin] = room
            return room

    def get_room(self, pin: str) -> RoomSession:
        now = time.time()
        with self._lock:
            self._cleanup_locked(now)
            room = self._rooms.get(normalize_pin(pin))
            if room is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "That session PIN is not active.")
            self._cleanup_viewers_locked(room, now)
            return room

    def authenticate_host(self, pin: str, host_key: str) -> RoomSession:
        room = self.get_room(pin)
        if not host_key or not secrets.compare_digest(room.host_key, host_key):
            raise ApiError(HTTPStatus.FORBIDDEN, "The host key is invalid.")
        return room

    def join_viewer(self, pin: str, display_name: str) -> tuple[RoomSession, ViewerLease]:
        now = time.time()
        with self._lock:
            room = self.get_room(pin)
            self._cleanup_viewers_locked(room, now)
            if len(room.viewers) >= self.config.max_viewers:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    f"This session already has {self.config.max_viewers} viewers.",
                )
            viewer_id = uuid.uuid4().hex
            lease = ViewerLease(
                viewer_id=viewer_id,
                viewer_key=secrets.token_urlsafe(24),
                display_name=clean_name(display_name, "Student"),
                last_seen=now,
            )
            room.viewers[viewer_id] = lease
            return room, lease

    def heartbeat(self, pin: str, viewer_id: str, viewer_key: str) -> int:
        now = time.time()
        with self._lock:
            room = self.get_room(pin)
            lease = room.viewers.get(viewer_id)
            if lease is None or not secrets.compare_digest(lease.viewer_key, viewer_key):
                raise ApiError(HTTPStatus.NOT_FOUND, "Viewer session has expired.")
            lease.last_seen = now
            return len(room.viewers)

    def leave_viewer(self, pin: str, viewer_id: str, viewer_key: str) -> None:
        with self._lock:
            try:
                room = self.get_room(pin)
            except ApiError:
                return
            lease = room.viewers.get(viewer_id)
            if lease and secrets.compare_digest(lease.viewer_key, viewer_key):
                room.viewers.pop(viewer_id, None)

    def end_room(self, pin: str, host_key: str) -> None:
        with self._lock:
            room = self.authenticate_host(pin, host_key)
            self._rooms.pop(room.pin, None)

    def room_status(self, pin: str) -> dict[str, Any]:
        room = self.get_room(pin)
        with self._lock:
            return {
                "pin": room.pin,
                "viewerCount": len(room.viewers),
                "maxViewers": self.config.max_viewers,
                "expiresAt": iso_timestamp(room.expires_at),
            }

    def count(self) -> int:
        with self._lock:
            self._cleanup_locked(time.time())
            return len(self._rooms)

    def _new_pin_locked(self) -> str:
        for _ in range(100):
            pin = f"{secrets.randbelow(1_000_000):06d}"
            if pin not in self._rooms:
                return pin
        raise RuntimeError("Unable to allocate a session PIN")

    def _cleanup_locked(self, now: float) -> None:
        expired = [pin for pin, room in self._rooms.items() if room.expires_at <= now]
        for pin in expired:
            self._rooms.pop(pin, None)
        for room in self._rooms.values():
            self._cleanup_viewers_locked(room, now)

    def _cleanup_viewers_locked(self, room: RoomSession, now: float) -> None:
        cutoff = now - self.config.viewer_ttl_seconds
        stale = [
            viewer_id
            for viewer_id, lease in room.viewers.items()
            if lease.last_seen < cutoff
        ]
        for viewer_id in stale:
            room.viewers.pop(viewer_id, None)


def clean_name(value: Any, fallback: str) -> str:
    name = " ".join(str(value or "").strip().split())
    return name[:40] or fallback


def normalize_pin(value: Any) -> str:
    pin = "".join(character for character in str(value or "") if character.isdigit())
    if len(pin) != 6:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Enter a six-digit session PIN.")
    return pin


def iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_access_token(
    config: AppConfig,
    room: RoomSession,
    identity: str,
    display_name: str,
    role: str,
) -> str:
    now = int(time.time())
    expires_at = min(int(room.expires_at), now + config.token_ttl_seconds)
    can_publish = role == "host"
    can_subscribe = role == "viewer"
    video_grant = {
        "roomJoin": True,
        "room": room.room_name,
        "canPublish": can_publish,
        "canSubscribe": can_subscribe,
        "canPublishData": False,
        "canUpdateOwnMetadata": False,
    }
    if can_publish:
        video_grant["canPublishSources"] = ["screen_share"]
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": config.api_key,
        "sub": identity,
        "name": display_name,
        "nbf": now - 5,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
        "metadata": json.dumps({"role": role}, separators=(",", ":")),
        "video": video_grant,
    }
    encoded_header = base64url(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = base64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(
        config.api_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{encoded_header}.{encoded_payload}.{base64url(signature)}"


class ScreenShareServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: AppConfig | None = None,
        store: RoomStore | None = None,
    ):
        self.config = config or AppConfig.from_env()
        self.store = store or RoomStore(self.config)
        super().__init__(server_address, AppHandler)


class AppHandler(BaseHTTPRequestHandler):
    server: ScreenShareServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json(
                HTTPStatus.OK,
                {"status": "ok", "activeRooms": self.server.store.count()},
            )
            return
        if path == "/api/config":
            self.send_json(
                HTTPStatus.OK,
                {
                    "livekitUrl": self.server.config.livekit_url,
                    "maxViewers": self.server.config.max_viewers,
                },
            )
            return
        if path.startswith("/api/rooms/") and path.endswith("/status"):
            parts = path.strip("/").split("/")
            try:
                self.send_json(
                    HTTPStatus.OK,
                    self.server.store.room_status(parts[2]),
                )
            except ApiError as error:
                self.send_api_error(error)
            return
        self.serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/rooms":
                room = self.server.store.create_room(payload.get("name", "Teacher"))
                self.send_json(
                    HTTPStatus.CREATED,
                    {
                        "pin": room.pin,
                        "hostKey": room.host_key,
                        "hostName": room.host_name,
                        "expiresAt": iso_timestamp(room.expires_at),
                        "maxViewers": self.server.config.max_viewers,
                    },
                )
                return
            if path == "/api/host/token":
                room = self.server.store.authenticate_host(
                    payload.get("pin", ""),
                    str(payload.get("hostKey", "")),
                )
                display_name = clean_name(payload.get("name"), room.host_name)
                token = create_access_token(
                    self.server.config,
                    room,
                    f"host-{room.pin}",
                    display_name,
                    "host",
                )
                self.send_json(HTTPStatus.OK, {"token": token})
                return
            if path == "/api/viewers/join":
                room, lease = self.server.store.join_viewer(
                    payload.get("pin", ""),
                    payload.get("name", "Student"),
                )
                token = create_access_token(
                    self.server.config,
                    room,
                    f"viewer-{lease.viewer_id}",
                    lease.display_name,
                    "viewer",
                )
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "pin": room.pin,
                        "viewerId": lease.viewer_id,
                        "viewerKey": lease.viewer_key,
                        "token": token,
                        "expiresAt": iso_timestamp(room.expires_at),
                    },
                )
                return
            if path == "/api/viewers/heartbeat":
                count = self.server.store.heartbeat(
                    payload.get("pin", ""),
                    str(payload.get("viewerId", "")),
                    str(payload.get("viewerKey", "")),
                )
                self.send_json(HTTPStatus.OK, {"viewerCount": count})
                return
            if path == "/api/viewers/leave":
                self.server.store.leave_viewer(
                    payload.get("pin", ""),
                    str(payload.get("viewerId", "")),
                    str(payload.get("viewerKey", "")),
                )
                self.send_json(HTTPStatus.OK, {"ok": True})
                return
            if path == "/api/rooms/end":
                self.server.store.end_room(
                    payload.get("pin", ""),
                    str(payload.get("hostKey", "")),
                )
                self.send_json(HTTPStatus.OK, {"ok": True})
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "Endpoint not found.")
        except ApiError as error:
            self.send_api_error(error)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_api_error(ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON body."))
        except Exception:
            self.send_api_error(
                ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "Unexpected server error.")
            )

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_security_headers()
        self.end_headers()

    def read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid content length.") from error
        if length > MAX_BODY_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body is too large.")
        if length == 0:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON body must be an object.")
        return payload

    def serve_static(self, path: str) -> None:
        relative_path = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (STATIC_DIR / relative_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(candidate.name)
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_security_headers()
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Cache-Control",
            "no-cache" if candidate.suffix in {".html", ".js", ".css"} else "public, max-age=86400",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_api_error(self, error: ApiError) -> None:
        self.send_json(error.status, {"error": error.message})

    def send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), display-capture=(self)",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; media-src 'self' blob:; "
            "connect-src 'self' ws: wss:; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'",
        )

    def log_message(self, message_format: str, *args: Any) -> None:
        print(
            f"{self.address_string()} - "
            f"[{self.log_date_time_string()}] {message_format % args}"
        )


def main() -> None:
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8080"))
    server = ScreenShareServer((host, port))
    print(f"ClassCast listening on http://{host}:{port}")
    print(f"LiveKit URL: {server.config.livekit_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
