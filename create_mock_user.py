"""Create mock user for development (authentication disabled)."""
import asyncio
import uuid
from db.connection import db_manager
from db.models import User, UserRole


async def create_mock_user():
    """Create the mock development user in the database."""
    await db_manager.init()

    async for session in db_manager.get_session():
        from sqlalchemy import select

        # Check if user exists
        result = await session.execute(
            select(User).where(User.id == uuid.UUID('00000000-0000-0000-0000-000000000001'))
        )
        user = result.scalar_one_or_none()

        if user:
            print('✓ Mock user already exists')
        else:
            # Create mock user
            user = User(
                id=uuid.UUID('00000000-0000-0000-0000-000000000001'),
                email='dev@example.com',
                password_hash='mock_hash_not_used',
                role=UserRole.ADMIN
            )
            session.add(user)
            await session.commit()
            print('✓ Mock user created successfully')

    await db_manager.close()


if __name__ == '__main__':
    asyncio.run(create_mock_user())
