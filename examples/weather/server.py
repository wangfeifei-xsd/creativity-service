"""本机只读天气 MCP：查询 Open-Meteo，不依赖平台业务代码或保存调用数据。"""

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FIELDS = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max,precipitation_sum,wind_speed_10m_max"
)
mcp = FastMCP(
    "Open-Meteo 每日天气",
    host="127.0.0.1",
    port=18083,
    stateless_http=True,
    json_response=True,
)


class DailyWeather(BaseModel):
    """单日天气及来源，数值缺失保留为空。"""

    city: str = Field(description="查询城市")
    date: str = Field(description="北京时间对应日期")
    timezone: str
    latitude: float
    longitude: float
    weather_code: int | None
    temperature_min_c: float | None
    temperature_max_c: float | None
    precipitation_probability_percent: float | None
    precipitation_mm: float | None
    wind_speed_max_ms: float | None
    source: str
    source_url: str
    retrieved_at: str


@mcp.tool(
    name="daily_weather",
    title="每日天气查询",
    description="查询城市一天的真实天气预报。未指定城市默认北京，未指定日期默认明天。",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True),
)
async def daily_weather(
    city: Annotated[str, Field(description="城市名称，默认北京", max_length=80)] = "北京",
    day: Annotated[
        str, Field(description="今天、明天、后天或 YYYY-MM-DD，默认明天", max_length=10)
    ] = "明天",
) -> DailyWeather:
    """仅请求固定天气域名，按返回的北京时间日期截取一天，不缓存或伪造天气。"""
    city = city.strip() or "北京"
    day = day.strip() or "明天"
    cities = {"北京": (39.9042, 116.4074), "南京": (32.0603, 118.7969)}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        coordinates = cities.get(city.removesuffix("市"))
        if coordinates is None:
            response = await client.get(
                GEOCODING_URL, params={"name": city, "count": 1, "language": "zh", "format": "json"}
            )
            response.raise_for_status()
            locations = response.json().get("results", [])
            if not locations:
                raise ValueError("未找到该城市，请提供明确城市名称")
            place = locations[0]
            city = place["name"]
            coordinates = (place["latitude"], place["longitude"])
        response = await client.get(
            FORECAST_URL,
            params={
                "latitude": coordinates[0],
                "longitude": coordinates[1],
                "daily": FIELDS,
                "timezone": "Asia/Shanghai",
                "forecast_days": 7,
                "wind_speed_unit": "ms",
            },
        )
        response.raise_for_status()
        payload = response.json()
    daily = payload["daily"]
    dates = daily["time"]
    if day in {"今天", "明天", "后天"}:
        index = {"今天": 0, "明天": 1, "后天": 2}[day]
    else:
        if day not in dates:
            raise ValueError("日期不在未来七天天气范围内")
        index = dates.index(day)
    return DailyWeather(
        city=city,
        date=dates[index],
        timezone=payload["timezone"],
        latitude=coordinates[0],
        longitude=coordinates[1],
        weather_code=daily["weather_code"][index],
        temperature_min_c=daily["temperature_2m_min"][index],
        temperature_max_c=daily["temperature_2m_max"][index],
        precipitation_probability_percent=daily["precipitation_probability_max"][index],
        precipitation_mm=daily["precipitation_sum"][index],
        wind_speed_max_ms=daily["wind_speed_10m_max"][index],
        source="Open-Meteo",
        source_url=str(response.url),
        retrieved_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
