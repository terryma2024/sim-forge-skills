# 从证据定位问题

| 观察 | 下一步 |
| --- | --- |
| Ark 格式错误/401 | 核对 Key **变量名**、账户和模型权限；不要让用户粘贴 Key；未知提交先查原任务 |
| 浏览器站点权限拒绝 | 说明拒绝原因并走授权设置；不能换浏览器绕过拒绝；已有明确授权的 API 是独立入口 |
| macOS 沙箱 Blender 退出 139 | 检查 crash/log；按工具正常审批请求原生执行权限，不修改全局安全设置 |
| 缺少 usable cavity | 打开 source_preview 与 source.blend；检查生成结果，重新生成需新的 run/任务授权；不要换成程序化杯子来“通过” |
| 重拓扑填掉杯底/内腔 | 对照高低模测量；在新 run 调整 voxel_size 和目标面数，保留原始高模 |
| 烘焙完成但缺纹理 | 核对五通道实际文件、分辨率、材质绑定、GPU 日志，不能仅看进程退出 |
| 小球穿薄壁、杯子被顶翻 | 开启场景和刚体 CCD，检查接触偏移、单位、固定时间步；从三维轨迹确定是否穿透 |
| 凸包球先被挡住再滚走 | 这是 AI 凸包顶面倾斜时可能的真实行为。不要隐藏它；若做稳定对照演示，显式选择并披露同组阻尼参数 |
| RGB 空/0×0 | 检查 actual annotator 数据；Isaac 6.0 预检需要 `--/exts/isaacsim.core.throttling/enable_async=false`，预热后固定时间轴 |
| `UsdContext.is_stage_loading` 不存在 | 用安装版本的 `isaacsim.core.experimental.utils.stage.is_stage_loading()` |
| Isaac 出错但 exit=0 | fastShutdown 可能掩盖 Python 失败；查 traceback、`simulation_result.json.passed`、完整帧数 |
| 远端观察超时 | 查已经记录的 host/attempt/PID；继续查询同一个任务，不能仅凭超时重启 |
| SSH 上传被自动审核拒绝 | 报告具体动作与审核原因，保留本地产物；落实指定目的地的授权后再执行同一动作 |

默认碰撞演示不代替真实摩擦、惯量和质量标定。高阻尼示例只是保持可观察的对照，不是所有杯子的正确材料参数；正式机器人任务应使用测量的物理属性，验证接触、抓取和任务成功率。

相关原理：[NVIDIA 碰撞近似与接触偏移](https://docs.isaacsim.omniverse.nvidia.com/latest/physics/simulation_fundamentals.html) · [RGB 采集排障](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/replicator_tutorials/troubleshooting.html) · [固定录制时间步](https://docs.isaacsim.omniverse.nvidia.com/latest/replicator_tutorials/tutorial_replicator_isaac_snippets.html)
