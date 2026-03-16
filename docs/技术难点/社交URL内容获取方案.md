# 社交 URL 内容获取方案

截至 2026-03-16，对现有方案和公开资料复核后的结论是：TripNexus 现有的 `requests + BeautifulSoup` 自动抓取链路，适合作为“开放网页正文提取”的基础能力，但不应继续把它视为小红书、微博、知乎这类平台的主方案。对这些平台，真正稳定的主方案应该是“授权导入/用户辅助导入优先，自动抓取受限可用，浏览器渲染只是兜底而不是银弹”。

## 背景

TripNexus 的差异化价值不在 OTA 的结构化 POI，而在“非结构化旅行内容”：
- 游记、避坑帖、评论情绪、KOL 经验
- 用户收藏的私域攻略（URL / 手动粘贴 / OCR / 截图）

现有知识库链路已经具备基本闭环：
- `POST /api/knowledge/bases/{knowledge_base_id}/ingest/url`
- 支持 `mode=auto/manual`
- 支持 `ingest_status`、`ingest_error_code`
- 导入结果可在 `/api/knowledge/bases/{knowledge_base_id}/sources` 查询

## 当前实现现状

当前代码里，`src/api/app.py` 的 `ingest_knowledge_url` 在 `mode=auto` 时调用 `ContentCrawler(max_workers=1)` 对单 URL 抓取；抓取成功则写入知识库并标记 `ingest_status=parsed`；抓取失败但用户提供 `manual_text` 或 `ocr_text` 时，降级为 `ingest_status=fallback`。

`src/rag/network/crawler.py` 当前使用 `requests + BeautifulSoup`，通过删除 `script/style/nav/footer/header/iframe/noscript` 等标签后，抽取 `p/h1-h5/li` 文本拼接正文。这套方案对普通博客、资讯站、静态攻略页是可用的，但对社交平台页面存在天然短板：
- 动态渲染导致服务端首屏 HTML 没有正文
- 登录墙、风险验证、短链跳转导致拿不到最终内容
- 评论区、推荐区、登录提示容易混入正文
- 即使用 Playwright 能渲染 DOM，也不等于能稳定越过平台风控

## Review 结论

原方案的主要问题不在“方向错误”，而在“难度判断偏乐观”：

1. 把“平台适配 extractor”视为主要增量手段，低估了平台页面结构、登录态、风控和合规边界的变化频率。
2. 把 Playwright 当作 L3 兜底是合理的，但如果把它当成社交平台主抓取方案，会把系统拖进高维护、高失败率和高合规风险。
3. 文档之前缺少“哪些平台天然不适合服务端抓取”的分级判断，因此路线过于统一。

因此，这份方案需要从“按平台攻克抓取”优化为“按内容获取方式分层”，即：
- 官方/授权接口优先
- 公开页面自动提取作为有限能力
- 用户辅助导入作为社交平台主路径
- 浏览器渲染只用于少量公开页面补救，不承担核心成功率

## 平台研究：小红书、微博、知乎为什么难

### 小红书

难点判断：
- 小红书公开页面大量依赖前端渲染，服务端首包未必包含可直接消费的正文。
- 实际传播中常见 `xhslink.com` 短链，需要先解跳到最终页面。
- 图文内容里常有“图片里的文字”，即使拿到正文 HTML 也经常缺失关键信息。
- 官方开放平台公开文档展示的 API 类别主要是通用数据、订单、库存、商品等开放能力，并要求开发者先申请 `App Key` / `App Secret`。从公开 API 能力目录看，它并不是为“公开笔记正文抓取”设计的。

对实现的影响：
- 不能假设存在稳定、官方支持的“笔记正文 API”可直接接入。
- 不能把“小红书 URL 自动抓取成功率提升”作为阶段性必达目标。
- 对小红书更合理的主路径是：用户粘贴文本、上传截图 OCR、后续再扩展浏览器侧授权导入。

### 微博

