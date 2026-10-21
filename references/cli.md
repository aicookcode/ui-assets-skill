# 可执行工具

以下命令中的 `studio.py` 指本 skill 的 `scripts/studio.py` 绝对路径，`python` 指已具备 Pillow 的 Python 3.10+。`--project` 在子命令之前。路径含空格时加引号；JSON 使用 UTF-8。安装依赖：`python -m pip install -r <skill>/requirements.txt`。

## 项目与清单

分析证据必须包含 `__inventory__`，用于原图元素盘点、代码实现项和重复图标去向；结构见 [source-inventory.md](source-inventory.md)。不能从素材清单自动宣称盘点完整；`analysis-check` 与后续执行会校验该记录，旧项目缺少时须补盘点并重新分析。

```text
python studio.py --project output/demo init --mode B --sources design.png
python studio.py --project output/demo scope --all --user-text "不补充，分析全图" --message-ref MESSAGE_REF
python studio.py --project output/demo analysis-template --file manifest.json
python studio.py --project output/demo analysis-check --file manifest.json --checks-file analysis.json
python studio.py --project output/demo manifest --file manifest.json
python studio.py --project output/demo approve-manifest --user-text "全部" --message-ref MESSAGE_REF
python studio.py --project output/demo process
python studio.py --project output/demo status
```

`scope --all` 仅在用户选择全图后使用。限制范围用 `scope --file scope.json`，格式见 scope-rules.md。

`process` 和 `status` 的 `notices` 返回待宿主生图/编辑或图像处理失败提示。待办不代表宿主调用成功；执行 agent 必须检查当前工具并在不可用或调用失败时告知用户，处理选择见 [provider-config.md](provider-config.md)。

`reuse` / `vector` 清单须预先登记计划写入的绝对 `input_file`。清单批准后先把真实输入写入该路径。若提前 `process`，输入缺失或不可读会返回 `status=awaiting_input` 和 `notices.code=input_not_ready`，没有其他活跃任务时项目为 `INPUT_PENDING`；此项不计次、不重试、不生成候选，其他就绪素材正常处理。之后文件准备好，再次 `process` 会重新检查并继续同一项，不要求 revise 或新一轮批准，也不重置真实处理次数。没有登记路径时先按正常流程补齐清单字段；不能只创建文件而让脚本猜测路径。

等待状态不等于停止执行：Agent 应继续完成可自行制作的 SVG 或其他输入，再调用 `process` 恢复；脚本本身不后台监听文件。`fulfill` 的结果文件也在计次前验证，文件未出现时保留原等待状态，文件就绪后重试同一个导入任务即可。只有实际调用生成、执行处理或导入有效候选时才开始计次，准备请求和检查依赖不计次。

将 analysis-template 返回的 JSON 保存为 analysis.json；查看精确原图后填写实际观察再 analysis-check。未通过时按报告修改，最多检查 3 次（含首次）。第 3 次仍失败时展示 marked 项；用户明确允许带视觉疑点继续才 `allow-analysis --user-text USER_TEXT --message-ref MESSAGE_REF`，不能覆盖越界坐标、无效遮罩或缺失证据。用户提出新修改后 `revise-analysis --user-text USER_TEXT --message-ref MESSAGE_REF`。完整循环见 [quality-loop.md](quality-loop.md)。没有当前分析记录不能 manifest 或 process。

所有确认类命令（scope、approve-design、approve-manifest、select、accept、revise、restore、reject、allow-analysis、revise-analysis）必须传 `--user-text` 与 `--message-ref`，记录真实用户原话及其消息引用。`select` 无修改意见时已经批准清单，不再重复 approve-manifest。

描述模式先 `init --mode A`，在宿主生成图后 `design --sources generated.png`，用户确认后 `approve-design`，再走范围和清单。

清单是完整数组，不是局部 patch：

```json
[
  {
    "id":"icon-001","name":"首页星星","source":"source-1",
    "page":"home","category":"icon","box":[20,40,84,104],
    "selected":true,"method":"crop","format":"png",
    "transparent":true,"trim":true,
    "difficulty":"完整可见，原图已有透明度",
    "adaptation":{"scale":"contain","anchor":"center"},
    "relations":[],
    "composition":{"role":"icon","code_reproducible":false,
      "decision":"原图图标完整可见，按原始像素提取。","embedded_ui":[]}
  },
  {
    "id":"bg-001","name":"底部山水","source":"source-1",
    "page":"home","category":"background","box":[0,500,400,800],
    "method":"repair","format":"png","occluded":true,
    "prompt":"仅去掉遮挡的按钮与文字，保留原图山形、墨色和构图",
    "adaptation":{"anchor":"bottom-center","scale":"keep-aspect-ratio"},
    "repair_regions":[[20,40,80,70]],
    "composition":{"role":"background","code_reproducible":false,
      "decision":"底部山水加完整上方底色过渡。","embedded_ui":[],"layout_independent":true,
      "background":{"pattern":"bottom-only","strategy":"full-base","canvas_box":[0,500,400,800],
        "scale":"uniform","anchor":"bottom","base_color":"#f9f6f0"}}
  }
]
```

