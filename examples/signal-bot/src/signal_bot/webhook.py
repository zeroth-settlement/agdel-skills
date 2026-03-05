"""Optional webhook HTTP server for instant purchase detection.

Starts a lightweight asyncio HTTP server that receives POST notifications
from the AGDEL API when a buyer purchases a signal. This eliminates polling
latency for delivery.

The server is only started when a webhook base URL is configured. The base
URL must be publicly reachable — the AGDEL server sends POSTs from the
internet. Common setups:
  - A server with a public IP/domain (e.g. https://mybot.example.com)
  - A tunnel service like ngrok for local development (e.g. ngrok http 8080)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Coroutine

_WEBHOOK_PATH = "/webhook"


class WebhookServer:
    """Minimal asyncio HTTP server that receives AGDEL purchase webhooks."""

    def __init__(
        self,
        port: int = 8080,
        on_purchase: Callable[[dict], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._port = port
        self._on_purchase = on_purchase
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection, "0.0.0.0", self._port
        )
        print(f"[webhook] Listening on port {self._port}", flush=True)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                return

            parts = request_line.decode("utf-8", errors="replace").strip().split()
            if len(parts) < 2:
                self._send_response(writer, 400, "Bad Request")
                return

            method, path = parts[0], parts[1]

            # Read headers
            content_length = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if not line or line == b"\r\n":
                    break
                header = line.decode("utf-8", errors="replace").strip().lower()
                if header.startswith("content-length:"):
                    content_length = int(header.split(":", 1)[1].strip())

            # Health check
            if method == "GET" and path == "/health":
                self._send_response(writer, 200, '{"status":"ok"}')
                return

            # Webhook endpoint
            if method == "POST" and path == _WEBHOOK_PATH:
                body = b""
                if content_length > 0:
                    body = await asyncio.wait_for(
                        reader.readexactly(content_length), timeout=10
                    )

                try:
                    payload = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    self._send_response(writer, 400, "Invalid JSON")
                    return

                event = payload.get("event", "unknown")
                commitment = payload.get("commitment_hash", "???")[:18]
                print(
                    f"[webhook] Received {event} for {commitment}...",
                    flush=True,
                )

                if self._on_purchase and event == "purchase":
                    try:
                        await self._on_purchase(payload)
                    except Exception as exc:
                        print(f"[webhook] Handler error: {exc}", flush=True)

                self._send_response(writer, 200, '{"received":true}')
                return

            self._send_response(writer, 404, "Not Found")

        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _send_response(
        writer: asyncio.StreamWriter, status: int, body: str
    ) -> None:
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(
            status, "Error"
        )
        response = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        )
        writer.write(response.encode("utf-8"))


def webhook_url_for(base_url: str) -> str:
    """Build the full webhook URL from a base URL."""
    return base_url.rstrip("/") + _WEBHOOK_PATH
