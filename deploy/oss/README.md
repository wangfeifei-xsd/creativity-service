# 阿里云 OSS

所有环境使用官方 `alibabacloud-oss-v2` SDK 和 V4 签名，不提供本地对象存储回退。API、Worker、技能包和初始化对象恢复共用同一个适配器。SDK 版本由 `uv.lock` 固定。

## 配置

在当前环境文件或部署变量中填写：

```dotenv
CREATIVITY_OSS_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
CREATIVITY_OSS_REGION=cn-beijing
CREATIVITY_OSS_BUCKET=playmate-dev
CREATIVITY_OSS_ACCESS_KEY_ID=<当前环境的 AccessKey ID>
CREATIVITY_OSS_ACCESS_KEY_SECRET=<当前环境的 AccessKey Secret>
CREATIVITY_OSS_PATH_PREFIX=creativity/
CREATIVITY_HEALTH_TIMEOUT_SECONDS=10
```

开发环境参考 playmate-core 的北京地域与 `playmate-dev` 桶；凭据放在 Git 忽略的 `.env.dev`。测试和生产必须显式填写各自的连接配置或注入环境变量，不能沿用示例占位符。敏感配置不写入日志或前端。

端点支持地域域名及带桶名前缀的域名，SDK 初始化前统一为地域地址。同地域阿里云服务可以使用 `https://oss-cn-beijing-internal.aliyuncs.com`。只接受与地域匹配的阿里云 HTTPS 端点，不接受本机地址、认证信息、路径、查询参数或片段。

对象物理路径为 `{CREATIVITY_OSS_PATH_PREFIX}{部署环境}/channels/...`，例如 `creativity/dev/channels/...` 与 `creativity/test/channels/...`。部署环境取自 `CREATIVITY_ENVIRONMENT`，不由请求参数决定。数据库继续保存 `channels/...` 相对路径，迁移存储不改写已有记录。共享桶中其他服务的对象不在本服务访问范围内。

通用文件上传使用 `public-read` 对象 ACL，返回稳定的 OSS 外网 URL：`https://{bucket}.oss-{region}.aliyuncs.com/{物理路径}`。即使上传使用内网端点，返回的 URL 仍使用外网域名，中文、空格和 `#` 正确编码。应用不创建桶或修改整个桶的权限；历史受控产物保留私有读取兼容。

文件流程为：Web 调用 `POST /admin/v1/files/upload`，以 `multipart/form-data` 的 `file` 字段上传单个文件；服务端验证登录、上传权限及 20 MiB 大小上限，返回 `{url, name, content_type, size_bytes}`。业务请求只保存 `url`，不保存文件字节、Base64 或新建附件 ID。业务后端需要解析文件时，仅接受本服务当前渠道的 URL，通过 SDK 读取，不请求任意外部地址。

文件路径为 `{服务前缀}{部署环境}/channels/{渠道}/files/{随机标识}/{文件名}`，同名文件不会覆盖。通用上传不创建业务来源关系，不设业务有效期；业务逻辑删除、上传后放弃表单、保存失败或备份恢复均不修改文件及 URL。OSS 原件持续保留，不发起对象删除，也不扫描回收孤儿对象。技能包使用 URL 保存，历史 `artifact_id` 只用于兼容读取已有配置与冻结快照。

## 权限与检查

[RAM 策略](ram-policy.json) 给出了 `playmate-dev` 桶中 Creativity 开发和测试目录的最小权限示例：

- `oss:ListObjects`：限定目录前缀的只读健康探测。
- `oss:PutObject`、`oss:GetObject`、`oss:PutObjectAcl`：上传、读取、对象 ACL 及内容核验。

生产环境应使用单独的凭据，并将策略的桶和目录替换为实际生产配置。不需要 `oss:DeleteObject` 权限；当前参考项目凭据已通过上传、读取和只读健康检查。

`./scripts/start-local.sh --check` 与 `/health/ready` 只读检查当前环境目录能否列举，不会上传探测文件，因此不能代替上传、读取的权限验收。真实文件验收使用隔离渠道和数据库，并保留上传的测试对象：

```bash
uv run pytest tests/integration/core/test_files.py
uv run pytest tests/integration/data_lifecycle/test_lifecycle.py -k real_oss
```

GitHub Actions 使用同名 OSS Variables 与 Secrets，并将前缀设为 `creativity-ci/{run_id}/{run_attempt}/`，隔离每次运行的对象。CI 凭据需要该 CI 目录下对应的上传、读取、对象 ACL 和列举权限，未配置时集成检查明确失败。

## 已有对象与初始化恢复

切换已有环境时，先停止本环境 API、Worker 和调度器，再将原桶内的 `channels/...` 对象复制到当前 OSS 环境前缀，全部设置私有 ACL，逐个回读核验字节和摘要后再启动。禁止覆盖目标中内容不同的同名对象；源对象和原存储数据卷保留至迁移确认完成。

空库部署按 [初始化说明](../../sql/README.md) 导入 SQL 后执行 `uv run python -m scripts.restore_init_objects`，再使用 `--check` 只读核验。恢复使用 OSS 的禁止覆盖选项，只写缺失对象；已有对象内容不一致时失败。