必填 id/name/source/page/category/box/method/format。id/page/category 采用 1–64 位 ASCII 字母数字、下划线、连字符，name 可以中文。selected 默认继承已有选择，新项默认 true；transparent/trim 默认 false。box 为像素 `[left,top,right,bottom]`。

可选：size `[宽,高]`（trim 后尺寸且不变形）；input_file（reuse/vector 必须是真实本地绝对路径）；remove_background `{"color":"#ffffff","tolerance":0}`；nine_slice `[左,上,右,下]`；occluded；prompt；relations；adaptation；difficulty。

新增保真处理：`matte_background:"#fefbf7"` 仅用于已知纯色背景小图标的 alpha 恢复；`repair_regions` 限定局部修复，格式见 fidelity-rules.md。修复不能同时 trim、改目标尺寸、去背景或 alpha 恢复。工具原生画布依 [canvas-mapping.md](canvas-mapping.md) 回映后再合成遮罩，最终画布严格等于源框。

处理方法：crop 原图裁切；reuse 复用 input_file；vector 导入可核对的 SVG；repair/generate 经后端修复/生成。不自动把 raster 转成 SVG。遮挡素材不能用 crop。

新分析清单须提供 `composition` 决策，字段及背景拓扑见 [composition-rules.md](composition-rules.md)。可用 `fidelity_regions` 声明可见图案保留区及纯底色检查区，见 [quality-loop.md](quality-loop.md)。旧清单缺少分析字段仍可读取，不能据此宣称检查通过。

背景、插画、纹理的位图清单还必须有 `composition.surface`，包含源图容器观察、四角修复区（存在圆角时）、原图边缘保留区；完整例子及约束见 [surface-contract.md](surface-contract.md)。本页简化示例只展示字段用法，装饰位图须补齐该契约才可执行。没有契约的旧项目不会自动升级为合格结果。

无图案融合可用 `background_join:{"start_y":194,"transition":96,"base_color":"#f9f6f0","pattern_free":true}`，从 start_y 行至末尾延续纯色；或 `edge_blend:[{"edge":"top","pixels":24,"color":"#fdfaf4","pattern_free":true}]`，将指定边缘的纯色带与底色衔接。二者的改动区须包括在 repair_regions 中，参数在清单阶段检查；只能在视觉分析确认无图案后使用。

## 原生结果、审核与恢复

用户在最终效果展示后明确选择导出时：`export-reviewed --summary <已展示目录>/report.json --user-text USER_TEXT --message-ref MESSAGE_REF`。此路径绑定完整展示快照与哈希，导出每项展示的最优候选（包括历史版本），保留内部视觉记录；质量、技术检查结论和次数不再拦截，不再次确认。只拒绝缺失/被改动的展示文件或越出项目的路径。输出独立素材、预览、来源参考、清单、处理记录、授权与校验值及 ZIP。无此明确选择时不得调用，普通 accept/deliver 规则不变。

内部 best.json 仍填写 failure_summary；失败 SVG 还需 raster_fallback:{"decision":"unavailable","reason":"具体原因"}，可提取 PNG 时先执行已授权的格式回退。质量状态及诊断原因只存 report.json，不显示在最终预览与 display.md；最终仅询问用户修改还是导出。

已确认的白色前景标记可用 matte_preserve_regions:[[left,top,right,bottom]] 在 alpha 恢复后保留原图像素；必须完全位于前景内，不能覆盖外围纸底。与 matte_background 一同使用，检查白色徽记、封闭孔洞和对比底色上的边缘。

图标分析模板增加 icon_policy，字段见 [icon-format.md](icon-format.md)；缺少可核对性证据不能通过 analysis-check。图标 visual-review 的 icon_fidelity 必须填写 source_features、output_features、remaining_differences、features（七类源图/输出特征及差异）与 independent_review（第二遍复核）。SVG 的 render-preview 自动生成 quality.source_comparison，审核须实际查看并填写其 comparison_sha256。缺少、被改动或不匹配的证据、分项或复核差异都不能判 passed；完整结构见 icon-format.md。明确 export-reviewed 不以这些质量结论阻断导出。

文字去除使用 `{"box":[...],"blend":"boundary"}` 时，整个 box 是经分析的修复范围；执行器按周围原图匹配补片底色，保存 repair-blend.json 与质量证据。修改既有遮罩须重新分析和清单批准，不能以 reprocess-candidates 偷换规格。

