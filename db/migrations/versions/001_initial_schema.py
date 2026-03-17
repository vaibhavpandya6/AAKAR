"""Initial schema creation.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-03-16 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all initial tables."""

    # Create ENUM types
    user_role_enum = sa.Enum(
        "admin", "developer", "viewer", name="userrole", create_type=False
    )
    user_role_enum.create(op.get_bind(), checkfirst=True)

    project_status_enum = sa.Enum(
        "CREATED",
        "PLANNING",
        "AWAITING_APPROVAL",
        "IN_PROGRESS",
        "QA",
        "REVIEW",
        "DELIVERED",
        "FAILED",
        name="projectstatus",
        create_type=False,
    )
    project_status_enum.create(op.get_bind(), checkfirst=True)

    task_skill_enum = sa.Enum(
        "backend", "frontend", "database", "qa", name="taskskill", create_type=False
    )
    task_skill_enum.create(op.get_bind(), checkfirst=True)

    task_status_enum = sa.Enum(
        "PENDING",
        "IN_PROGRESS",
        "COMPLETE",
        "FAILED",
        name="taskstatus",
        create_type=False,
    )
    task_status_enum.create(op.get_bind(), checkfirst=True)

    # Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            user_role_enum,
            nullable=False,
            server_default="developer",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    # Create projects table
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "status",
            project_status_enum,
            nullable=False,
            server_default="CREATED",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_projects_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(op.f("ix_projects_user_id"), "projects", ["user_id"])
    op.create_index(op.f("ix_projects_status"), "projects", ["status"])

    # Create tasks table
    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("skill_required", task_skill_enum, nullable=False),
        sa.Column(
            "status",
            task_status_enum,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("assigned_agent", sa.String(255), nullable=True),
        sa.Column("depends_on", sa.ARRAY(sa.UUID()), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_tasks_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index(op.f("ix_tasks_project_id"), "tasks", ["project_id"])
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"])

    # Create agent_logs table
    op.create_table(
        "agent_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("agent", sa.String(255), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_agent_logs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_agent_logs_task_id_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_logs")),
    )
    op.create_index(
        op.f("ix_agent_logs_project_id"), "agent_logs", ["project_id"]
    )
    op.create_index(
        op.f("ix_agent_logs_timestamp"), "agent_logs", ["timestamp"]
    )

    # Create messages table
    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("sender", sa.String(255), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("message_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("message_id", name=op.f("uq_messages_message_id")),
    )
    op.create_index(op.f("ix_messages_message_id"), "messages", ["message_id"], unique=True)
    op.create_index(
        op.f("ix_messages_correlation_id"), "messages", ["correlation_id"]
    )


def downgrade() -> None:
    """Drop all tables and ENUM types."""

    # Drop indexes
    op.drop_index(op.f("ix_messages_correlation_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_message_id"), table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_agent_logs_timestamp"), table_name="agent_logs")
    op.drop_index(op.f("ix_agent_logs_project_id"), table_name="agent_logs")
    op.drop_table("agent_logs")

    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_project_id"), table_name="tasks")
    op.drop_table("tasks")

    op.drop_index(op.f("ix_projects_status"), table_name="projects")
    op.drop_index(op.f("ix_projects_user_id"), table_name="projects")
    op.drop_table("projects")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    # Drop ENUM types
    sa.Enum(name="taskstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="taskskill").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="projectstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="userrole").drop(op.get_bind(), checkfirst=True)
