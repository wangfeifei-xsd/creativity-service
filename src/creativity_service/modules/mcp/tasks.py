"""兼容消费升级前已排队的检查消息，MCP 测试与发现仅由管理端手动触发。"""

from celery import shared_task


def sweep_mcp() -> None:
    # 旧队列可能仍有 mcp.sweep；直接完成，不访问远端或生成测试与发现记录。
    return None


sweep_mcp_task = shared_task(name="mcp.sweep", ignore_result=True)(sweep_mcp)
