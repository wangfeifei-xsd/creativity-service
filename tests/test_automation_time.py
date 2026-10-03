"""时区窗口的不存在时间与重复时间边界。"""

from datetime import UTC, datetime

from creativity_service.core.primitives import RunInput
from creativity_service.modules.integrations.automation_schemas import ScheduleCreate, next_window


def test_timezone_missing_and_repeated_local_time():
    spec = ScheduleCreate(
        name="时区测试",
        request=RunInput(agent_code="test", input={}),
        timezone="America/New_York",
        daily_at="02:30",
    )
    assert next_window(spec, datetime(2026, 3, 8, 6, tzinfo=UTC)) == datetime(
        2026, 3, 9, 6, 30, tzinfo=UTC
    )
    repeated = spec.model_copy(update={"daily_at": "01:30"})
    first = next_window(repeated, datetime(2026, 11, 1, 4, tzinfo=UTC))
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert next_window(repeated, first) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)
