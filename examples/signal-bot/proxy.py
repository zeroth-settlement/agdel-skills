"""Lightweight reverse proxy to share one ngrok tunnel between multiple bots.

Routes requests by path:
  /api/webhook/purchase  ->  fragility bot (port 8080)
  /webhook               ->  signal bot (port 8090)
  /health                ->  local health check
  everything else        ->  fragility bot (port 8080, serves dashboard)

Usage:
  python proxy.py                         # default: listen on 8888
  python proxy.py --port 8888
  ngrok http 8888 --url your-domain.ngrok.io
"""

import argparse
import asyncio
import json
import sys

ROUTES = {
    "/webhook": 8090,             # signal bot
    "/api/webhook/purchase": 8080, # fragility bot
}
DEFAULT_BACKEND = 8080  # fragility bot gets everything else


async def proxy_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        # Read request line
        request_line = await asyncio.wait_for(reader.readline(), timeout=10)
        if not request_line:
            return

        parts = request_line.decode("utf-8", errors="replace").strip().split()
        if len(parts) < 3:
            _send_error(writer, 400, "Bad Request")
            return

        method, path, version = parts[0], parts[1], parts[2]

        # Read all headers
        headers_raw = [request_line]
        content_length = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            headers_raw.append(line)
            if not line or line == b"\r\n":
                break
            header = line.decode("utf-8", errors="replace").strip().lower()
            if header.startswith("content-length:"):
                content_length = int(header.split(":", 1)[1].strip())

        # Read body
        body = b""
        if content_length > 0:
            body = await asyncio.wait_for(reader.readexactly(content_length), timeout=10)

        # Route by path
        backend_port = DEFAULT_BACKEND
        for route_path, port in ROUTES.items():
            if path == route_path or path.startswith(route_path + "?"):
                backend_port = port
                break

        # Forward to backend
        try:
            br, bw = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", backend_port), timeout=5
            )
        except (ConnectionRefusedError, asyncio.TimeoutError):
            print(f"[proxy] Backend :{backend_port} unavailable for {method} {path}", flush=True)
            _send_error(writer, 502, json.dumps({"error": f"backend :{backend_port} unavailable"}))
            return

        # Send request to backend
        bw.write(b"".join(headers_raw))
        if body:
            bw.write(body)
        await bw.drain()

        # Read and forward response
        response = await asyncio.wait_for(br.read(65536), timeout=30)
        if response:
            writer.write(response)
            await writer.drain()

        bw.close()
        try:
            await bw.wait_closed()
        except Exception:
            pass

    except (asyncio.TimeoutError, ConnectionError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def _send_error(writer: asyncio.StreamWriter, status: int, body: str) -> None:
    reason = {400: "Bad Request", 502: "Bad Gateway"}.get(status, "Error")
    resp = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
        f"{body}"
    )
    writer.write(resp.encode("utf-8"))


async def _run(port: int) -> None:
    server = await asyncio.start_server(proxy_request, "0.0.0.0", port)
    print(f"[proxy] Listening on :{port}", flush=True)
    for route_path, backend_port in ROUTES.items():
        print(f"[proxy]   {route_path} -> :{backend_port}", flush=True)
    print(f"[proxy]   (default) -> :{DEFAULT_BACKEND}", flush=True)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Webhook reverse proxy")
    parser.add_argument("--port", type=int, default=8888, help="Listen port (default: 8888)")
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.port))
    except KeyboardInterrupt:
        print("\n[proxy] Stopped.", flush=True)


if __name__ == "__main__":
    main()
