# SimForge Skills

把自然语言或参考图转成可用于机器人仿真的 3D 资产，覆盖 Seed3D → 原版 Blender SimForge → PBR/物理 USD → 实际 Isaac Sim 验收。

[主 Skill](skills/simforge-asset-pipeline/SKILL.md) · [环境初始化](skills/simforge-asset-pipeline/references/environment.md) · [配置与恢复](skills/simforge-asset-pipeline/references/workflow.md)

## 两种使用方式

在 Codex 中安装后直接说：

> 用 $simforge-asset-pipeline 生成一个高9cm、质量300g的中空陶瓷杯，在参考图、3D模型、Blender资产和Isaac Sim效果处让我确认。

或：

> 用 $simforge-asset-pipeline 一键到底生成并验证这个杯子，使用我已授权的 NVIDIA 仿真机器，保留全部中间产物。

默认是四阶段确认；端到端模式由代理生成参考图、填写配置、检查视觉结果并连续执行。脚本本身不调用代理专有的图像工具，纯 CLI 需提供参考图或已有 GLB。

## 安装 Skill

```bash
git clone https://github.com/terryma2024/sim-forge-skills.git
mkdir -p "$HOME/.agents/skills"
ln -s "$PWD/sim-forge-skills/skills/simforge-asset-pipeline" \
  "$HOME/.agents/skills/simforge-asset-pipeline"
```

若同名 Skill 已存在，先检查是否是本仓库的旧版本再更新，不覆盖无关目录。Codex 支持 `.agents/skills` 与 symlink；其他支持 Agent Skills 的客户端也可加载该目录。[官方 Skill 目录说明](https://learn.chatgpt.com/docs/build-skills)

## CLI 快速开始

Python 3.12+，Blender 5.2，FFmpeg；Isaac Sim 在受支持的 NVIDIA 机器上运行。以下在仓库根目录执行，先把 `reference.png` 换成真实的 PNG 图：

```bash
python3 skills/simforge-asset-pipeline/scripts/pipeline.py init \
  --run-dir runs/cup --prompt "高9cm、300g的中空陶瓷杯" \
  --config examples/cup-demo.json --image reference.png --mode end-to-end
python3 skills/simforge-asset-pipeline/scripts/pipeline.py bootstrap --run-dir runs/cup
python3 skills/simforge-asset-pipeline/scripts/pipeline.py doctor --run-dir runs/cup
python3 skills/simforge-asset-pipeline/scripts/pipeline.py run --run-dir runs/cup --until assets
```

`--until assets` 仅完成本地资产。配置 `runs/cup/config.json` 中的 Isaac 机器/路径及实际授权后，再 `run --run-dir runs/cup` 完成物理验证和视频。需要逐阶段确认时初始化 `--mode checkpoint`；每次 `awaiting_review` 展示产物，用户接受后 `approve reference|model|blender|simulation --run-dir ...` 再恢复。

Key 放在 `ARK_API_KEY` 环境变量或 `~/.env`；已有其他变量名可改 `seed3d.key_env`。不要把 Key 写到 JSON 或提交仓库。每个新资产一个新的 run；所有任务状态和产物保留在其中。现有 GLB 用 `import-model`；未知提交用 `adopt-task`，不会自动重复付费。

## 已验证范围

本 Skill 的可执行流程已用真实 Seed3D 杯子实测：50万三角面 → 8,949四边形面 → 2048px五通道 → 两个相同几何的物理USD → Isaac Sim 6.0.1 双杯落球测试，600个轨迹采样/组、150帧真实RGB。Mac 上使用 Metal，Linux 上使用 NVIDIA RTX；远端观察超时后通过同一任务 ID/PID 恢复。

`cup-demo.json` 显式使用小球高角阻尼300/s，持续展示凸分解保留空腔和凸包封口的区别。默认通用配置是普通落地测试和低角阻尼。此示例未标定真实陶瓷或小球材料，也未验证机械臂抓取。新模型可能需要视觉审查、调整重拓扑与碰撞配置；不承诺任意AI模型都自动可用于仿真。

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖确认哈希、断点恢复入口、目的地授权、未知提交防重复、ZIP安全提取和实际验收结果判定。实际 GPU/Isaac 集成证据见 [验证记录](docs/validation.md)。

本仓库遵循 Apache-2.0。上游 [SimForge](https://github.com/XIAODUOLU/SimForge) 按 MIT 许可分发；bootstrap 下载其固定版本并保留上游许可证，本仓库不分发用户模型、凭据或私有环境配置。