难点判断：
- 官方 `robots.txt` 当前对 `User-agent: *` 明确是 `Disallow: /`，这意味着通用爬虫默认不应抓取。
- 微博开放平台存在，但其核心定位是开放平台接入、授权登录、SDK、分享等能力，不等价于“任意公开微博正文/评论可稳定抓取”。
- 微博内容强依赖登录态、时间线上下文、折叠内容、评论分页和风控策略，抓“帖子正文”只是最简单的一层，真正对旅行场景有价值的评论和回复更难。

对实现的影响：
- 微博不应进入“自动抓取主支持平台”名单。
- 服务端自动抓取只适合作为 best effort，失败后必须立即提示用户切换手动文本/OCR。
- 即便做 Playwright，也只能提升极少数公开页面的命中率，不能承诺稳定抓取。

### 知乎

难点判断：
- 本次调研中直接访问 `https://www.zhihu.com/robots.txt` 会跳到 `account/unhuman` 风险验证页，说明平台对自动访问本身就有较强安全校验。
- 知乎官方站点文档同时展示了 Cookie、脚本、标签等浏览器侧机制，页面依赖前端逻辑较重。
- 知乎官方帮助中心展示了会员体系（如盐选会员），这意味着不能假设所有目标内容都属于可自由导入的公开正文。
- 问答页、文章页、评论区、展开全文、折叠回答的结构差异较大，抽取规则长期维护成本高。

对实现的影响：
- 知乎可以保留“公开页面 best effort 自动提取”，但不应作为高承诺平台。
- 对知乎更现实的方向是：抽取题目、回答摘要、公开首屏正文；如果质量不足则转用户辅助导入。

## 平台分级建议

不应继续把所有 URL 当成同一类资源处理。建议分成三档：

| 平台类型 | 代表 | 主方案 | 目标 |
| --- | --- | --- | --- |
| 低风险开放网页 | 博客、媒体、普通攻略站 | `requests + bs4` 自动提取 | 高成功率、低维护 |
| 中风险半开放网页 | 知乎公开问答、部分论坛页 | 自动提取 + 质量门禁 + 少量浏览器兜底 | 有条件支持 |
| 高风险社交平台 | 小红书、微博 | 用户辅助导入优先，自动抓取仅 best effort | 降低失败和风控成本 |

这个分级比“平台适配 extractor 越做越多”更符合真实约束。

## 优化后的方案

## 一、产品策略调整

把“社交 URL 导入”重新定义为三种能力，而不是单一抓取能力：

1. 链接识别
   - 识别平台、短链、风险等级
   - 告诉用户这个链接是“自动解析优先”还是“建议手动导入”

2. 文本获取
   - 开放网页：自动抓取正文
   - 高风险社交平台：优先让用户提供文本、截图、OCR 结果
   - 后续可扩展“浏览器插件/书签脚本”在用户本地浏览器中做授权导入

3. 内容结构化
   - 不要求导入时就拿到完美正文
   - 重点是把“标题、正文、标签、作者、来源 URL、时间、图片 OCR 文本”变成可检索文档

这能把目标从“攻破所有平台抓取”转成“稳定拿到可用旅行内容”。

## 二、分层获取架构

### L0：URL 预处理层

新增 `url_preprocessor.py`（建议放在 `src/rag/network/` 下），职责：

#### 1. URL 规范化
- 去除追踪参数：`utm_source`、`utm_medium`、`utm_campaign`、`utm_content`、`utm_term`、`fbclid`、`gclid`、`spm`、`share_token`、`share_source`
- 去除 fragment（`#` 后内容），除非平台依赖 hash 路由（如知乎专栏）
- scheme 统一为 `https`
- 输出 `normalized_url`

#### 2. 短链解跳
- 对已知短链域名（`xhslink.com`、`b23.tv`、`t.cn`、`dwz.cn`）执行 HEAD 请求跟踪 3xx 重定向
- 最多跟踪 5 次重定向，超过则标记 `resolved_url = None`，`ingest_error_code = URL_RESOLVE_LOOP`
- HEAD 请求超时 5 秒，失败则降级使用原始 URL
- 输出 `resolved_url`（解跳后的最终 URL）

