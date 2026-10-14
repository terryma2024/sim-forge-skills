# 环境初始化

## 能力分工与版本

已实测组合：Blender 5.2.2 LTS + Apple Metal；SimForge commit `b80ae50f5f6d8ec4353d8374dce972744e5a233a`（1.0.0）；Isaac Sim 6.0.1 + Linux NVIDIA RTX。其他版本先预检，尤其是 Isaac Python API。Python CLI 使用 3.12+；视频需要 FFmpeg（含 libx264）。

Mac 可以生成参考图、调用 Seed3D、处理 Blender/PBR/USD。实际 Isaac Sim 需要受支持的 NVIDIA 环境；Mac 可通过 SSH 控制该环境。[官方要求](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/installation/requirements.html)

## 本地

1. 安装 Blender 和 Python 3.12+，macOS 可用 `brew install --cask blender`、`brew install python@3.12 ffmpeg uv`。若已有应用直接复用，不重装或覆盖用户配置。
2. 初始化 run，然后执行 `pipeline.py bootstrap --run-dir ...`，下载固定版本原版 SimForge 并构建 ZIP。CLI 通过源码直接调用服务，不依赖全局启用插件。
3. 如需在 Blender 打开 SimForge 面板，在 Preferences → Extensions → Install from Disk 安装 bootstrap 返回的 extension ZIP。安装前保存偏好备份；确认启用 SimForge。交付 `.blend` 保存高低模与面板配置。
4. 用 `doctor` 检查可执行文件、源码、FFmpeg、Key 是否存在（只返回布尔值）。实际 GPU 能否烘焙由 Blender worker 验证；仅看到 Metal/CUDA 名称不是完成证据。
5. 凭据优先读 `seed3d.key_env` 指定的环境变量，其次指定 `.env`。默认 `ARK_API_KEY`；其他现有变量名可配置。不将 Key 写进配置 JSON、聊天或仓库。

## 可选 Blender MCP

MCP 用于交互查看和编辑；批处理管线不要求 MCP。先检查是否已配置。需要安装时，使用当前平台可执行的绝对 `uvx` 路径：

```bash
uvx mcp-for-blender==2.1.9 install-addon
codex mcp add blender --env BLENDER_MCP_APPS=1 --env BLENDER_MCP_OPENAI_FORMS=1 \
  -- /absolute/path/to/uvx mcp-for-blender==2.1.9
codex mcp list
```

在 Blender 启用 MCP for Blender，确保只运行一个对应服务；重启客户端使配置生效。不要混用不同 blender-mcp 项目的插件/服务。更新已有 MCP 配置时保留其他 server，并保存备份。[包文档](https://pypi.org/project/mcp-for-blender/2.1.9/) · [Codex MCP 配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

## NVIDIA 仿真机器

复用已安装的 Isaac Python 环境，在配置中写它的 Python 可执行路径。检查 NVIDIA 驱动/GPU、Isaac 版本、磁盘空间以及 `omni.replicator.core`、`isaacsim.core.experimental.prims` 是否可用。下载或安装新 Isaac 时按目标版本的官方指南执行；不要替用户默默接受新许可证。[Python 安装指南](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/installation/install_python.html)

已完成环境/许可证初始化后，在私有 run 配置中设置：

```json
{
  "isaac": {
    "host": "my-ssh-alias",
    "directory": "/data/simforge-runs",
    "python": "/opt/isaac-env/bin/python",
    "eula_accepted": true,
    "upload_authorized": true,
    "authorized_destination": {"host": "my-ssh-alias", "directory": "/data/simforge-runs"}
  }
}
```

`upload_authorized` 与 `eula_accepted` 记录实际用户授权和既有许可状态，不是取得授权的替代品。SSH 使用现有密钥/agent；无密码写入脚本。每个远端尝试创建该目录内的唯一子目录。Windows 本地 Isaac 可配置 Python 路径；自动 SSH 模块目前针对 POSIX 远端。

原版插件来源及 MIT 许可：[XIAODUOLU/SimForge](https://github.com/XIAODUOLU/SimForge)。bootstrap 把它保留在运行目录，不将用户偏好或上游源码混入本 Skill 仓库。
