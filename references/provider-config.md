# 原生执行与 API 扩展

默认 backend=native，无需 Key。process 为 generate/repair 生成 requests/<id>.json，状态 awaiting_native。模型查看 reference_crop；遮罩修复用 canvas_mapping.file 作为实际编辑目标，按任务调用宿主工具，再 fulfill 登记原生文件。没有工具时保留待办；不能把仍有待办的批次作为合格结果交付。

生图前检查宿主工具是否可用；缺少工具或调用失败，必须在当前回复中告知受影响素材和已知原因，原因不可得时明确说明未知。提供稍后重试、上传已有设计/修复候选，或经用户明确选择后配置 API。脚本只能识别本地待办和 API 错误，无法探测宿主工具的额度、权限或调用结果；宿主错误由执行 agent 依据实际工具返回说明，不捏造原因。API 不是默认必填配置，不能自动切换、无限重试或要求把密钥发到聊天。

描述模式的初始设计生成也在宿主执行，真实文件通过 design 注册。清单批准前不得调用生成独立素材。结果不可直接设置“已通过”，还需要质量和用户验收。

`reference_crop` 为精确局部基准；存在 `canvas_mapping` 时 `reference` 指向只含该裁片和登记填充的载体，编辑和回映规则见 [canvas-mapping.md](canvas-mapping.md)。不能改用整页描述重绘。使用裁片的 `reference_crop_sha256` 作为 `fulfill --reference-sha256`，第二次修复还要 `--request-id`。记录防止混用或过期引用，实际图案和接缝仍须原图对比。

输出先进入内部检查；检查失败后自动执行 `repair-queue`，按同一规格修复并复检，不能把未通过候选当作完成结果。两轮生成结果均不主动展示；工具支持静默或仅文件返回时优先使用，结果只读取路径及必要元数据，不打印整个对象或图片 Base64，不另行转发原生图。宿主工具若自身强制展示图片卡片，项目 CLI 无法关闭或删除；不虚构隐藏参数，用户询问时再说明实际限制。工具调用仍须遵守宿主要求。最终只展示汇总选出的结果，见 [quality-loop.md](quality-loop.md)。

每轮固定两次实际处理/导入（含首次），API 的 max_attempts 也必须为 2。第 2 次仍失败时比较候选并用 review-summary 返回全部选中项的最优结果；问题记录在内部报告。用户明确导出后直接 export-reviewed，不因失败或次数拦截；不可偷偷切换 API 或加大次数。

## 通用 API 适配契约

这是一个可供自建网关实现的扩展协议，不宣称兼容任何厂商原始接口。使用 API 是用户明确选择的模式；内建不执行线上试调用。配置文件保存在交付目录之外，`process --provider /path/provider.json` 临时读取：

```json
{"backend":"api","endpoint":"https://your-gateway.example/generate","model":"your-model","key_env":"UI_ASSETS_API_KEY","capabilities":["reference_edit","transparent"],"parameters":{},"max_attempts":2}
```

Key 仅从指定环境变量读取，不能写入配置、清单、日志或包。HTTPS POST，Authorization Bearer，JSON 含 asset_id、method、box、target_size、transparent_background、prompt、invariants、occluded、model、parameters 和 reference_image_base64；遮罩修复传绑定载体。响应 `{"image_base64":"<PNG/WebP bytes as base64>"}`，不支持 URL 下载回退。

必须声明 reference_edit；透明请求还需 transparent。不满足能力则失败，不能忽略要求。每次请求超时 90 秒，读取上限 40 MiB，有限重试；错误仅保留泛化原因，不回显响应/认证。不可使用参数中放密钥，所有认证只通过 key_env。外部 API 费用与精度取决于用户配置的服务。

如需接特定厂商，在 providers.py 新增对应适配函数并维持同一请求/审核/质量协议。不把接口地址、模型能力或尺寸限制硬编码到 SKILL.md。