#### 3. 平台识别
- 基于 `resolved_url`（优先）或 `normalized_url` 的 hostname 匹配：

| hostname 关键词 | `source_platform` |
| --- | --- |
| `xiaohongshu.com`、`xhslink.com` | `xiaohongshu` |
| `weibo.com`、`weibo.cn`、`t.cn`（微博短链） | `weibo` |
| `zhihu.com`、`zhuanlan.zhihu.com` | `zhihu` |
| `bilibili.com`、`b23.tv` | `bilibili` |
| 其他 | `unknown` |

#### 4. 风险等级识别
- 基于 `source_platform` 静态映射：

| `source_platform` | `source_risk_level` | 默认建议 |
| --- | --- | --- |
| `unknown` | `low` | 自动解析优先 |
| `bilibili` | `medium` | 自动解析 + 质量门禁 |
| `zhihu` | `medium` | 自动解析 + 质量门禁 |
| `xiaohongshu` | `high` | 建议手动导入 |
| `weibo` | `high` | 建议手动导入 |

新增输出字段：
- `normalized_url`
- `resolved_url`
- `source_platform`
- `source_risk_level`：`low/medium/high`

### L1：开放网页自动提取层

继续使用当前 `requests + BeautifulSoup`，但只把它定位为开放网页正文提取能力。

应补齐：
- 元信息提取：`title`、`meta description`、`og:title`、`og:description`、`og:image`
- 正文密度打分（见下方质量门禁规则）
- 登录提示、广告、推荐流关键词过滤

如果得分不足，则不要入库，直接判定对应错误码（见"入库前质量门禁"章节）。

### L2：有限平台适配层

平台适配仍然有价值，但只能做“有限适配”，不能作为总方案核心。

建议仅做：
- 最小化元信息提取
- 公开首屏正文提取
- 页面标题、作者、发布时间、话题标签

不建议承诺：
- 评论区完整抓取
- 多页全文抓取
- 登录后内容抓取
- 会员内容抓取

原因很直接：这些部分通常最依赖登录态、风控和持续维护。

### L3：浏览器渲染兜底层

Playwright 只在这些场景启用：
- 页面是公开的
- L1/L2 拿不到正文
- 用户明确发起导入操作
- 单链接同步耗时在可接受范围内

Playwright 的作用应明确写死为：
- 弥补 CSR/懒加载带来的首屏无正文
- 不负责绕过登录
- 不负责绕过验证码/风控
- 不负责抓取会员内容

这是本次 review 后最需要修正的点。

## 三、社交平台主路径改为“用户辅助导入”

对于小红书、微博、知乎，建议把主成功路径从“服务端抓取”改成“用户辅助导入”。

推荐顺序：

1. 手动粘贴文本
   - 复制正文、评论、避坑点
   - 最稳定、最合规、最省维护

2. 截图 OCR
   - 适合小红书图片文字、微博长图、知乎展开页

3. 后续扩展浏览器侧授权导入
   - 浏览器插件/书签脚本在用户当前已打开页面中提取可见 DOM
   - 由用户主动触发，把内容发给 TripNexus
   - 这比服务端集中抓取更稳定，也更符合“用户自带访问上下文”的场景

这个调整会直接改善三件事：
- 失败率
- 平台风控风险
- 维护成本

## 四、入库前质量门禁

在 `ingest_knowledge_url` 入库前增加 `validate_content_quality(content_text, metadata)` 函数，建议放在 `src/rag/network/content_validator.py`。

### 质量打分规则

`quality_score` 为 0-100 的综合分，入库阈值建议 `>= 40`：

