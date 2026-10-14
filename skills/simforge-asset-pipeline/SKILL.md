---
name: simforge-asset-pipeline
description: Use when a user wants AI-generated 3D objects or natural-language descriptions turned into robot-simulation assets through Seed3D, Blender/SimForge and Isaac Sim, including staged visual review, environment setup, or an end-to-end run.
---

# SimForge 仿真资产管线

将自然语言或参考图变成可检查、可复跑的仿真资产。目标是**真实模型、原版 SimForge 服务、实际 Isaac Sim 轨迹和画面**；最终范围由用户要求决定。

## 选择模式与输入

- 默认 `checkpoint`：展示参考图、原始 3D、Blender 资产、Isaac Sim 结果，分别等用户确认后继续。
- 用户说“一键到底 / 端到端 / 不用中间确认”时用 `end-to-end`：代理自动检查这四个产物并连续执行，不新增阶段审批；现有外部传输授权仍须覆盖具体目的地。
- 已有参考图、GLB 或 Ark 任务时复用它们，从相应阶段恢复。缺少参考图时用当前代理可用的图像生成工具；只有缺少不可推断的信息才询问用户。

已有 GLB 且无参考图时直接从 `model` 确认点开始；参考图阶段标为不适用，不额外生成与原模型无关的图。

从需求提取物体、外观、真实尺寸（米）、质量（kg）、是否需要空腔、验收任务和输出位置。不能从外观推断已标定的质量/摩擦。普通实心资产用 `drop`；杯子落球对照用 `cup-comparison` 并要求几何空腔。多部件装配、机器人关节和液体仿真不属于此脚本的自动验收范围。

## 执行入口

`scripts/pipeline.py` 只需要 Python 3.12+ 标准库；Blender 使用自己的 Python 和 USD 库。命令均可从任意工作目录执行，`SKILL_DIR` 指向本 Skill 的绝对路径。环境初始化先读 [environment.md](references/environment.md)；配置/确认协议读 [workflow.md](references/workflow.md)。

```bash
python "$SKILL_DIR/scripts/pipeline.py" init --run-dir "$RUN" \
  --prompt "中空陶瓷杯，高9cm、质量300g" --config "$CONFIG" \
  --mode checkpoint --image "$REFERENCE"
python "$SKILL_DIR/scripts/pipeline.py" bootstrap --run-dir "$RUN"
python "$SKILL_DIR/scripts/pipeline.py" doctor --run-dir "$RUN"
python "$SKILL_DIR/scripts/pipeline.py" run --run-dir "$RUN"
```

`CONFIG` 是按需求填写的 JSON；杯子示例可从仓库 `examples/cup-demo.json` 开始。图像生成在代理工具中完成；CLI 不假装能从文字自动生成位图。无参考图时 `run` 返回 `needs_reference_image`，用 `provide-reference --image ... --run-dir ...` 接上它。

## 四个确认点

| 确认点 | 必须展示并检查 | 继续命令 |
| --- | --- | --- |
| `reference` | `assets/reference.png`；形状、材质、杯口/把手与需求一致，无遮挡背景 | `approve reference` |
| `model` | `source_preview.png`、原始 `source.glb`、`source_report.json`；真实尺寸、源哈希、杯腔是否存在 | `approve model` |
| `blender` | `high_low_comparison.png`、`asset_pipeline.blend`、`asset_validation.json`；低模/UV、五张 PBR、单位/质量/碰撞和相对贴图引用 | `approve blender` |
| `simulation` | `simulation.mp4`、首末帧、`simulation_result.json`；实际落地/接触、稳定性和所选任务是否通过 | `approve simulation` |

每个 `approve` 命令都带 `--run-dir "$RUN"`，随后重新 `run`。只在用户已经接受该产物后写确认；确认绑定当前文件哈希，修改图像/模型后不能沿用旧确认。端到端模式仍生成和检查全部中间证据。

## 执行与完成的证据

1. 每次资产使用新的运行目录；原始 GLB、阶段 `.blend` 和失败尝试保留。用 `status` 查看收据和任务状态。`run --until assets` 只完成本地资产，不代表 Isaac 已通过。
2. 原版 SimForge 依赖固定 commit，由 `bootstrap` 下载、构建并记录哈希。GPU 烘焙使用 Metal/OptiX/CUDA 等实际可用后端；不把 CPU 降级伪称为 GPU 成功。
3. Seed3D 只提交一次。`generation_pending` 时继续查询同一个 ID；未知提交结果先核对 Ark，再 `adopt-task`。读 [seed3d.md](references/seed3d.md)，不要因等待超时重新付费提交。
4. Mac 处理资产；实际 Isaac 在配置的 NVIDIA Linux/Windows 环境执行。首次传输前落实用户对 host、directory、文件类型和执行动作的授权；记录到 `isaac.authorized_destination`。已有明确授权继续复用。Key 只从环境变量/指定 `.env` 读取，永不上传到仿真机器。
5. `simulation_running` 记录具体远端尝试/PID；恢复会查这个任务，不启动替代任务。进程退出 0 不能证明 Isaac 成功，必须核对 `passed=true`、实际轨迹与 RGB 帧/视频。
6. 最终交付资产目录、视频、源记录、验证 JSON 和复跑配置。描述当前范围：碰撞演示不是材料标定或机器人抓取验证。`cup-demo` 的高角阻尼是明确的演示参数，不自动推广到其他资产。

遇到缺失杯腔、薄壁穿透、空 RGB 或失败尝试，读 [troubleshooting.md](references/troubleshooting.md)。诊断后最多进行 3 次修正；相同错误连续 2 次则停止自动改动并报告当前证据，保留完整任务目标。
