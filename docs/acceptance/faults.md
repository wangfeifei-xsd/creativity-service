# 26 故障注入记录

通过独立 schema、Redis 前缀和受控响应注入，复用正式业务服务。

- 受理提交后队列不可用：关联用例通过。

  `tests/integration/runs/test_runs.py::test_admission_commit_without_publish_and_queue_failure`

- 独立 Worker 重复投递：关联用例通过。

  `tests/integration/runs/test_queue_and_exit.py::test_real_celery_duplicate_messages_are_only_wakeups`

- 外部返回后进程退出：关联用例通过。

  `tests/integration/runs/test_queue_and_exit.py::test_process_exit_after_remote_return_keeps_attempt_and_pending_cost`

- Redis 不可用与账号撤销：关联用例通过。

  `tests/integration/iam/test_accounts.py::test_redis_unavailable_is_503_and_errors_are_distinct`
  `tests/integration/iam/test_accounts.py::test_reset_disable_and_logout_block_even_when_redis_cleanup_fails`

- 取消、删除后的迟到响应：关联用例通过。

  `tests/integration/runtime/test_boundaries.py::test_deletion_drops_late_text_but_settles_usage`
  `tests/integration/runtime/test_execution.py::test_cancel_inflight_preserves_usage_without_success`

- 旧备份恢复与独立删除清单：关联用例通过。

  `tests/integration/data_lifecycle/test_lifecycle.py::test_restore_old_database_and_objects_replays_latest_manifest`
  `tests/integration/data_lifecycle/test_lifecycle.py::test_recovery_rejects_stale_or_unavailable_ledger`

- 重复用量与下一次预算预占：关联用例通过。

  `tests/integration/usage/test_ledger.py::test_simultaneous_final_callback_and_next_reservation_preserve_exposure`
  `tests/integration/usage/test_ledger.py::test_cumulative_dedup_final_precedence_and_late_correction`