| 检查项 | 权重 | 判定规则 | 触发错误码 |
| --- | --- | --- | --- |
| 最小字符数 | - | `< 80` 字符直接判定为空 | `AUTO_PARSE_EMPTY` |
| 有效文本密度 | 30% | `有效文本字符数 / 原始 HTML 字符数`，阈值 `>= 0.15` | `AUTO_PARSE_LOW_QUALITY` |
| 中文内容比例 | 20% | 中文字符占比，旅行内容预期 `>= 0.3` | `AUTO_PARSE_LOW_QUALITY` |
| 重复段比例 | 20% | 以句号分段后，重复段落占比 `< 0.4` | `AUTO_PARSE_LOW_QUALITY` |
| 噪声模板命中 | 15% | 命中以下关键词数 `< 3` | 见下方噪声判定 |
| 段落数量 | 15% | 有效段落（`>= 20` 字符）数量 `>= 2` | `AUTO_PARSE_LOW_QUALITY` |

### 噪声模板关键词表

以下关键词用于判定页面是否被平台拦截或内容不可用：

| 关键词 | 对应错误码 |
| --- | --- |
| `登录后查看`、`请登录`、`登录即可`、`sign in`、`log in to` | `AUTO_PARSE_LOGIN_REQUIRED` |
| `下载 App 查看`、`打开 App`、`在 App 内打开` | `AUTO_PARSE_BLOCKED` |
| `安全验证`、`验证码`、`人机验证`、`unhuman`、`captcha` | `AUTO_PARSE_RISK_VERIFICATION` |
| `会员专享`、`盐选会员`、`付费内容`、`VIP 专属` | `AUTO_PARSE_PAYWALLED` |
| `展开更多`、`查看完整内容`、`点击展开` | 不单独触发，但计入噪声分 |

### 去重判定

对同一 `knowledge_base_id` 下相同 `resolved_url`（或 `normalized_url`）的历史记录，如果已存在 `ingest_status=parsed` 的条目且内容相似度 > 80%（基于前 500 字符的 Jaccard 相似度），则判定为重复，返回 `AUTO_PARSE_DUPLICATED`。

### 错误码完整定义

| 错误码 | 含义 | 触发条件 |
| --- | --- | --- |
| `AUTO_PARSE_EMPTY` | 自动解析无内容 | 有效文本 < 80 字符 |
| `AUTO_PARSE_LOW_QUALITY` | 自动解析质量不足 | `quality_score < 40` |
| `AUTO_PARSE_DUPLICATED` | 重复内容 | 同 URL 已有高相似度条目 |
| `AUTO_PARSE_BLOCKED` | 平台阻断 | 命中”下载 App”等关键词 |
| `AUTO_PARSE_LOGIN_REQUIRED` | 需要登录 | 命中登录提示关键词 |
| `AUTO_PARSE_RISK_VERIFICATION` | 风控验证 | 命中验证码/人机验证关键词 |
| `AUTO_PARSE_PAYWALLED` | 付费内容 | 命中会员/付费关键词 |
| `URL_RESOLVE_LOOP` | 短链解跳循环 | 重定向次数 > 5 |
| `URL_RESOLVE_TIMEOUT` | 短链解跳超时 | HEAD 请求超时 |
| `VECTOR_STORE_INSERT_FAILED` | 向量库写入失败 | 内容提取成功但入库异常 |

## 五、协议与字段优化

沿用现有 `success + ingest_status + metadata` 契约，但补充更强的可解释性：

建议新增 metadata 字段：
- `normalized_url`
- `resolved_url`
- `extractor_layer`：`l1_html/l2_platform/l3_browser/manual/ocr`
- `source_risk_level`
- `quality_score`
- `content_lang`
- `requires_user_assist`：`true/false`
- `failure_reason`

状态保持不变：
- 自动成功：`success=true, ingest_status=parsed`
- 自动失败后人工补全：`success=true, ingest_status=fallback`
- 全部失败：`success=false, ingest_status=failed`

## 六、前端交互优化

前端不应只告诉用户“失败了”，而应该告诉用户“为什么失败”和“下一步该怎么做”。

### 平台识别后的前置引导

当用户输入 URL 后、点击”导入”之前，前端根据 L0 预处理返回的 `source_platform` 和 `source_risk_level` 展示引导：

