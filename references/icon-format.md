# SVG 优先与透明 PNG 回退

所有新图标先按 SVG 方案分析；不得因原图模糊、图案复杂或抠图方便，直接将整批图标设为 PNG。分析阶段仅允许用于格式判定的最小 SVG 试绘、真实渲染与原图比较，不提前生成其他正式素材。

分析模板的每个图标须填写 icon_policy：clarity（clear / ambiguous / textured），outline_verifiable、details_verifiable、appearance_verifiable（布尔），uncertain_features、simplifications（字符串数组），approach、reason。reason 写实际观察。

- faithful-vector：轮廓、细节、原设计外观均可核对，uncertain_features 为空。
- conservative-vector：轮廓与外观可核对；列出无法辨认的微小细节及有限简化，不把模糊团块画成明确竖线、字符或装饰。内部符号影响识别或整体外观时不采用此路径。
- source-raster：无法可靠核对轮廓、内部标记或保留原设计层次且已有 SVG 实际试绘与渲染对比失败的证据时，才输出透明 PNG，优先从源图提取并保留真实透明边缘。不能把错误 SVG 栅格化冒充回退；不能把放大插值称为细节提升。PNG 的 code_reproducible 基于是否能可靠还原整个前景判断。

analysis-check 强制检查字段及格式相容性。模糊输入不等于全部改 PNG，也不等于可以猜出更多细节。优先保持原生显示尺寸；放大仅用于检查。

生产结果的 visual-review 增加 icon_fidelity：verdict、note、source_features、output_features、remaining_differences。比较形状、线宽、内部标记、书页/封面等层次和比例。passed 时 remaining_differences 必须为空；疑点写 unknown，不可把“仍有颜色/形状问题”改标 passed。每版重新观察，不复制旧版文字后只改结论。

### 真实对比证据

每版 SVG 必须对照对应原图的精确源框和真实渲染，保存一张左右对比图及文件哈希。先按原生显示尺寸比较，再以相同倍率放大两侧检查。保留长宽比；不得各自拉满格子、分别缩放宽高或用不同放大倍率，让比例差异被掩盖。注明对齐和缩放依据，放大只用于观察，不代表恢复细节。

`render-preview` 自动生成 `quality.source_comparison`：记录 source、reference_box、source_sha256、source_crop_sha256、vector_sha256、render_sha256、version、对比图 file/sha256 和 trim_offset。按 SVG 原生坐标与收紧前偏移回填，不能居中或按轮廓框再次缩放而隐藏位置、比例问题。渲染替换会清除旧视觉结论。审核之前必须实际查看该对比图。

`icon_fidelity` 除既有字段外，还必须填写实际查看的 `comparison_sha256`，以及以下两个对象：

- `features`：七个键 `outline`、`proportions`、`strokes`、`internal_marks`、`color`、`edges`、`layers`。每项分别写 `source`（源图具体观察）、`output`（输出具体观察）、`verdict`（passed/issues/unknown）、`differences`（实际差异数组）。没有内部标记也须分别说明原图和输出都没有；不能用同一句泛化描述填满所有特征。
- `independent_review`：`verdict`、`note`（第二遍实际观察）、`differences`（仍存在的差异数组）。重新从原图出发检查，不复制首次结论，不因次数用完或用户等待而写通过。

脚本核对证据绑定、确认哈希、特征完整性和判断自洽性，不能代替真实视觉判断。任何特征或第二遍仍有差异/unknown，整体不得 passed。明确导出时不运行这些质量门禁，只保留审核结论。

模型必须实际查看对比图，再逐项写出源图和输出的轮廓、宽高比例、线宽、圆角、内部标记、孔洞、颜色渐变、柔和边缘和层次。日历的挂耳与页内图案、奖杯的杯耳与徽记、书本的封面与内页必须分别比较；“都是日历/奖杯/书本”“颜色相似”“已渲染”不能作为保真通过的证据。存在形状或比例差异时明确记录 issues；源图细节不可辨时记 unknown，不自行创造明确符号，也不声称 faithful-vector。

真实渲染、透明、边界和文件检查只证明文件可用，不证明外观还原。逐项差异未解决时不得将 icon_fidelity 写为 passed。用户要求直接导出仍按导出规则执行，保留不通过结论，不能把导出选择改写成还原验证通过。

格式回退须重新分析并更新清单，不能为绕过失败私改格式。限定例外：同一轮两版 SVG 均有有效原图对比且保真结论仍为 issues/unknown 时，自动转为同一源框的透明紧裁 PNG，无需再向用户确认；PNG 额外最多处理两次（首次提取加一次优化）。保留 SVG 失败记录和预算，PNG 单独计次，不能在 SVG/PNG 间反复切换。脚本只允许此回退使用 crop 源图提取，拒绝把错误 SVG 栅格化导入。汇总把 PNG 与 SVG 图标放在同一预览中，标明格式。

SVG 的主体轮廓或内部徽记仍无法可靠还原时，应评估源图 PNG；不要只因 SVG 两次失败就停止。执行上述两次 SVG 加两次 PNG 的规则；更早回退或更改源框等不属于自动例外，沿用已有适用授权或正常确认。无法提取 PNG 时，失败汇总必须提供 raster_fallback:{decision:"unavailable",reason:"具体原因"}，脚本拒绝无说明地保留失败 SVG。

浅色背景与白色图案必须区分。杯身徽记等闭合白色前景可用 matte_preserve_regions:[[...]] 明确保留；只框前景内部，不能把外围背景也框入。必须检查原底色及对比底色，避免把徽记抠空或留下白边。

## PNG 回退证据

icon_policy.svg_trial 必须包含 source、reference_box（与当前素材原图和源框一致）、verdict="issues"、svg_file、svg_file_sha256、render_file、render_file_sha256、source_features、output_features、remaining_differences（非空具体差异数组）。两个文件路径使用绝对路径；SVG 必须为真实矢量，render_file 为该 SVG 的真实渲染图，不能用源裁片代替。证据绑定当前 analysis 的 source 与 reference_box，不能挪用其他图标或旧版本的试绘。

analysis-check 核实文件存在、哈希与格式有效并要求实际对比记录；仅写“SVG 无法还原”或用 passed 的 SVG 作为回退理由会被拒绝。脚本不能仅从哈希证明渲染对应关系或审美判断正确，执行 agent 仍须查看源图和真实渲染。SVG 仍可修正时先修正；确认无法合理保留原设计后才采用 PNG。
