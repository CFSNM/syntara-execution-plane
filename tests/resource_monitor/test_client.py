"""Kubernetes client factory from Cluster credentials."""

from __future__ import annotations

from typing import Self

import pytest
from kubernetes import client

from execution_plane.resource_monitor.client import KubernetesClientError, api_client_from_credentials

_CAPTURED_CONFIGS: list[client.Configuration] = []


class _CapturingApiClient:
    def __init__(self, configuration: client.Configuration) -> None:
        _CAPTURED_CONFIGS.append(configuration)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _with_captured_client(endpoint: str, api_key: str, ca_certificate: str | None = None) -> client.Configuration:
    _CAPTURED_CONFIGS.clear()
    original = client.ApiClient
    client.ApiClient = _CapturingApiClient  # type: ignore[misc, assignment]
    try:
        with api_client_from_credentials(
            endpoint=endpoint,
            api_key=api_key,
            ca_certificate=ca_certificate,
        ):
            pass
    finally:
        client.ApiClient = original  # type: ignore[misc]
    return _CAPTURED_CONFIGS[0]


def test_bearer_token_sets_both_auth_keys_and_uses_cluster_ca() -> None:
    configuration = _with_captured_client(
        "https://cluster.example:6443/",
        "bearer-token",
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
    )
    assert configuration.host == "https://cluster.example:6443"
    assert configuration.api_key["authorization"] == "bearer-token"
    assert configuration.api_key["BearerToken"] == "bearer-token"
    assert configuration.api_key_prefix["BearerToken"] == "Bearer"
    assert configuration.verify_ssl is True
    assert configuration.ssl_ca_cert is not None
    assert configuration.ssl_ca_cert.endswith("ca.pem")


def test_missing_cluster_ca_still_verifies_tls() -> None:
    configuration = _with_captured_client("https://cluster.example:6443", "bearer-token")
    assert configuration.verify_ssl is True
    assert configuration.ssl_ca_cert is None


def test_non_http_endpoint_is_rejected() -> None:
    with pytest.raises(KubernetesClientError, match="not an HTTP Kubernetes API"):
        with api_client_from_credentials(endpoint="local://execution-plane", api_key="token"):
            pass
