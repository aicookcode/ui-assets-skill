# 分析与生成循环

## 分析：最多 3 次

`analysis-template --file manifest.json` 输出按素材 ID 分组的检查结构。逐项对照源图填写 source、reference_box，以及 scope / composition / method / format / source_edges / mask_plan 的 verdict 与具体 note。verdict 为 passed、issues 或 unknown。

`analysis-check --file manifest.json --checks-file analysis.json` 合并脚本错误与视觉判断。未通过时根据全部 findings 修改、复检，最多 3 次（含首次）；重复提交相同失败记录不能当作修改。第 3 次后标记输出全部未满足项，等待用户修改或明确许可。

`allow-analysis` 仅在第 3 次后、技术检查有效时记录带视觉疑点继续的用户许可；疑点会保留在编号清单及生成任务中，不代表最终质量通过。`revise-analysis` 只用于用户新修改，开启新轮并保留旧记录。边界、方法等变化须重新分析；不可篡改检查来清空预算。

## 生成：每阶段最多 2 次

SVG 图标两次实际输出均完成有效原图对比、仍有保真 issues/unknown 时，额外允许透明 PNG 阶段最多两次（首次源图提取和一次优化），无需另问格式变化。先对同一原图、源框和尺寸的 PNG crop 方案执行 analysis-check，再 manifest；仅此变更自动继承清单选择授权。旧 SVG 的候选、检查和两次计数全部保留，png_fallback 单独登记 SVG 版本及 PNG 上限，不能再次回退领取预算。PNG 仍须逐版对比原设计，不因格式变化直接判通过。PNG 两次后仍失败则按下列规则选择一个最优候选展示。其他素材仍只有原来的两次。

输入就绪检查先于实际计次。缺少或不可读的 reuse/vector 输入属于 `awaiting_input`，不是生成失败：不消耗预算、不重试同一缺项、不影响批次其他素材。Agent 继续补齐可制作的输入，之后再次 `process` 自动重新检查并续处理，无需 revise、重置或再次询问。宿主请求准备与缺失的 fulfill 结果文件同样不计次；真实生成调用、处理和候选导入仍按原两次上限记录，不能借等待状态恢复预算。

1. 清单批准后 `process`。遮罩修复的编辑目标为 `canvas_mapping.file`，按 [canvas-mapping.md](canvas-mapping.md) 校验载体并回映到精确源尺寸，再合成遮罩；原生分辨率不作为最终尺寸。
2. 每次结果用 `inspect` 对照原图。SVG 先真实渲染，再以 `render-preview --asset ID --version N --vector-sha256 SHA --file render.png` 绑定证据；脚本生成 `source_comparison` 原尺寸/6倍原图对比图。实际查看后记录 `comparison_sha256`、七类特征和独立第二遍复核。原图、裁框、版本、渲染、对比图不对应或被改动时拒绝审核。不得用原图截图冒充 SVG 渲染。
3. `visual-review` 完整填写 color、contour、details、proportion、transparency、artifacts、palette、structure、new_elements、text_residue、seams；背景/插画增加 composition，局部修复增加 repair_boundary；装饰位图增加 container_corners、edge_padding、content_position。画布回映后增加 canvas_alignment。每项写具体观察，不能只写“符合规则”。
4. 文件错误及实际对比的 issues/unknown 自动排队；先补缺失的检查证据，再按 `repair-queue` 调整修复并复检。不能因为一个尺寸错误就忽略圆角、留白或构图错误。保持既有参考和规格，不放宽阈值或扩大遮罩。
5. 修复用 `fulfill --asset ID --file RESULT --request-id REQUEST --reference-sha256 CROP_SHA`。新结果重新检查，不沿用旧结论。创建任务不计次，实际处理/导入总计最多 2 次（含首次）。
6. 第 2 次仍失败：查看本轮全部候选，选择最佳且说明比较理由。写 `[{"id":"ID","attempt":1,"reason":"第一版缺陷较少，具体为……；仍有……"}]`，覆盖全部失败项。执行 `review-summary --file best.json`；全通过时无需 --file。汇总必须包含全部选中项，既有合格图标也有失败位图，每项仅一个候选，不默认最后一版最优。旧 failed-preview 仅供内部候选诊断，不能替代完整返回。
7. 合格验收路径要求全部选中项 passed 才 `preview`，用户验收后才 `accept` 和 `deliver`。用户明确回复“导出”则立即 `export-reviewed` 导出已展示的最优候选，不再修复、重复确认或因质量/次数拦截；原质量结论保留。

内容审核以整体画面与主要对象为单位，用 major_objects 记录是否明显增减花束、树、船等；不逐一计数花瓣或纹理斑点。使用 content-comparison.png 排除补角区，完整 comparison.png 另查补绘和接缝。图案补角增加 corner_texture：看原尺寸及4倍平滑放大，确认无棋盘、像素条纹、重复图案、模糊补块及明显接缝；不能因补角区被排除出内容对比而省略检查。

用户要求修改脚本规则并复验已有候选时，执行 `reprocess-candidates --assets ... --user-text USER_TEXT --message-ref MESSAGE_REF`。仅重跑当前轮同一哈希原始文件；规则代码未变则跳过。创建新处理版本，保留旧版与原始候选，不调用生图、不重置次数、不沿用视觉结论。复检后再 review-summary。

## 原图保护

内部报告保留 failure_summary；失败 SVG 先评估 PNG 回退。最终效果图不显示检查状态或问题标记，按 display.md 统一询问用户修改还是导出。白色前景徽记用 matte_preserve_regions 保留，不能当背景孔抠空。

去字按完整、已确认无主体的文字窗口修复并以原图边界校色；实际检查合成后的字形色差，不只检查原生生成图。图标的格式分析与新版本核对见 [icon-format.md](icon-format.md)，含 PNG 回退和 unresolved 差异的程序拦截。

- 局部修复使用已批准的 repair_regions，遮罩外像素来自原图。装饰位图执行 [surface-contract.md](surface-contract.md)，检查原底边保留证据、四角、图案位置及新增空带；原有留白和必要底色过渡须保留。
- `fidelity_regions` 可声明原图像素 preserve 区或纯色 plain 区。完整字段及用途见 [fidelity-rules.md](fidelity-rules.md)。只对确认的纯底色声明 plain，不能把水彩、纸纹或半透明图案误当纯色。
- 文件哈希、尺寸和像素检查不能证明语义观察正确，必须看精确源图与结果；输出经过重建时不宣称无损提取。
- 返回只内嵌 review-summary 的 display.md 所列 PNG，另给 SVG 矢量预览链接。脚本核对完整项数、候选哈希及 SVG 实际渲染，不改变版本、次数、验收和导出状态。两轮候选均只供内部检查，不主动展示或转发工具原始图，不打印包含图片数据的完整结果对象；工具支持静默返回时优先使用。宿主自身强制显示的图片卡片不受本脚本控制，不能宣称已经关闭。
