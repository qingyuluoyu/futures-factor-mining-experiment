from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_notify_setting import UserNotifySetting


async def get_or_create_preferences(session: AsyncSession, user_id: UUID) -> UserNotifySetting:
    await session.execute(
        insert(UserNotifySetting).values(user_id=user_id).on_conflict_do_nothing(index_elements=["user_id"])
    )
    return (await session.execute(
        select(UserNotifySetting).where(UserNotifySetting.user_id == user_id)
    )).scalar_one()
