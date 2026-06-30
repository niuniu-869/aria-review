STABLEsciverse-apiv1.3.0

# Sciverse API 仓库

面向 LLM Agent / RAG 与检索应用的学术检索 REST 接口集，包含 5 个端点：agentic-search、content、resource、meta-catalog、meta-search。

Endpoints

5

端点[POSTagentic-search](https://sciverse.opendatalab.com/docs#sciverse/api/agentic-search)[GETcontent](https://sciverse.opendatalab.com/docs#sciverse/api/content)[GETresource](https://sciverse.opendatalab.com/docs#sciverse/api/resource)[GETmeta-catalog](https://sciverse.opendatalab.com/docs#sciverse/api/meta-catalog)[POSTmeta-search](https://sciverse.opendatalab.com/docs#sciverse/api/meta-search)

POST/agentic-search#agentic-search

## agentic-search 智能检索与片段返回

用自然语言提问，返回最相关的可引用文献段落。

## 概述

agentic-search 面向 LLM Agent 与 RAG 场景。每条结果包含标题、正文片段、doc_id 和页码/位置等来源信息，适合快速找到可引用的上下文；需要读全文时，可用 doc_id 继续调用 content。

## 适用场景

- · RAG 应用：为 LLM 补充含引用的文献证据
- · Agent 工具调用：一屏拿到可回链的片段与原文位置
- · 问答系统：结合文献原文与片段生成带出处的回答

## 请求示例

curlPython复制

```
curl -X POST https://api.sciverse.space/agentic-search \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "graphene battery cycle stability",
    "top_k": 10
  }'
```

## 请求体（JSON）

| 字段        | 类型    | 必填 | 说明                                        |
| ----------- | ------- | ---- | ------------------------------------------- |
| query       | string  | 必填 | 你的检索问题；不能为空。范围 最大 4096 字符 |
| top_k       | integer | 可选 | 返回片段数量。默认 10范围 1–100             |
| sub_queries | integer | 可选 | 查询改写数量，0 表示不改写。默认 0范围 0–4  |

## 响应结构

| 字段                 | 类型    | 说明                                       |
| -------------------- | ------- | ------------------------------------------ |
| hits                 | array   | 命中片段列表。                             |
| hits[].chunk_id      | string  | 片段 ID。                                  |
| hits[].chunk         | string  | 片段文本内容。                             |
| hits[].doc_id        | string  | 所属文献 ID，可传给 /content 读取原文。    |
| hits[].title         | string  | 文献标题。                                 |
| hits[].abstract      | string  | 文献摘要。                                 |
| hits[].score         | float   | 相关度得分。                               |
| hits[].source_type   | string  | pdf / web 等来源类型。                     |
| hits[].offset        | integer | 片段在原文中的字符偏移（Unicode 码点数）。 |
| hits[].page_no       | integer | 原文页码（仅 pdf 类有）。                  |
| hits[].model_name    | string  | 用于打分的模型名。                         |
| hits[].model_version | string  | 模型版本。                                 |

## 响应示例

```
{
  "hits": [
    {
      "chunk_id": "c_8c1f...",
      "chunk": "Graphene-based cathodes exhibit improved cycle stability ...",
      "doc_id": "d_2a91...",
      "title": "Cycle stability of graphene composite cathodes",
      "abstract": "...",
      "score": 0.873,
      "source_type": "pdf",
      "offset": 18432,
      "page_no": 4,
      "model_name": "sciverse-retriever",
      "model_version": "v2.3"
    }
  ]
}
```

## 错误码

| 错误码  | 信息                 | 说明                                      |
| ------- | -------------------- | ----------------------------------------- |
| 400     | INVALID_REQUEST      | 请求参数错误，检查 query / top_k 等取值。 |
| 401     | UNAUTHORIZED         | 鉴权失败，检查 Authorization 请求头。     |
| 429     | RATE_LIMITED         | 触发限流或配额耗尽，等窗口恢复后重试。    |
| 500     | INTERNAL_ERROR       | 服务错误，指数退避重试。                  |
| 502/503 | UPSTREAM_UNAVAILABLE | 服务暂不可用，指数退避重试。              |

## 调用限制

| 限制项     | 值           | 说明 |
| ---------- | ------------ | ---- |
| query 长度 | ≤ 4096 字符  |      |
| top_k 上限 | 100          |      |
| 默认限流   | 60 次 / 分钟 |      |

## 重试建议

- · 建议重试：500 / 502 / 503
- · 不应重试：400 / 401；429 请等窗口恢复

GET/content#content

## content 按 doc_id 读取原文

用 doc_id 分段读取文章全文。

## 概述

content 接口按 doc_id 读取文献全文文本。doc_id 通常来自 agentic-search 或 meta-search，适合详情页展示、引用核对和长文分批加载；支持 offset / limit 分段拉取以适配长上下文场景。

## 适用场景

- · 以 agentic-search 返回的 doc_id 拉取原文打二次摘要
- · 分段读取超长文献以避免超出上下文窗口
- · 根据 next_offset / more 完成多轮流式读取

## 请求示例

curlPython 流式拉取复制

```
curl -G https://api.sciverse.space/content \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  --data-urlencode "doc_id=YOUR_DOC_ID" \
  --data-urlencode "offset=0" \
  --data-urlencode "limit=700"
```

## 请求参数（URL query）

| 字段   | 类型    | 必填 | 说明                                                         |
| ------ | ------- | ---- | ------------------------------------------------------------ |
| doc_id | string  | 必填 | 文献 ID（由 agentic-search / meta-search 返回）。            |
| offset | integer | 可选 | 字符偏移（Unicode 码点数）；未传时返回全文。范围 ≥ 0         |
| limit  | integer | 可选 | 单次最大字符数（Unicode 码点数），默认 700；仅在传入 offset 时生效。默认 700 |

## 响应结构

| 字段           | 类型    | 说明                                 |
| -------------- | ------- | ------------------------------------ |
| text           | string  | 文本内容（Markdown 或纯文本）。      |
| chars_returned | integer | 本次返回的字符数（Unicode 码点数）。 |
| next_offset    | integer | 下一段读取的字符偏移。               |
| more           | bool    | 是否还有更多内容。                   |

## 错误码

| 错误码  | 信息                 | 说明                    |
| ------- | -------------------- | ----------------------- |
| 400     | INVALID_REQUEST      | doc_id 缺失或参数非法。 |
| 401     | UNAUTHORIZED         | 鉴权失败。              |
| 404     | NOT_FOUND            | 文档不存在。            |
| 405     | METHOD_NOT_ALLOWED   | 仅支持 GET。            |
| 429     | RATE_LIMITED         | 触发限流。              |
| 502/503 | UPSTREAM_UNAVAILABLE | 服务暂不可用。          |

## 调用限制

| 限制项 | 值                                                           | 说明 |
| ------ | ------------------------------------------------------------ | ---- |
| offset | 字符偏移（Unicode 码点数）；未传时返回全文                   |      |
| limit  | 单次最大字符数（Unicode 码点数），默认 700；仅在传入 offset 时生效 |      |

## 重试建议

- · 建议重试：502 / 503
- · 不应重试：400 / 401 / 405

GET/resource#resource

## resource 按相对路径下载附件

用相对路径 file_name 下载论文图片和其他二进制附件。

## 概述

resource 接口用于拉取论文插图、实验图、解析图等文献相关二进制附件。file_name 通常来自检索结果、解析结果或正文中的图片路径，只传相对路径，不要传完整 URL；响应为二进制流，带 Content-Type 与 Content-Disposition。

## 请求示例

curl复制

```
curl -G https://api.sciverse.space/resource \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  --data-urlencode "file_name=papers/2025/abcd/fig1.png" \
  -o fig1.png
```

## 请求参数（URL query）

| 字段      | 类型   | 必填 | 说明                                            |
| --------- | ------ | ---- | ----------------------------------------------- |
| file_name | string | 必填 | 资源相对路径；不得包含 \、..，且不得以 / 开头。 |

## 响应结构

响应为二进制流；常见 Content-Type：image/jpeg、image/png、application/pdf 等。

| 字段                | 类型   | 说明                 |
| ------------------- | ------ | -------------------- |
| Content-Type        | header | 附件的 MIME 类型。   |
| Content-Disposition | header | 文件名与下载提示。   |
| X-Request-ID        | header | 请求追踪 ID。        |
| body                | binary | 附件原始二进制内容。 |

## 错误码

| 错误码      | 信息                 | 说明                       |
| ----------- | -------------------- | -------------------------- |
| 400         | INVALID_REQUEST      | file_name 缺失或路径非法。 |
| 401         | UNAUTHORIZED         | 鉴权失败。                 |
| 404         | NOT_FOUND            | 资源不存在。               |
| 429         | RATE_LIMITED         | 限流。                     |
| 500/502/503 | UPSTREAM_UNAVAILABLE | 服务异常。                 |

## 调用限制

| 限制项   | 值                              | 说明 |
| -------- | ------------------------------- | ---- |
| 默认限流 | 60 次 / 分钟                    |      |
| 路径限制 | 不得包含 \、..，且不得以 / 开头 |      |

## 重试建议

- · 建议重试：500 / 502 / 503
- · 不应重试：400 / 401

GET/meta-catalog#meta-catalog

## meta-catalog 查看元数据字段目录

查看 meta-search 支持的字段、筛选排序能力与默认返回列。

## 概述

meta-catalog 返回 meta-search 可用字段、哪些字段可以筛选或排序、默认返回哪些列，以及过滤算子和枚举样本值。适合在搭筛选器、生成查询表单或让 Agent 自动拼 meta-search 请求前调用。

## 请求示例

curl复制

```
curl -G https://api.sciverse.space/meta-catalog \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  --data-urlencode "include_sample_values=true"
```

## 请求参数（URL query）

| 字段                  | 类型 | 必填 | 说明                                                       |
| --------------------- | ---- | ---- | ---------------------------------------------------------- |
| include_sample_values | bool | 可选 | 是否返回枚举字段的样本值；样本值会缓存 24 小时。默认 false |

## 响应结构

| 字段                      | 类型   | 说明                                                         |
| ------------------------- | ------ | ------------------------------------------------------------ |
| fields                    | array  | 字段列表。                                                   |
| fields[].name             | string | 字段名。                                                     |
| fields[].type             | string | 类型：String / Integer / Float / List[...]。                 |
| fields[].filterable       | bool   | 是否可作为 filters 字段。                                    |
| fields[].sortable         | bool   | 是否可排序。                                                 |
| fields[].searchable       | bool   | 是否可被全文检索。                                           |
| fields[].default_returned | bool   | 是否为默认返回字段。                                         |
| fields[].description      | string | 字段说明。                                                   |
| fields[].sample_values    | array  | 枚举样本值，仅在 include_sample_values=true 时返回。         |
| fields[].operators        | array  | 该字段支持的算子；Integer/Float 通常全集，List 类型多为 IN/NIN/CONTAINS，不可过滤字段为空数组。 |
| default_fields            | array  | 默认返回的字段集合。                                         |
| filter_operators          | array  | 全局支持的过滤算子：EQ / NE / GT / GTE / LT / LTE / IN / NIN / CONTAINS。 |

## 错误码

| 错误码 | 信息                                          | 说明                             |
| ------ | --------------------------------------------- | -------------------------------- |
| 401    | UNAUTHORIZED                                  | 鉴权失败。                       |
| 429    | RATE_LIMITED                                  | 限流。                           |
| 502    | UPSTREAM_UNAVAILABLE                          | 服务异常。                       |
| 503    | METADATA_GRPC_NOT_CONFIGURED / UPSTREAM_ERROR | 元数据服务暂不可用，请稍后重试。 |

## 调用限制

| 限制项     | 值                                        | 说明 |
| ---------- | ----------------------------------------- | ---- |
| 样本值默认 | 默认不拉取，需 include_sample_values=true |      |
| doc_id     | 始终可见                                  |      |
| 默认限流   | 60 次 / 分钟                              |      |

## 重试建议

- · 建议重试：502 / 503 / 504
- · 不应重试：401

POST/meta-search#meta-search

## meta-search 按字段过滤与排序检索元数据

按年份、期刊、DOI、语言等结构化条件筛选论文书目信息。

## 概述

meta-search 返回标题、摘要、作者、发表年份等元数据，适合做论文列表、筛选和导出，不返回段落正文。你可以不传 query 仅以 filters / sort 精准检索；也可以传入 query 做全文模糊检索（此时不能同时使用 sort，但可以用 freshness_boost 偏向新文献）。

## 适用场景

- · 按学科 / 年份 / 期刊 / 语言等字段筛选文献列表
- · 结合 meta-catalog 动态生成 UI 过滤器
- · 需要跨页检索的场景，使用 cursor 翻页

## 请求示例

curlPython 游标翻页复制

```
curl -X POST https://api.sciverse.space/meta-search \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "graphene battery cycle stability",
    "filters": [
      {"field": "publication_published_year", "operator": "FILTER_OP_GTE", "value": 2022}
    ],
    "fields": ["title", "doi", "publication_published_year", "publication_venue_name_unified"],
    "page": 1,
    "page_size": 10
  }'
```

## 请求体（JSON）

FilterItem：{ field, operator?, value }，operator 默认 EQ，可选值 FILTER_OP_EQ/NE/GT/GTE/LT/LTE/IN/NIN/CONTAINS。SortItem：{ field, order }，order 默认 SORT_ORDER_DESC。

| 字段            | 类型              | 必填 | 说明                                                         |
| --------------- | ----------------- | ---- | ------------------------------------------------------------ |
| query           | string            | 可选 | 全文模糊检索词；与 sort 不能同时使用。                       |
| filters         | array<FilterItem> | 可选 | 字段过滤条件列表。                                           |
| sort            | array<SortItem>   | 可选 | 排序字段集合。可排序字段：publication_published_year / publication_published_date / reference_count / citation_count / influential_citation_count / fwci。与 query、freshness_boost 互斥。 |
| fields          | array<string>     | 可选 | 字段投影。doc_id 始终返回。                                  |
| page            | integer           | 可选 | 页码。默认 1范围 ≥ 1                                         |
| page_size       | integer           | 可选 | 每页条数。默认 25范围 1–200                                  |
| cursor          | string            | 可选 | 游标翻页令牌；与 page>1 互斥。                               |
| freshness_boost | enum              | 可选 | 模糊搜索新鲜度加权。MILD：近 10 年加权，适合日常查文献；STRONG：近 3 年加权，适合跟踪研究方向 / 追最新进展。仅 query 非空时生效；与 sort 互斥。底层为 function_score + gauss decay over publication_published_date。默认 NONE范围 NONE / MILD / STRONG |

## 响应结构

| 字段                                     | 类型          | 说明                                                         |
| ---------------------------------------- | ------------- | ------------------------------------------------------------ |
| results                                  | array<object> | 命中记录列表；字段受 fields 参数与 Token 权限影响。          |
| results[].abstract                       | string        | 摘要、简介或内容概述                                         |
| results[].author                         | array<string> | 作者列表                                                     |
| results[].citation_count                 | integer       | 被引次数：该文章被其他文章引用的次数，是衡量文章影响力的重要指标 |
| results[].doc_id                         | string        | 全文 artifact 内容哈希（sha256）。仅在文档存在全文时返回；没有全文的元数据记录无 doc_id 字段。要引用元数据记录本身请用 unique_id；要拉全文走 /content 接口必须用 doc_id。 |
| results[].doi                            | string        | 数字对象唯一标识符，主要用于论文等学术资源定位               |
| results[].fwci                           | float         | 一篇文献的'领域加权引用影响力'Field-Weighted Citation Impact |
| results[].influential_citation_count     | integer       | 高影响力被引次数                                             |
| results[].keywords                       | array<string> | 关键词列表                                                   |
| results[].language                       | string        | 资源语言                                                     |
| results[].metadata_type                  | string        | 元数据来源类型。论文来源取值 paper，图书来源取值 ebook。     |
| results[].publication_published_year     | integer       | 出版/发表年份                                                |
| results[].publication_venue_name_unified | string        | 规范化后的发表载体名称（消除缩写/大小写/标点噪声）。比 publication_venue_name_unified 更适合精确匹配 / 分组聚合；可作为 venue_name 的替代。 |
| results[].reference_count                | integer       | 引用文献数：本文引用了多少篇文献                             |
| results[].title                          | string        | 资源标题 / 题名                                              |
| results[].unique_id                      | string        | 元数据记录的全局唯一 ID。任何记录都有，与是否有全文无关；适合做引用、去重、跨服务关联（默认返回）。 |
| total_count                              | integer       | 命中总数。                                                   |
| page                                     | integer       | 当前页。                                                     |
| page_size                                | integer       | 每页条数。                                                   |
| total_pages                              | integer       | 总页数。                                                     |
| search_time_ms                           | float         | 检索耗时（毫秒）。                                           |
| next_cursor                              | string        | 下一段 cursor（深翻页用）。                                  |

## 响应示例

```
{
  "results": [
    {
      "doc_id": "d_2a91...",
      "title": "Cycle stability of graphene composite cathodes",
      "doi": "10.1234/xyz",
      "language": "en",
      "publication_published_year": 2024,
      "publication_venue_name_unified": "Adv. Energy Mater.",
      "citation_count": 42,
      "fwci": 1.84
    }
  ],
  "total_count": 318,
  "page": 1,
  "page_size": 25,
  "total_pages": 13,
  "search_time_ms": 56.4,
  "next_cursor": "eyJvZmZzZXQiOjI1fQ=="
}
```

## 错误码

| 错误码          | 信息                               | 说明                                             |
| --------------- | ---------------------------------- | ------------------------------------------------ |
| 400             | INVALID_REQUEST / INVALID_ARGUMENT | 参数错误，含 query/sort 冲突、cursor/page 互斥。 |
| 401             | UNAUTHORIZED / UNAUTHENTICATED     | 鉴权失败。                                       |
| 403             | PERMISSION_DENIED                  | 字段无访问权限，调整 fields 或使用其他 Token。   |
| 429             | RATE_LIMITED                       | 限流。                                           |
| 500/502/503/504 | UPSTREAM_UNAVAILABLE               | 服务异常。                                       |

## 调用限制

| 限制项        | 值                                    | 说明 |
| ------------- | ------------------------------------- | ---- |
| page 下限     | ≥ 1                                   |      |
| page_size     | 1–200，默认 25                        |      |
| 浅翻页        | page * page_size ≤ 10000              |      |
| 深翻页        | 使用 cursor；cursor 与 page>1 互斥    |      |
| query 与 sort | 不能同时使用；带 query 时按相关性排序 |      |
| 默认限流      | 60 次 / 分钟                          |      |

## 重试建议

- · 建议重试：502 / 503 / 504
- · 不应重试：400 / 401 / 403；429 请等窗口恢复