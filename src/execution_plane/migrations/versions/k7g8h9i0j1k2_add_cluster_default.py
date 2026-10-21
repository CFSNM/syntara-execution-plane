"""Add the global default Cluster flag.

Revision ID: k7g8h9i0j1k2
Revises: j6f7a8b9c0d1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k7g8h9i0j1k2"
down_revision: str | Sequence[str] | None = "j6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EP = "execution_plane"


def upgrade() -> None:
    """Persist desired and observed global default state without losing a default."""
    op.add_column(
        "clusters",
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema=EP,
    )
    op.add_column(
        "cluster_bindings",
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema=EP,
    )
    op.execute(
        sa.text(
            """
            WITH chosen AS (
                SELECT id, created_by
                FROM execution_plane.clusters
                ORDER BY (status = 'active') DESC, created_at, id
                LIMIT 1
            )
            UPDATE execution_plane.clusters AS cluster
            SET is_default = true,
                updated_by = chosen.created_by,
                updated_at = now()
            FROM chosen
            WHERE cluster.id = chosen.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE execution_plane.cluster_bindings AS binding
            SET is_default = true
            FROM execution_plane.clusters AS cluster
            WHERE binding.cluster_id = cluster.id
              AND cluster.is_default = true
            """
        )
    )
    op.create_index(
        "uq_clusters_default",
        "clusters",
        ["is_default"],
        unique=True,
        schema=EP,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    """Remove global default state."""
    op.drop_index("uq_clusters_default", table_name="clusters", schema=EP)
    op.drop_column("cluster_bindings", "is_default", schema=EP)
    op.drop_column("clusters", "is_default", schema=EP)
