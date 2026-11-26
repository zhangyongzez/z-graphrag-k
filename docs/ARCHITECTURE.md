# 架构说明

## 1. 整体分层

```
┌─────────────────────────────────────────────────────────────┐
│  管理页面 (web/index.html + app.js)                          │
│  看板 · 知识库 · 文档上传 · 对话                              │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼─────────────────────────────────┐
│  FastAPI (kgrag.api)                                        │
│  ├── routes_kb.py    知识库 CRUD / 上传 / 文档 / 看板         │
│  ├── routes_chat.py  问答 / 流式输出                          │
│  └── app.py          health / config / 兼容旧接口             │
└───────────────────────────┬─────────────────────────────────┘
                            │
      ┌─────────────────────┼─────────────────────┐
      ▼                     ▼                     ▼
┌───────────┐       ┌─────────────┐       ┌─────────────┐
│ ingest    │       │ retrieval   │       │ kb          │
│ 解析/切分  │       │ 检索/问答    │       │ 注册表/统计  │
└─────┬─────┘       └──────┬──────┘       └──────┬──────┘
      │                    │                     │
      └────────────────────┼─────────────────────┘
                           ▼
      ┌────────────────────┼────────────────────┐
      ▼                    ▼                    ▼
┌───────────┐      ┌─────────────┐      ┌─────────────┐
│ embeddings│      │ vectorstore │      │ llm         │
│ 4 种实现   │      │ 2 种后端     │      │ 4 种实现     │
└─────┬─────┘      └──────┬──────┘      └──────┬──────┘
      │                   │                    │
      ▼                   ▼                    ▼
  bge-m3 /          Milvus Lite /        Claude /
  OpenAI /          Milvus 服务端         OpenAI /
  Ollama /                                Ollama /
  Hash                                    Mock
```

## 2. 可插拔设计

四个维度全部通过环境变量切换，代码零改动：

| 能力 | 环境变量 | 可选值 |
|---|---|---|
| 向量库 | `VECTORSTORE_PROVIDER` | `lite` / `milvus` |
| Embedding | `EMBEDDING_PROVIDER` | `local` / `openai` / `ollama` / `hash` |
| LLM | `LLM_PROVIDER` | `anthropic` / `openai` / `ollama` / `mock` |
| 图库 | `GRAPH_ENABLED` | `true` / `false`（规划中） |

所有实现都遵循同一接口，由 `get_vectorstore()` / `get_embeddings()` /
`get_llm()` 三个工厂统一产出。

## 3. 数据模型

### 3.1 知识库

每个知识库对应 Milvus 中的一个 **collection**，命名 `kb_<12位hex>`。

注册表持久化在 `data/registry.json`：

```json
{
  "version": 1,
  "kbs": [
    {
      "id": "2f576848cfcd",
      "name": "产品手册",
      "description": "验证用",
      "collection": "kb_2f576848cfcd",
      "embedding_dim": 1024,
      "created_at": "2026-11-25T09:19:11+00:00",
      "updated_at": "2026-11-25T09:19:11+00:00"
    }
  ]
}
```

### 3.2 向量块

Milvus collection 的 schema（由 LangChain 自动创建）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pk` | VARCHAR | 主键，`sha1(namespace\|doc_id\|chunk_index)[:32]` |
| `text` | VARCHAR | 切块后的文本 |
| `vector` | FLOAT_VECTOR | 向量，维度由 embedding 决定 |
| `filename` | 动态字段 | 文件名 |
| `source` | 动态字段 | 原始路径 |
| `doc_id` | 动态字段 | 文件标识（路径+大小+mtime） |
| `chunk_index` | 动态字段 | 块序号 |

> **注意**：LangChain 在 `enable_dynamic_field=True` 时会把 metadata
> **拍平**成顶层字段，不是嵌套在 `metadata` 里。查询时直接取 `filename`。

### 3.3 幂等性

主键由「知识库命名空间 + 文档标识 + 块序号」哈希得到，因此：

- 同一文件重复上传 → 主键相同 → **原地覆盖**，不产生重复
- 不同知识库的同名文件 → 命名空间不同 → **互不干扰**

## 4. 检索流程

```
问题
  │
  ├─▶ 向量化 (embedding)
  │        │
  │        ▼
  │   Milvus 相似度检索 (top_k)
  │        │
  │        ▼
  │   组装上下文（带文件名标注）
  │        │
  │        ▼
  └─▶ LLM 生成回答 ──▶ 回答 + 引用来源
```

流式模式（`/api/chat/stream`）用 SSE，事件顺序：

1. `event: sources` — 先推命中的资料
2. `event: delta` — 逐 token 推回答
3. `event: done` — 结束

## 5. 目录结构

```
src/kgrag/
├── config.py           统一配置（pydantic-settings，全部环境变量）
├── logging.py          loguru + 标准 logging 桥接
├── cli.py              命令行入口
├── db.py               SQLite：用户 / 会话 / 权限 / 运行时配置 / 审计
├── settings/           运行时配置（SQLite 覆盖 .env）+ 系统配置页表单结构
├── audit.py            操作审计
├── models.py           模型注册表（LLM + Embedding，含连接信息）
├── conversations.py    会话与消息持久化
├── auth/
│   ├── security.py     scrypt 密码哈希、会话令牌
│   ├── service.py      登录 / 会话 / 用户管理
│   └── ad.py           LDAP 认证
├── embeddings/
│   └── base.py         local / openai / ollama / hash 四种实现
├── llm/
│   └── base.py         anthropic / openai / ollama / mock 四种实现
├── vectorstore/
│   └── store.py        Milvus Lite / 服务端，按 collection 隔离
├── ingest/
│   ├── loader.py       多格式解析 + Markdown 结构切分
│   └── pipeline.py     入库流水线（幂等）
├── retrieval/
│   └── rag.py          检索 + 加权融合 + 上下文组装 + 回答
├── kb/
│   ├── registry.py     知识库注册表（JSON 持久化）
│   ├── collections.py  集合统计 / 文档删除（共享 Milvus 客户端）
│   └── permissions.py  归属 / 授权 / 公共 三层权限
├── api/
│   ├── app.py          应用入口（lifespan 初始化 DB + 管理员）
│   ├── deps.py         当前用户 / 管理员依赖
│   ├── routes_auth.py  登录 / 用户管理
│   ├── routes_admin.py 配置 / 模型管理 / 系统看板 / 审计
│   ├── routes_kb.py    知识库接口（带权限校验）
│   └── routes_chat.py  会话 / 对话接口
└── web/
    ├── index.html      管理页面（登录 + 7 个页面）
    └── assets/
        ├── style.css
        └── app.js
