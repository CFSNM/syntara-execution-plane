"""Register the kind cluster in the EP database via the production reconciler path.

This script is run in CI after the kind cluster is provisioned and RBAC applied.
It exercises the full ClusterBindingReconciler path — the same code path the EP
worker uses in production — rather than calling ClusterRegistrar.provision()
directly (which is what the old conftest.py fixture did and which failed to catch
the PR #89 regression).

Required env vars:
  EP_TEST_DATABASE_URL         asyncpg-compatible URL for the disposable test DB
  EP_CREDENTIAL_ENCRYPTION_KEY base64 Fernet key (already set in the CI job env)
  EP_IT_K8S_ENDPOINT           kind API server URL
  EP_IT_K8S_TOKEN              dispatcher service account bearer token

Optional env vars:
  EP_IT_K8S_NAMESPACE  target namespace (default: execution-plane)
  EP_IT_K8S_CA_CERT    PEM CA certificate for TLS verification
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from execution_plane.cluster.binding_reconciler import ClusterBindingReconciler
from execution_plane.cluster.cluster_registrar import ClusterRegistrar, NoopDiscoveryMechanism
from execution_plane.cluster.cluster_store import ClusterStore
from execution_plane.config import get_ep_settings
from execution_plane.execution_target.execution_target_store import ExecutionTargetStore
from execution_plane.models.cluster import ClusterStatus
from execution_plane.models.cluster_binding import ClusterBinding

KIND_CLIENT_ID = "integration-test"
KIND_CLUSTER_NAME = "integration-test"
KIND_SOURCE_INTEGRATION_ID = uuid.uuid5(
    uuid.NAMESPACE_URL,
    f"https://execution-plane.local/kind/{KIND_CLUSTER_NAME}",
)


async def _register(database_url: str) -> None:
    endpoint = os.environ["EP_IT_K8S_ENDPOINT"]
    token = os.environ["EP_IT_K8S_TOKEN"]
    namespace = os.environ.get("EP_IT_K8S_NAMESPACE", "execution-plane")
    ca_cert = os.environ.get("EP_IT_K8S_CA_CERT") or None

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with (
            ClusterStore.from_engine(engine) as cluster_store,
            ExecutionTargetStore.from_engine(engine) as target_store,
        ):
            registrar = ClusterRegistrar(cluster_store, target_store, NoopDiscoveryMechanism())
            reconciler = ClusterBindingReconciler(session_factory, registrar, cluster_store)

            now = datetime.now(UTC)
            async with session_factory.begin() as session:
                existing = await session.get(ClusterBinding, (KIND_CLIENT_ID, KIND_SOURCE_INTEGRATION_ID))
                if existing is None:
                    session.add(
                        ClusterBinding(
                            client_id=KIND_CLIENT_ID,
                            source_integration_id=KIND_SOURCE_INTEGRATION_ID,
                            revision=1,
                            name=KIND_CLUSTER_NAME,
                            endpoint=endpoint,
                            namespace=namespace,
                            credential=token,
                            ca_certificate=ca_cert,
                            is_default=False,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    print(f"[register-kind-cluster] Wrote ClusterBinding {KIND_SOURCE_INTEGRATION_ID}")
                else:
                    print(
                        f"[register-kind-cluster] Reusing existing binding "
                        f"{KIND_SOURCE_INTEGRATION_ID} (status={existing.status})"
                    )

            # One reconciler pass via the same public entry point the worker uses.
            # If the binding was already reconciled (e.g. a re-run), reconcile_once()
            # skips it (non-pending status) and the cluster lookup below still succeeds.
            await reconciler.reconcile_once()

            cluster = await cluster_store.get_by_source(KIND_CLIENT_ID, KIND_SOURCE_INTEGRATION_ID)
            if cluster is None or cluster.status is not ClusterStatus.ACTIVE:
                status = cluster.status if cluster else "not found"
                print(
                    f"[register-kind-cluster] ERROR: cluster status is {status}",
                    file=sys.stderr,
                )
                sys.exit(1)

            print(f"[register-kind-cluster] Cluster {cluster.id} registered and ACTIVE")
    finally:
        await engine.dispose()


def main() -> None:
    """Entry point: validate env vars and run the async registration."""
    database_url = os.environ.get("EP_TEST_DATABASE_URL") or os.environ.get("EP_DATABASE_URL")
    if not database_url:
        print(
            "[register-kind-cluster] ERROR: EP_TEST_DATABASE_URL must be set",
            file=sys.stderr,
        )
        sys.exit(1)

    # Ensure get_ep_settings() can initialise so the encrypted-credential columns work.
    os.environ.setdefault("EP_DATABASE_URL", database_url)
    get_ep_settings.cache_clear()

    asyncio.run(_register(database_url))


if __name__ == "__main__":
    main()
