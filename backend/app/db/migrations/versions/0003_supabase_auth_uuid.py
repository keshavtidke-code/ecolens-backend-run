"""supabase auth migration: users.id + all user_id FK columns to UUID

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22

Migration summary
-----------------
* Drop the old INTEGER-based `users` table (with password/email columns).
* Recreate `users` with a UUID primary key that mirrors Supabase auth.users.id.
* Alter user_id FK columns in observations, journal_entries, and chat_threads
  from INTEGER to UUID.

IMPORTANT: This migration drops the existing users table.  Any existing user
rows (and their cascaded children) will be DELETED.  Run this on a fresh
Supabase database or after backing up data.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.db.types

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Drop child tables that FK → users (CASCADE will handle data but
    #    we need to drop the FK constraints before altering the column type).
    # ------------------------------------------------------------------

    # Drop FK constraints on child tables
    with op.batch_alter_table("observations", schema=None) as batch_op:
        batch_op.drop_constraint("fk_observations_user_id_users", type_="foreignkey")

    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.drop_constraint("fk_journal_entries_user_id_users", type_="foreignkey")

    with op.batch_alter_table("chat_threads", schema=None) as batch_op:
        batch_op.drop_constraint("fk_chat_threads_user_id_users", type_="foreignkey")

    # ------------------------------------------------------------------
    # 2. Recreate `users` with UUID primary key (Supabase auth.users.id).
    #    All existing user rows are dropped here.
    # ------------------------------------------------------------------
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("last_active_at", app.db.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("created_at", app.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )

    # ------------------------------------------------------------------
    # 3. Alter user_id columns in child tables to UUID and re-add FK.
    #    Existing rows in observations / journal_entries / chat_threads
    #    will have their user_id set to NULL (safe since ON DELETE CASCADE
    #    referenced users that no longer exist anyway).  In practice you
    #    should truncate child tables too on a first deploy.
    # ------------------------------------------------------------------

    # observations.user_id: INTEGER → UUID (NOT NULL enforced after FK restore)
    with op.batch_alter_table("observations", schema=None) as batch_op:
        batch_op.alter_column(
            "user_id",
            type_=postgresql.UUID(as_uuid=True),
            postgresql_using="NULL::uuid",
            existing_nullable=False,
            nullable=True,   # temporarily nullable while migrating
        )
        batch_op.create_foreign_key(
            "fk_observations_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # journal_entries.user_id: INTEGER → UUID
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.alter_column(
            "user_id",
            type_=postgresql.UUID(as_uuid=True),
            postgresql_using="NULL::uuid",
            existing_nullable=False,
            nullable=True,
        )
        batch_op.create_foreign_key(
            "fk_journal_entries_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # chat_threads.user_id: INTEGER → UUID
    with op.batch_alter_table("chat_threads", schema=None) as batch_op:
        batch_op.alter_column(
            "user_id",
            type_=postgresql.UUID(as_uuid=True),
            postgresql_using="NULL::uuid",
            existing_nullable=False,
            nullable=True,
        )
        batch_op.create_foreign_key(
            "fk_chat_threads_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    # Downgrade is intentionally a no-op: reverting from UUID PKs back to
    # integer PKs would require data reconstruction that isn't feasible.
    # If you need to roll back, restore from a database backup.
    raise NotImplementedError(
        "Downgrade from Supabase UUID auth is not supported. Restore from backup."
    )
