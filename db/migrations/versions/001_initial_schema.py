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

    # Create ENUM types using raw SQL with IF NOT EXISTS
    op.execute("DO $$ BEGIN CREATE TYPE userrole AS ENUM ('admin', 'developer', 'viewer'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE projectstatus AS ENUM ('CREATED', 'PLANNING', 'AWAITING_APPROVAL', 'IN_PROGRESS', 'QA', 'REVIEW', 'DELIVERED', 'FAILED'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE taskskill AS ENUM ('backend', 'frontend', 'database', 'qa'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE taskstatus AS ENUM ('PENDING', 'IN_PROGRESS', 'COMPLETE', 'FAILED'); EXCEPTION WHEN duplicate_object THEN null; END $$;")

    # Create users table
    op.execute("""
        CREATE TABLE users (
            id UUID PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            role userrole NOT NULL DEFAULT 'developer',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX ix_users_email ON users (email)")

    # Create projects table
    op.execute("""
        CREATE TABLE projects (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            prompt TEXT NOT NULL,
            status projectstatus NOT NULL DEFAULT 'CREATED',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_projects_user_id ON projects (user_id)")
    op.execute("CREATE INDEX ix_projects_status ON projects (status)")

    # Create tasks table
    op.execute("""
        CREATE TABLE tasks (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT NOT NULL,
            skill_required taskskill NOT NULL,
            status taskstatus NOT NULL DEFAULT 'PENDING',
            assigned_agent VARCHAR(255),
            depends_on UUID[],
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_tasks_project_id ON tasks (project_id)")
    op.execute("CREATE INDEX ix_tasks_status ON tasks (status)")

    # Create agent_logs table
    op.execute("""
        CREATE TABLE agent_logs (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
            agent VARCHAR(255) NOT NULL,
            action VARCHAR(255) NOT NULL,
            file_path VARCHAR(500),
            status VARCHAR(50) NOT NULL,
            duration_ms INTEGER NOT NULL,
            metadata JSONB,
            timestamp TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_agent_logs_project_id ON agent_logs (project_id)")
    op.execute("CREATE INDEX ix_agent_logs_timestamp ON agent_logs (timestamp)")

    # Create messages table
    op.execute("""
        CREATE TABLE messages (
            id UUID PRIMARY KEY,
            message_id UUID NOT NULL UNIQUE,
            correlation_id VARCHAR(255) NOT NULL,
            sender VARCHAR(255) NOT NULL,
            recipient VARCHAR(255) NOT NULL,
            message_type VARCHAR(50) NOT NULL,
            payload JSONB,
            timestamp TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX ix_messages_message_id ON messages (message_id)")
    op.execute("CREATE INDEX ix_messages_correlation_id ON messages (correlation_id)")


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
