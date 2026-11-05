"""Combine policies, and check that each one is used where it belongs. No network access."""

import inspect
from typing import Any, TypeVar, overload

from ._policy import WritePolicy, WriteRequest, WriteRules, check_policy, validate_write_policy
from ._read_policy import ReadPolicy, ReadRequest, ReadRules, check_read, validate_read_policy
from .errors import ConfigurationError

P = TypeVar("P", WriteRequest, ReadRequest)


class _AllOf:
    """Several policies, all of which must allow a request. Kept as an object, so that the client
    can still find any `ReadRules` inside, which trim responses as well as deciding."""

    __slots__ = ("policies",)

    def __init__(self, policies: tuple[Any, ...]) -> None:
        self.policies = policies

    def __call__(self, request: WriteRequest | ReadRequest) -> bool:
        for policy in self.policies:
            if isinstance(request, ReadRequest):
                check_read(policy, request)
            else:
                check_policy(policy, request)
        return True

    def __repr__(self) -> str:
        return f"all_of{self.policies!r}"


def _flatten(policy: Any) -> tuple[Any, ...]:
    if isinstance(policy, _AllOf):
        return tuple(item for part in policy.policies for item in _flatten(part))
    return (policy,)


def read_rules_in(policy: ReadPolicy | None) -> tuple[ReadRules, ...]:
    """Every `ReadRules` in a read policy, each of which trims responses."""
    return tuple(item for item in _flatten(policy) if isinstance(item, ReadRules))


def check_write_policy(policy: WritePolicy | None, *, read_only: bool) -> WritePolicy | None:
    if any(isinstance(item, ReadRules) for item in _flatten(policy)):
        raise ConfigurationError("ReadRules limits reads. Pass it as read_policy.")
    return validate_write_policy(policy, read_only=read_only)


def check_read_policy(policy: ReadPolicy | None) -> ReadPolicy | None:
    if any(isinstance(item, WriteRules) for item in _flatten(policy)):
        raise ConfigurationError("WriteRules limits writes. Pass it as write_policy.")
    return validate_read_policy(policy)


@overload
def all_of(*policies: WritePolicy) -> WritePolicy: ...
@overload
def all_of(*policies: ReadPolicy) -> ReadPolicy: ...
def all_of(*policies: Any) -> Any:
    """Combine policies, so that a request must pass every one of them.

    Combine write policies for `write_policy`, or read policies for `read_policy`, but not both
    kinds at once. Every `ReadRules` in a read policy also trims what comes back.

    Args:
        *policies: The policies, which are checked in order until one refuses.

    Returns:
        A policy of the same kind.

    Raises:
        ConfigurationError: No policies were given, one can't be used, or they mix `WriteRules`
            and `ReadRules`.
    """
    if not policies:
        raise ConfigurationError("all_of() needs at least one policy.")
    for policy in policies:
        if policy is None or not callable(policy):
            raise ConfigurationError("all_of() takes policies, which are functions or rules.")
        if inspect.iscoroutinefunction(policy):
            raise ConfigurationError("all_of() can't take an async policy, as none can be async.")
    flat = [item for policy in policies for item in _flatten(policy)]
    if any(isinstance(item, ReadRules) for item in flat) and any(
        isinstance(item, WriteRules) for item in flat
    ):
        raise ConfigurationError(
            "all_of() can't combine WriteRules and ReadRules. Pass one as write_policy and the "
            "other as read_policy."
        )
    return _AllOf(policies)