| `source_risk_level` | 引导文案 | 交互 |
| --- | --- | --- |
| `low` | “该链接支持自动解析，点击导入即可” | 默认 `mode=auto` |
| `medium` | “该平台内容可能无法完整获取，建议同时准备文本备份” | 默认 `mode=auto`，展示手动输入区 |
| `high` | “该平台内容建议手动导入：复制正文粘贴，或上传截图” | 默认 `mode=manual`，`mode=auto` 标注”尝试自动解析（成功率较低）” |

### 失败后的平台级引导文案

| `source_platform` | `ingest_error_code` | 引导文案 |
| --- | --- | --- |
| `xiaohongshu` | 任意失败码 | “小红书内容建议复制笔记正文粘贴，或截图后上传（支持 OCR 识别图中文字）” |
| `weibo` | 任意失败码 | “微博内容建议复制正文粘贴，长微博可截图上传” |
| `zhihu` | 任意失败码 | “知乎内容建议复制回答正文粘贴，或截图上传” |
| `bilibili` | 任意失败码 | “B站内容建议复制视频简介或评论粘贴” |
| 任意 | `AUTO_PARSE_LOGIN_REQUIRED` | “该页面需要登录才能查看，请复制可见内容粘贴导入” |
| 任意 | `AUTO_PARSE_RISK_VERIFICATION` | “该页面触发了安全验证，请复制内容后手动导入” |
| 任意 | `AUTO_PARSE_PAYWALLED` | “该页面为付费内容，请复制已获取的部分粘贴导入” |

### 来源列表展示字段

在知识条目列表中展示：
- `source_platform`（平台标签，带图标）
- `source_risk_level`（颜色标识：绿/黄/红）
- `ingest_status`（状态标签）
- `ingest_error_code`（失败时展示，hover 显示完整含义）
- `extractor_layer`（调试模式下展示）

这样用户会理解这是”平台限制”，不是产品故障。

## 七、实施优先级

### Phase 1

先做低风险、高收益改造：
- 增加 URL 风险分级
- 增加质量门禁
- 增加错误码与 metadata
- 前端增加失败原因和平台引导文案

这个阶段不引入 Playwright，不做复杂平台适配。

### Phase 2

补有限平台适配：
- 小红书：标题、首屏说明、标签、图片 OCR 流程联动
- 微博：正文摘要提取、长图 OCR 联动
- 知乎：题目、回答摘要、首屏正文提取

目标不是“稳定抓全”，而是“提高 best effort 命中率”。

### Phase 3

只在证明确实有收益后，再加：
- Playwright 公开页兜底
- 浏览器插件/书签脚本授权导入

如果没有 Phase 1 的平台指标，就不应该直接上 Phase 3。

## 最终建议

这类平台真正困难的不是“HTML 解析难一点”，而是：
- 官方接口并不为公开内容抓取设计
- 自动访问容易触发风控
- 页面高度依赖前端与登录态
- 关键旅行信息常常存在于图片、评论、展开内容里

所以最优解不是继续把服务端抓取做得越来越重，而是：
- 对开放网页，把自动抓取做到稳定
- 对高风险社交平台，把用户辅助导入做到顺滑
- 用更明确的状态机、错误码和平台提示把失败转化为可恢复流程

这比“继续堆 selector 和 Playwright”更符合 TripNexus 当前阶段的成本和收益。

## 参考资料

- 小红书开放平台 `App Key/App Secret` 申请说明：https://miniapp.xiaohongshu.com/doc/DC686827
- 小红书开放平台 API 分类页：https://miniapp.xiaohongshu.com/doc/DC686828
- 微博 robots.txt：https://weibo.com/robots.txt
- 微博开放平台首页：https://open.weibo.com/
- 知乎 robots.txt（当前访问会跳转到安全验证页）：https://www.zhihu.com/robots.txt
- 知乎 Cookie 说明：https://www.zhihu.com/cookie/detail
- 知乎用户服务中心（含会员服务入口）：https://www.zhihu.com/contact