```

## 6. 权限模型

### 6.1 角色

| 角色 | 来源 | 权限 |
|---|---|---|
| 管理员 | 环境变量 `ADMIN_USERNAME` / `ADMIN_PASSWORD`，**唯一** | 全部；可查看所有知识库、配置、用户 |
| 普通用户 | 管理员创建（本地）或 AD 登录自动注册 | 自己的知识库 + 被授权的 + 公共的 |

### 6.2 知识库可见性

三张表决定一个用户能否看到某个知识库：

```
kb_owner(kb_id, owner_id, is_public)   -- 归属 + 是否公共
kb_grant(kb_id, user_id, can_write)    -- 点对点授权
users(role)                            -- 管理员绕过一切
```

判定顺序（`kb/permissions.py`）：

1. 管理员 → 全部可见、可读写
2. 是 owner → 可读可写可删
3. `is_public=1` → 所有人可读
4. 在 `kb_grant` 中 → 可读（`can_write=1` 时可写）
5. 否则不可见

列表接口用 `visible_kb_ids()` 一次性取集合过滤，避免 N+1。

### 6.3 管理员唯一性

管理员账号**只**由环境变量决定，`ensure_admin()` 在启动时：

- 数据库里没有管理员 → 用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 创建
- 已有管理员但用户名变了 → 同步用户名
- 页面上不允许删除或降级管理员

## 7. 模型与配置的职责边界

这是第二轮需求的核心：**模型在模型管理维护，配置页只做选择。**

```
┌──────────────────────────────┐        ┌───────────────────────────┐
│  模型管理（models 表）        │        │  系统配置（app_config 表） │
│                              │        │                           │
│  每条记录 = 一个完整模型      │        │  llm_model_id  ──────────┐│
│  · kind: llm / embedding     │◀───────┼─ embedding_model_id ─────┤│
│  · provider                  │  引用   │                           │
│  · model / base_url / api_key│        │  + 运行时参数：            │
│  · path / dim                │        │    device / batch_size    │
│                              │        │    temperature / top_k    │
└──────────────────────────────┘        └───────────────────────────┘
```

**为什么这样分**

- 同一个模型可能被多个会话、多个配置引用，连接信息不该重复填写
- 换一个 API Key 只需改模型记录，不用碰配置
- 配置页保持「选择 + 调参」的轻量语义，符合需求描述

**回退链**

```
app_config.llm_model_id  →  models 表记录  →  没有则用 .env 的 LLM_PROVIDER 等
```

`settings.effective_all()` 的合并顺序是
**运行时配置 > .env > DEFAULT_CONFIG**，
`DEFAULT_CONFIG` 只补齐 Settings 里不存在的键（如 `llm_model_id`），
绝不覆盖 `.env`。

## 8. 部署形态
### 开发：Milvus Lite

```
应用进程 ──▶ milvus_lite (进程内) ──▶ data/milvus/kgrag.db
```

- 零外部依赖
- 单进程独占（文件锁）
- 适合开发、测试、单机小规模

### 生产：Milvus 服务端

```
应用 ──gRPC:19530──▶ milvus ──▶ etcd (元数据)
                       └──────▶ minio (对象存储)
```

- 多客户端并发
- 数据规模可扩展
- `docker compose --profile milvus up -d` 一键启动

### minio 与 Milvus Lite 的区别

| | minio | Milvus Lite |
|---|---|---|
| 是什么 | S3 兼容对象存储 | Milvus 的嵌入式版本 |
| 作用 | 存 Milvus 的段文件、索引、WAL | 整个向量数据库 |
| 形态 | 独立服务 | Python 包，进程内 |
| 依赖 | Milvus 服务端必需 | 无 |
| 规模 | TB 级 | 单机百万级以内 |

**minio 之于 Milvus 服务端，相当于磁盘之于数据库**——它是底层存储，
不能绕过。

## 9. 后续规划

| 模块 | 状态 | 说明 |
|---|---|---|
| Milvus + LangChain + Embedding | ✅ 已完成 | 端到端可用 |
| 管理页面 | ✅ 已完成 | 看板/知识库/上传/对话 |
| 用户认证与权限 | ✅ 已完成 | 本地 / AD，三层隔离 |
| 管理员配置与模型管理 | ✅ 已完成 | 配置页 / 模型管理 / 系统看板 |
| Neo4j 图谱增强 | ⏳ 规划 | GraphRAG |
| 文档解析增强 | ⏳ 规划 | OCR、表格、代码 |
| 权限管控 | ⏳ 规划 | 多租户、字段级过滤 |
| 日志审计 | ⏳ 规划 | 操作留痕 |
