# Seed3D 接口与来源保留

当前已验证模型 ID：`doubao-seed3d-2-0-260328`，可通过配置替换。调用前确认目标账号开通与模型权限。[创建任务](https://docs.volcengine.com/docs/ark/create-3d-generation-task-api?lang=zh) · [查询任务](https://docs.volcengine.com/docs/ark/get-3d-generation-task-api?lang=zh)

默认 base URL 为 `https://ark.cn-beijing.volces.com/api/v3`。创建请求向 `/contents/generations/tasks` POST，`content` 包含参考图的 PNG data URI 和弱参数文本 `--subdivisionlevel high --fileformat glb`。不要把未知的 `subdivision_level/file_format` 顶层字段当作有效配置；旧请求曾请求 high 但实测只有 500,000 三角面。以实际几何统计为准，不承诺请求分辨率等于结果面数。

Key 从配置的环境变量或 `.env` 读取；不打印内容。`state/seed3d.json` 中服务端响应可能包含短期签名 URL，是私有中间态，不能提交到 GitHub 或打进公开资产包。交付 `source_provenance.json` 只包含任务 ID、模型、请求质量、参考图/GLB 哈希和字节数。

提交前写 `submission_outcome=unknown`。有 ID 后记录 accepted；连接超时、客户端退出或无 ID 时禁止自动重试 POST。使用 Ark 任务列表/控制台找回真正任务，再 `adopt-task`。查询失败或轮询窗口到期也不能触发新提交。

已成功任务可能返回 ZIP 包，里面才是 GLB。脚本保留原包、校验 CRC，只复制唯一 GLB 成员，不按压缩包中的路径解压。GLB 核验版本、声明总长度和块边界，截断下载不能通过。下载使用独立 CDN 请求，不附 Ark Authorization 头。

有 `.partial` 时保留并改名后重新下载，不能把它直接当成完整资产。如果 GLB 已保存但 provenance 尚未完成，恢复会验证已有 GLB 并补写来源记录。若返回 OBJ 等其他格式，先检查生成参数和服务端内容，再用明确的转换步骤处理；默认管线不假装它是 GLB。
