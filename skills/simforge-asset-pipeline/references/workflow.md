# 配置、产物与恢复

## 运行契约

每个独立资产一个 `--run-dir`。`init` 保存配置与自然语言需求；`assets/` 是交付，`state/` 是私有运行状态，`logs/` 是工作日志，`.runtime/` 是固定上游依赖，`.attempts/` 保存失败的烘焙/导出尝试。

配置由 `common.py` 默认值与 `--config` JSON 合并。只填写区别：

| 配置 | 意义 |
| --- | --- |
| `mode` | `checkpoint` 或 `end-to-end` |
| `asset.height_m / mass_kg` | 真实高度米、质量 kg；从需求提取或用明确标注的演示值 |
| `asset.require_cavity` | 是否必须通过实际几何空腔检查 |
| `asset.static_friction / dynamic_friction / restitution` | 显式物理参数，不能由视觉材质推断为已标定值 |
| `seed3d.model / key_env / env_file` | 模型 ID、Key 变量名、凭据文件；不填 Key 值 |
| `seed3d.poll_seconds / wait_seconds` | 查询间隔与单次观察窗口；窗口结束仍保留同一个任务 |
| `blender.executable / gpu_backend` | 可执行路径、`AUTO` 或实际可用 `METAL/OPTIX/CUDA/HIP/ONEAPI` |
| `blender.target_faces / voxel_size` | 重拓扑目标（不保证面数完全相等）和保持薄壁的分辨率 |
| `blender.resolution / samples / cage_extrusion` | 贴图像素、Cycles 样本、烘焙笼距离（标准化 Blender 单位） |
| `simforge.commit / source_dir` | 完整 commit；bootstrap 设置本次运行的源码目录 |
| `isaac.*` | 机器授权/路径及 `drop` 或 `cup-comparison` 任务配置 |

默认普通资产使用 `drop` 和标准低角阻尼。中空杯示例用 `examples/cup-demo.json`：0.09m、0.3kg、2048px、五通道、动态刚体、两个相同几何的 USD 碰撞版本。示例显式使用角阻尼 300/s 以持续展示凸包封口效果，不宣称真实材料精度。

自然语言“一键到底”是代理入口：理解需求 → 生成参考图 → 写配置 → `init --mode end-to-end` → bootstrap/doctor → run。CLI 一键入口要求参考图或已有模型；没有图像生成后端时返回 `needs_reference_image`，不会用程序化模型代替 AI 模型。

## 中间产物

- 原始输入：`reference.png`、`source.glb`、`source_provenance.json`。源高模保留哈希和实际面数。
- 3D 视觉确认：`source_preview.png`、`source.blend`、`source_report.json`。
- 重拓扑：`retopo.blend`、`retopo_report.json`，有 UV、独立低模、非流形边检查；中空任务同时检查高/低模型杯腔。
- 烘焙：`baked.blend`、`bake_report.json`、`baked_textures/` 五通道 Normal/Diffuse/Metallic/Roughness/Alpha。
- 交付资产：`asset_pipeline.blend`、两个 USD 和各自 `_textures/`、`asset_validation.json`、`high_low_comparison.png`。
- 实际仿真：`cup_comparison_scene.usda`、`simulation_result.json`、`simulation_frames/`、`simulation.mp4`、首末帧与 `completion.json`。

移动 USD 要连同同名 `_textures` 目录和场景引用的其他 USD 一起移动。Blender checkpoint 贴图已打包；USD 依赖保持相对路径。两个 USD 的几何、质量与材质相同，区别只有碰撞近似。

## 确认和恢复

`run` 遇到确认点返回 `awaiting_review` 和实际路径；展示可视图，等用户接受后调用 `approve`。确认保存文件哈希；修改后须重新检查。自动模式只省去用户逐阶段确认，不省掉代理视觉检查。

```bash
python "$SKILL_DIR/scripts/pipeline.py" approve model --run-dir "$RUN"
python "$SKILL_DIR/scripts/pipeline.py" run --run-dir "$RUN"
```

已有模型时：

可以不提供参考图；此时跳过 reference 确认点，从真实 source_preview 开始，不要求凭据或创建新的 Seed3D 任务。

```bash
python "$SKILL_DIR/scripts/pipeline.py" import-model --run-dir "$RUN" \
  --glb "$EXISTING_GLB" --provenance "$PROVENANCE_JSON"
```

已有 Ark ID 或 POST 结果未知时：先人工核对服务端 ID，再 `adopt-task --task-id ... --run-dir ...`；不能猜一个新任务或重新付费提交。观察窗口结束后再次 `run` 查询同一个 ID。

Blender 阶段用收据及哈希跳过已完成工作。运行中不要编辑资产处理配置；需要新的尺寸、面数或贴图分辨率时使用新 run。复制运行目录到其他机器后可再次 bootstrap 定位同一固定源码；机器路径变化不会推翻原资产收据，数值参数、commit 和产物哈希仍须一致。部分贴图/导出失败保留 `.attempts/`，不要删除源高模。若失败发生在最终文件搬运后，先检查 `.attempts` 与日志，在新 run 导入同一源模型重新执行。

远端启动前记录唯一尝试目录；启动后 `process.pid` 和 `remote.json` 用于观察。`simulation_running` 只是存活任务的状态；再次 `run` 接续同一个尝试。断线或观察超时不会自动重启。已有 `runner.lock` 时先核对 PID 与远端任务，确认没有活跃执行再清理陈旧锁。

`assets_complete`、预检通过或渲染预览都不能代表物理完成。最终要求所选任务的 `passed=true`、完整轨迹、实际 RGB、可解码视频，以及已接受的视觉结果。
