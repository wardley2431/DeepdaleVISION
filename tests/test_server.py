from __future__ import annotations

import base64
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from server import AppConfig, RoomStore, ScreenShareServer, create_access_token


def decode_segment(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


class RoomStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AppConfig(
            api_key="test-key",
            api_secret="test-secret-that-is-long-enough",
            livekit_url="ws://localhost:7880",
            max_viewers=2,
            room_ttl_seconds=3600,
            viewer_ttl_seconds=1,
            token_ttl_seconds=600,
        )
        self.store = RoomStore(self.config)

    def test_room_pin_and_host_authentication(self) -> None:
        room = self.store.create_room("Ms Test")
        self.assertRegex(room.pin, r"^\d{6}$")
        self.assertEqual(self.store.authenticate_host(room.pin, room.host_key), room)

    def test_viewer_limit_and_expiry(self) -> None:
        room = self.store.create_room("Teacher")
        self.store.join_viewer(room.pin, "One")
        self.store.join_viewer(room.pin, "Two")
        with self.assertRaisesRegex(Exception, "already has 2 viewers"):
            self.store.join_viewer(room.pin, "Three")

        for lease in room.viewers.values():
            lease.last_seen = time.time() - 5
        _, replacement = self.store.join_viewer(room.pin, "Three")
        self.assertEqual(replacement.display_name, "Three")

    def test_tokens_restrict_viewer_publishing(self) -> None:
        room = self.store.create_room("Teacher")
        host_token = create_access_token(
            self.config, room, "host-id", "Teacher", "host"
        )
        viewer_token = create_access_token(
            self.config, room, "viewer-id", "Student", "viewer"
        )
        host_payload = decode_segment(host_token.split(".")[1])
        viewer_payload = decode_segment(viewer_token.split(".")[1])
        self.assertTrue(host_payload["video"]["canPublish"])
        self.assertFalse(host_payload["video"]["canSubscribe"])
        self.assertEqual(
            host_payload["video"]["canPublishSources"],
            ["screen_share"],
        )
        self.assertFalse(viewer_payload["video"]["canPublish"])
        self.assertTrue(viewer_payload["video"]["canSubscribe"])
        self.assertEqual(viewer_payload["video"]["room"], room.room_name)


class HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config = AppConfig(
            api_key="test-key",
            api_secret="test-secret-that-is-long-enough",
            livekit_url="ws://localhost:7880",
            max_viewers=12,
        )
        cls.server = ScreenShareServer(("127.0.0.1", 0), config=config)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_create_join_heartbeat_and_end(self) -> None:
        status, room = self.request("/api/rooms", {"name": "Teacher"})
        self.assertEqual(status, 201)

        status, viewer = self.request(
            "/api/viewers/join",
            {"pin": room["pin"], "name": "Student"},
        )
        self.assertEqual(status, 200)
        self.assertIn("token", viewer)

        status, heartbeat = self.request(
            "/api/viewers/heartbeat",
            {
                "pin": room["pin"],
                "viewerId": viewer["viewerId"],
                "viewerKey": viewer["viewerKey"],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(heartbeat["viewerCount"], 1)

        status, _ = self.request(
            "/api/rooms/end",
            {"pin": room["pin"], "hostKey": room["hostKey"]},
        )
        self.assertEqual(status, 200)

        status, payload = self.request(f"/api/rooms/{room['pin']}/status")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