```text
python studio.py --project output/demo fulfill --asset bg-001 --file /absolute/generated.png --reference-sha256 CROP_SHA
python studio.py --project output/demo inspect
python studio.py --project output/demo repair-queue
python studio.py --project output/demo visual-review --asset bg-001 --verdict passed --note "与原图逐项核对通过" --checks-file checks.json
python studio.py --project output/demo accept --assets icon-001 bg-001 --user-text "导出全部" --message-ref MESSAGE_REF
python studio.py --project output/demo revise --assets bg-001 --reason "保留原图轮廓" --user-text USER_FEEDBACK --message-ref MESSAGE_REF
python studio.py --project output/demo process
python studio.py --project output/demo restore --asset bg-001 --version 1 --user-text USER_REQUEST --message-ref MESSAGE_REF
python studio.py --project output/demo reject --assets bg-001 --reason "未满足轮廓要求" --user-text USER_FEEDBACK --message-ref MESSAGE_REF
python studio.py --project output/demo accept --assets bg-001 --accept-differences --user-text USER_APPROVAL --message-ref MESSAGE_REF
python studio.py --project output/demo failed-preview --file best.json
python studio.py --project output/demo review-summary --file best.json
python studio.py --project output/demo reprocess-candidates --assets bg-001 --user-text "应用修改后的规则复验已有候选" --message-ref MESSAGE_REF
python studio.py --project output/demo deliver
```

以上是独立用法示例，不能在同一轮自动模拟全部用户审核。确认必须以真实用户意见为依据；内部修复与复检无需用户逐项继续。visual-review 不修改原始自动 status，也不代表用户验收；其完整 passed 记录可通过 unknown 的质量门禁。accept-differences 只记录用户意见，不能绕过技术或视觉检查失败。

每阶段最多 2 次（含首次），不能用 provider 配置加大上限。SVG 图标两版均完成有效原图对比仍有保真差异时，允许额外的 PNG 阶段两次。重新 analysis-check 后，manifest 将同一源框、尺寸和选择的 SVG 改为 format=png、method=crop、transparent=true、trim=true，并删除 input_file；仅此回退自动继承原选择授权，无需 revise 或再次 approve-manifest。png_fallback 保留 SVG 两次计数、版本和周期，新的 PNG 周期从零计数，不能反复回退。第 2 次仍失败才可 failed-preview；best.json 为 `[{"id":"bg-001","attempt":1,"reason":"比较两版后选择第一版的具体理由及仍有的问题"}]`，覆盖本轮全部失败项。生成带未通过标记的诊断图和报告，不修改版本；用户明确导出后可通过 export-reviewed 导出已展示快照。即使文件因画布错误未能导入版本，可读原始候选仍保存在 candidates 中。缺少 SVG 真实渲染或无可读图片时报告缺口。

process 会保存每项成功结果，失败不影响其他项；恢复执行直接重跑。修改文件/清单先 revise 对应 ID，再更新完整清单并重新审核；单纯修复输出不改变清单规格时直接 process。

`review-summary --file best.json` 在本轮检查结束后汇总全部选中项，失败项需完成两次并指定最优选择；全通过时不传 --file。输出 icons.png（来自真实 SVG 渲染）、icons.svg、rasters.png、report.json、display.md；不存在的格式不生成。返回须内嵌 display.md 中每张图一次，并给 SVG 链接，不能只返回路径。汇总不改变质量、版本、次数或导出许可。

`reprocess-candidates` 用于用户要求修改规则后的复验：核对当前轮原始候选哈希，仅在处理代码改变时重新执行确定性处理；保留生成次数和候选数，不调用提供者，不重用旧视觉结论。补角区生成独立排除遮罩和内容对比图，整图仍查边缘质量。

`inspect` 生成内部检查图；`repair-queue` 返回修复待办。`preview` 只在全部选中素材通过后生成正式审核预览；SVG 必须先 `render-preview` 登记真实渲染及哈希。`status` 只读。CLI 输出 JSON，错误退出码 2；不成功时不要宣称完成。

严格复刻的截图对比使用独立工具 `scripts/compare_render.py`，命令和门槛配置见 [render-validation.md](render-validation.md)。工具只比较真实截图，不自行生成页面或从截图伪造页面。

## 产物

```text
project.json                     当前状态、规格、各版本质量与事件
analysis/                        三轮分析方案、检查证据及标记报告
candidates/                      本轮所有实际候选，含未能合成的失败图片
sources/                         原始文件与归一化设计基准
history/manifest-NNNN.json        完整清单历史快照
previews/manifest-NNNN/           每项裁切、原图标号、总览及清单
inspections/results/             仅内部使用的候选与检查图
previews/results/                全部通过后才生成的用户审核预览
previews/failed-*/                两次失败后的带标记反馈，禁止用于交付
requests/                        原生/API 后端统一任务描述
versions/<id>/vNNNN/             不覆盖的版本、九宫格及哈希
deliveries/delivery-*/            页面/类别/素材ID/独立文件
deliveries/delivery-*.zip         已验证 ZIP
```

交付清单和报告是经过筛选的公开字段，不复制整个项目状态，不打包请求、provider 配置或密钥。每个版本做 SHA-256，交付前再次检查；ZIP 做 CRC 和内容哈希验证。

图标默认 SVG。PNG/WebP 图标的 analysis 证据须提供 icon_policy.svg_trial：当前 source、reference_box、verdict="issues"、SVG 和真实渲染的绝对路径及 SHA-256、源图/输出观察和非空差异记录；字段见 [icon-format.md](icon-format.md)。旧 passed 记录也会在执行前按现行规则复核。页面底图的 canvas_box 须匹配已确认的完整页面 scope，不能同时将裁框与画布缩成顶部。
