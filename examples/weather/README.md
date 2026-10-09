# 每日天气示例

免费数据源为 [Open-Meteo](https://open-meteo.com/en/docs)，无需 API Key；免费服务适用于非商业用途，使用时遵循其当前额度与署名要求。示例通过现有 MCP 工具流程接入，不增加平台适配器。

在 `creativity-service` 目录启动：

```bash
uv run --locked python examples/weather/server.py
```

服务监听 `http://127.0.0.1:18083/mcp`，工具为 `daily_weather(city="北京", day="明天")`。城市为空默认北京，日期为空默认明天；支持今天、明天、后天和未来七天内的 `YYYY-MM-DD`。输出为北京时间的一整天预报，气温单位 ℃，风速单位米/秒；没有数据时返回错误或空值，不伪造数值。

API 与 Worker 的 MCP 出站目的地配置须包含：

```dotenv
CREATIVITY_MCP_DESTINATIONS=[{"channel_id":"channel_407de822881947339e26ec7d9d55a8a0","environment":"dev","purpose":"mcp","hostname":"127.0.0.1","port":18083,"scheme":"http","allowed_networks":["127.0.0.1/32"],"path_prefix":"/mcp"}]
```

已有其他目的地时将此项合并进数组。修改配置后重启 API 与 Worker。天气服务是独立进程，平台的 `start-local.sh` 不负责启动它。

网页已配置并发布“Open-Meteo 天气”连接、“每日天气查询”工具、“每日天气”技能、“天气助手提示词”、“通用模型路由”和“每日天气助手”。智能体采用受约束工具循环，始终加载技能正文，使用已有 DeepSeek 模型进行工具调用。

已验证：

| 输入 | 工具参数 | 结果日期 |
| --- | --- | --- |
| 今天的南京天气如何？ | 南京、今天 | 2026-10-07 |
| 天气如何？ | 北京、明天 | 2026-10-08 |

以上日期是本次验证时的真实结果，之后执行按 Open-Meteo 返回的北京时间日期计算。完整恢复方法见 [初始化 SQL](../../sql/README.md#天气成功链路归档)。
