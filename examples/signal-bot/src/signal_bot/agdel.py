"""AGDEL MCP client — communicates with the agdel-mcp server via stdio.

All marketplace interactions go through MCP tools. Connects via `npx agdel-mcp`.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class AgdelMCPError(Exception):
    """Raised when an MCP tool call fails."""


class AgdelMCPClient:
    """Async client for the AGDEL MCP server.

    Usage:
        client = AgdelMCPClient(wallet_private_key="0x...", ...)
        await client.start()
        try:
            info = await client.whoami()
            ...
        finally:
            await client.stop()
    """

    def __init__(
        self,
        wallet_private_key: str,
        api_url: str = "https://agent-deliberation.net/api",
        marketplace_address: str = "0x1779255c0AcDe950095C9E872B2fAD06CFB88D4c",  # HyperEVM (chain 999)
    ) -> None:
        self._wallet_private_key = wallet_private_key
        self._api_url = api_url
        self._marketplace_address = marketplace_address
        self._stack = contextlib.AsyncExitStack()
        self._session: ClientSession | None = None

    async def start(self) -> None:
        """Start the MCP server subprocess and initialize the session."""
        env = os.environ.copy()
        env["AGDEL_API_URL"] = self._api_url
        env["MARKETPLACE_ADDRESS"] = self._marketplace_address
        if self._wallet_private_key:
            env["AGDEL_SIGNER_PRIVATE_KEY"] = self._wallet_private_key

        # Use local agdel-mcp if available, otherwise fall back to npx
        local_mcp = os.environ.get("AGDEL_MCP_PATH", "")
        if local_mcp and os.path.isfile(local_mcp):
            params = StdioServerParameters(
                command="node",
                args=[local_mcp],
                env=env,
            )
        else:
            params = StdioServerParameters(
                command="npx",
                args=["-y", "agdel-mcp"],
                env=env,
            )
        streams = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(
            ClientSession(*streams)
        )
        await self._session.initialize()

    async def stop(self) -> None:
        """Shut down the MCP server subprocess."""
        await self._stack.aclose()
        self._stack = contextlib.AsyncExitStack()
        self._session = None

    async def _call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call an MCP tool and return the parsed JSON response."""
        if self._session is None:
            raise AgdelMCPError("MCP session closed — call start() to reconnect")

        try:
            result = await self._session.call_tool(
                tool_name, arguments=arguments or {}
            )
        except Exception as exc:
            err_msg = str(exc) or type(exc).__name__
            raise AgdelMCPError(f"{tool_name} transport error: {err_msg}") from exc

        if result.isError:
            texts = [c.text for c in result.content if hasattr(c, "text")]
            raise AgdelMCPError(f"{tool_name} failed: {' '.join(texts)}")

        for content in result.content:
            if hasattr(content, "text"):
                try:
                    return json.loads(content.text)
                except json.JSONDecodeError:
                    return content.text
        return None

    # ── Identity ──────────────────────────────────────────────────

    async def whoami(self) -> dict:
        return await self._call("agdel_whoami")

    # ── Market Discovery ──────────────────────────────────────────

    async def get_signal(self, commitment_hash: str) -> dict:
        return await self._call(
            "agdel_market_get_signal",
            {"commitment_hash": commitment_hash},
        )

    # ── Maker Publishing ──────────────────────────────────────────

    async def create_listing(
        self,
        *,
        commitment_hash: str,
        asset: str,
        expiry_time: int,
        cost_usdc: str,
        signal_type: str,
        maker_address: str | None = None,
        signal_name: str | None = None,
        signal_description: str | None = None,
        confidence: float | None = None,
        entry_price: str | None = None,
        maker_signature: str | None = None,
        horizon_bucket: str | None = None,
        webhook_url: str | None = None,
    ) -> dict:
        args: dict[str, Any] = {
            "commitment_hash": commitment_hash,
            "asset": asset,
            "expiry_time": expiry_time,
            "cost_usdc": cost_usdc,
            "signal_type": signal_type,
        }
        if maker_address is not None:
            args["maker_address"] = maker_address
        if signal_name is not None:
            args["signal_name"] = signal_name
        if signal_description is not None:
            args["signal_description"] = signal_description
        if confidence is not None:
            args["confidence"] = confidence
        if entry_price is not None:
            args["entry_price"] = entry_price
        if maker_signature is not None:
            args["maker_signature"] = maker_signature
        if horizon_bucket is not None:
            args["horizon_bucket"] = horizon_bucket
        if webhook_url is not None:
            args["webhook_url"] = webhook_url
        return await self._call("agdel_market_create_listing", args)

    async def reveal_signal(
        self,
        *,
        commitment_hash: str,
        target_price: str,
        direction: int,
        salt: str,
    ) -> dict:
        return await self._call(
            "agdel_market_reveal_signal",
            {
                "commitment_hash": commitment_hash,
                "target_price": target_price,
                "direction": direction,
                "salt": salt,
            },
        )

    # ── Encrypted Delivery ────────────────────────────────────────

    async def register_key(self, algorithm: str, public_key_b64: str) -> dict:
        return await self._call(
            "agdel_exchange_register_key",
            {"algorithm": algorithm, "public_key_b64": public_key_b64},
        )

    async def get_key(self, address: str) -> dict:
        return await self._call(
            "agdel_exchange_get_key",
            {"address": address},
        )

    async def post_delivery(
        self,
        *,
        commitment_hash: str,
        buyer_address: str,
        algorithm: str,
        ephemeral_pubkey_b64: str,
        nonce_b64: str,
        ciphertext_b64: str,
    ) -> dict:
        return await self._call(
            "agdel_exchange_post_delivery",
            {
                "commitment_hash": commitment_hash,
                "buyer_address": buyer_address,
                "algorithm": algorithm,
                "ephemeral_pubkey_b64": ephemeral_pubkey_b64,
                "nonce_b64": nonce_b64,
                "ciphertext_b64": ciphertext_b64,
            },
        )
