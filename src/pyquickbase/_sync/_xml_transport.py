# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""The one place requests in Quickbase's XML format are sent from."""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from .._xml import (
    Element,
    XmlCall,
    build_body,
    policy_request,
    read_envelope,
    request_headers,
    retry_status,
    target_url,
)
from ..errors import PolicyRefusedError

if TYPE_CHECKING:
    from ._transport import Transport


class XmlTransport:
    """Sends the calls in `_xml.XML_CALLS`, with the client's token, retries, and policy.

    Only the SDK's own methods reach this, so nothing public can send an arbitrary XML action.
    """

    def __init__(self, transport: "Transport") -> None:
        self._transport = transport

    def check(self, call: XmlCall, target: str) -> None:
        """Raise what the policy would raise for this call, without sending it."""
        policy = self._transport.policy
        if policy is None:
            return
        decision = policy.decide(policy_request(call, target))
        if not decision.allowed:
            raise PolicyRefusedError(decision)

    def call(
        self, call: XmlCall, target: str, fields: Mapping[str, str | int] | None = None
    ) -> Element:
        """Send one call and return its parsed response, or raise the matching error."""
        transport = self._transport
        url = target_url(transport.realm, transport.base_url, target)
        self.check(call, target)
        response = transport.send_with_retries(
            "POST",
            url,
            f"/db/{target}",
            safe_to_repeat=call.safe_to_repeat,
            status_of=retry_status,
            content=build_body(fields or {}),
            headers=request_headers(call, transport.user_agent),
            follow_redirects=False,
        )
        return read_envelope(response, call, target)
