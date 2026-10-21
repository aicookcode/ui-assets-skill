# 原图元素盘点

先盘点原图，再决定提取清单。按页从上到下检查背景、内容区、卡片、按钮、入口和导航；特别检查加号、刷新、返回、展开、状态标记及重复箭头。不能只扫描大块插画，也不能从已有清单反推“原图没有其他元素”。

每个可独立使用的图形元素记录一条原图位置及去向：

- `extract`：独立提取，指定 `asset_id`。
- `reuse`：重复使用同一素材，仍逐个记录使用位置和共用的 `asset_id`，说明形状、状态和样式为何可共用。共享目标须有一条 `extract` 主记录。
- `code`：由代码实现，写出 `implementation`，例如两条垂直线组成加号；不是漏项。不能为减少文件数，把复杂图标笼统归为代码实现。
- `out-of-scope`：位于用户范围之外，写明原因；范围内元素不可使用此项。用户未选导出与范围外不同，未选项仍保留盘点与提取关系，在素材清单设 `selected=false`。

装饰画内不可分离的小花瓣、纹理不逐个盘点，归入所属插画。背景含完整底色和必要过渡。重复图标的出现次数与最终文件数不同，不要求每次产出固定数量文件。

## 分析证据

`analysis-template` 在逐项素材证据旁返回 `__inventory__`。以下为结构示例，坐标和观察须根据实际原图填写：

```json
{
  "__inventory__": {
    "regions": [
      {"source":"source-1","page":"home","box":[0,0,464,928],
       "observation":"已检查整页背景、顶部状态、诗句、课程、入口和底部导航。"}
    ],
    "elements": [
      {"id":"arrow-first","source":"source-1","page":"home","category":"icon",
       "box":[80,600,90,620],"disposition":"extract","asset_id":"icon-arrow",
       "reason":"首个入口的独立箭头。"},
      {"id":"arrow-second","source":"source-1","page":"home","category":"icon",
       "box":[300,600,310,620],"disposition":"reuse","asset_id":"icon-arrow",
       "reason":"已对比原图，与首个箭头的形状及样式相同。"},
      {"id":"energy-plus","source":"source-1","page":"home","category":"icon",
       "box":[420,220,432,232],"disposition":"code",
       "implementation":"两条垂直的等宽线组成加号，圆形底托由 CSS 实现。",
       "reason":"规则几何可由代码准确实现。"}
    ],
    "second_pass":{"verdict":"passed","note":"独立重扫原图，已核对小按钮、重复图标与导航元素的去向。"}
  }
}
```

`regions` 对每个已确认 scope 区域记录同一 source、page（scope 未指定则省略）和完整 box，以及实际观察；不是只填单个素材裁框。`elements` 用原图绝对坐标，category 与对应素材一致。素材级分析证据继续放在各 asset ID 下。

`analysis-check` 校验范围扫描记录、元素 ID 唯一、坐标有效、范围归类、代码实现说明、提取与复用映射，以及所有选中素材均有提取记录。`extract` 源图/页面须一致且元素位于素材框内；复用允许跨页面位置，仍须位于当前授权范围。缺失或错误记录不能通过用户允许视觉疑点的命令豁免。

清单展示时同时列出原图元素的去向，区分“出现次数”和“独立文件数”；代码实现项及复用位置也须展示。再次看原图，独立于清单执行第二遍扫描。未解决的漏项疑点写 `issues/unknown`，按既有最多 3 次分析修改循环处理；不能伪填 passed。

代码能检查记录与清单一致，不能证明模型已识别图中所有元素，也不能仅靠文字证明两个图标相同。原图复扫和用户可见的去向表用于发现这类视觉遗漏。
