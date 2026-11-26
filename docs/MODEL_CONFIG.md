# 模型配置手册

本文档说明如何配置 Embedding 模型和大模型。

配置入口：管理员登录 →「**模型管理**」→「+ 新增模型」

配置完成后，还需到「**系统配置**」的下拉框中选择刚添加的模型才会生效。

---

## 一、两种模型的分工

| 模型 | 作用 | 不配会怎样 |
|---|---|---|
| **Embedding** | 把文档和问题转成向量，用于**检索** | 完全无法上传文档、无法检索 |
| **大模型（LLM）** | 根据检索结果**生成回答** | 降级为「纯检索模式」，直接返回原文片段 |

> 两个模型都要配。只配 Embedding 能搜索但不会总结；只配 LLM 没有知识库内容可用。

---

## 二、Embedding 模型

### 2.1 本地模型（推荐，离线可用）

**适用**：服务器有 GPU 或 CPU 性能足够，希望数据不出内网。

**配置步骤**：

1. 下载模型到服务器，例如 `models/bge-m3/`
2. 目录结构需符合 sentence-transformers 约定：

```
models/bge-m3/
├── config.json                 ← 必需
├── model.safetensors           ← 必需（或 pytorch_model.bin）
├── tokenizer.json              ← 必需
├── tokenizer_config.json
├── sentence_bert_config.json   ← 建议
└── 1_Pooling/                  ← 建议
```

3. 在「模型管理」中：

| 字段 | 填写 |
|---|---|
| 名称 | `bge-m3`（任意，便于识别） |
| 类型 | `Embedding` |
| 提供方 | `本地目录（sentence-transformers）` |
| 模型目录 | `./models/bge-m3` 或 `D:/models/bge-m3` |
| 维度 | 留空自动探测，或填 `1024` |

4. 填完路径后**失焦**，系统会自动检查目录并提示维度

**下载模型**：

```bash
# 方式一：HuggingFace CLI
pip install huggingface_hub
huggingface-cli download BAAI/bge-m3 --local-dir ./models/bge-m3

# 方式二：国内镜像
HF_ENDPOINT=https://hf-mirror.com \
  huggingface-cli download BAAI/bge-m3 --local-dir ./models/bge-m3
```

**常见模型**：

| 模型 | 维度 | 体积 | 说明 |
|---|---|---|---|
| `BAAI/bge-m3` | 1024 | ~2.3 GB | 中英文多语言，效果好，**推荐** |
| `BAAI/bge-large-zh-v1.5` | 1024 | ~1.3 GB | 纯中文 |
| `BAAI/bge-small-zh-v1.5` | 512 | ~95 MB | 轻量，速度快 |
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | ~90 MB | 英文，最轻量 |

**注意事项**：

- 首次加载需要时间（大模型 10~30 秒），之后有缓存
- 显存不足时在「系统配置」把「批大小」调小（如 8）
- 切换 Embedding 模型后**必须重建知识库**（维度不同，旧向量不可用）

### 2.2 OpenAI 兼容接口

**适用**：使用 OpenAI、DeepSeek、通义、vLLM 部署的服务等。

| 字段 | 填写示例 |
|---|---|
| 名称 | `text-embedding-3-small` |
| 类型 | `Embedding` |
| 提供方 | `OpenAI 兼容` |
| API 地址 | `https://api.openai.com/v1` |
| API Key | `sk-xxxx` |
| 模型标识 | `text-embedding-3-small` |
| 维度 | `1536` |

**常见服务商**：

| 服务商 | API 地址 | 模型标识 | 维度 |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `text-embedding-3-small` | 1536 |
| OpenAI | 同上 | `text-embedding-3-large` | 3072 |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `text-embedding-v3` | 1024 |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `embedding-3` | 2048 |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `BAAI/bge-m3` | 1024 |

> ⚠️ API 地址**必须带 `/v1`**（或服务商要求的完整路径），否则会 404。

### 2.3 Ollama

**适用**：本机已装 Ollama，想统一管理模型。

```bash
ollama pull bge-m3
```

| 字段 | 填写 |
|---|---|
| 类型 | `Embedding` |
| 提供方 | `Ollama 本地服务` |
| API 地址 | `http://localhost:11434` |
| 模型标识 | `bge-m3` |
| 维度 | `1024` |

### 2.4 哈希占位（仅测试）

**适用**：只想跑通链路，不关心检索质量。

- 提供方选 `哈希占位`
- 无需任何配置
- ⚠️ **向量无语义**，检索结果基本随机，**不要用于生产**

---

## 三、大模型（LLM）

### 3.1 Anthropic Claude

| 字段 | 填写 |
|---|---|
| 类型 | `大模型（LLM）` |
| 提供方 | `Anthropic（Claude）` |
| API 地址 | 留空（用官方）或 `https://api.anthropic.com` |
| API Key | `sk-ant-xxxx` |
| 模型标识 | `claude-sonnet-4-5` |

**可用模型**：`claude-sonnet-4-5`、`claude-opus-4-5`、`claude-haiku-4-5`

Key 获取：https://console.anthropic.com/settings/keys

### 3.2 OpenAI 兼容接口（最常用）

**适用**：OpenAI、DeepSeek、通义、智谱、Moonshot、vLLM、公司内网网关等。

| 字段 | 填写示例 |
|---|---|
| 类型 | `大模型（LLM）` |
| 提供方 | `OpenAI 兼容` |
| API 地址 | `https://api.deepseek.com/v1` |
| API Key | `sk-xxxx` |
| 模型标识 | `deepseek-chat` |

**常见服务商配置**：

| 服务商 | API 地址 | 模型标识 |
|---|---|---|
| **DeepSeek** | `https://api.deepseek.com/v1` | `deepseek-chat` / `deepseek-reasoner` |
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o` / `gpt-4o-mini` |
| **阿里云百炼** | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-max` |
| **智谱 GLM** | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` |
| **Moonshot** | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| **硅基流动** | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` |
| **本地 vLLM** | `http://localhost:8000/v1` | 启动时指定的模型名 |

> ⚠️ 三个易错点：
> 1. API 地址**必须带 `/v1`**（除非服务商另有说明）
> 2. 模型标识是**服务商的模型名**，不是显示名
> 3. 有些服务商（如 DeepSeek）的 base_url 是 `https://api.deepseek.com`（不带 `/v1`）也能用，
>    但带上更保险

### 3.3 Ollama

```bash
ollama pull qwen2.5:7b
```

| 字段 | 填写 |
|---|---|
| 类型 | `大模型（LLM）` |
| 提供方 | `Ollama 本地服务` |
| API 地址 | `http://localhost:11434` |
| 模型标识 | `qwen2.5:7b` |

**推荐模型**：`qwen2.5:7b`（中文好）、`llama3.1:8b`、`deepseek-r1:7b`

### 3.4 Mock（仅测试）

- 提供方选 `Mock`
- 返回固定占位文本，不联网
- 用于验证「配置流程是否通畅」

---

## 四、配置完成后

### 4.1 在系统配置中选中模型

「模型管理」里添加只是**登记**，还需要：

1. 进入「**系统配置**」
2. 「Embedding 模型」下拉选刚加的模型
3. 「大模型」下拉选刚加的模型
4. 点「保存配置」

侧边栏底部的黄色警告消失，说明配置生效。

### 4.2 验证配置

```bash
# 查看当前生效的模型
curl http://localhost:8000/health

# 期望返回
# "embedding_ready": true,
# "llm_ready": true
```

或命令行自检：

```bash
python -m kgrag.cli doctor
```

---

## 五、常见问题

### 检索结果不相关

1. **Embedding 模型换过但没重建索引** —— 维度变了，旧向量无效。删除知识库重新上传
2. **切块太大** —— 在「系统配置」把 `切块大小` 从 800 调到 500
3. **用的是哈希占位** —— 换成真实的 embedding 模型

### 大模型报错 "API Key 无效"

- 检查 Key 是否复制完整（前后不要有空格）
- 检查 API 地址是否需要 `/v1`
- 检查账户余额

### 本地模型加载失败

```
OSError: We couldn't connect to 'https://huggingface.co'
```

说明填的是模型名而不是本地路径。要么：
- 把「模型目录」改成真实的本地目录
- 或设置 `HF_ENDPOINT=https://hf-mirror.com` 让它联网下载

### 显存不足

```
torch.cuda.OutOfMemoryError
```

在「系统配置」把「批大小」调到 8 或 4；或把「运行设备」改成 `cpu`（慢但能跑）。

### 配置后仍显示"未配置"

检查是否在「系统配置」里**选中**了模型——「模型管理」只是登记，不选中不生效。

---

## 六、配置对照表

| 场景 | Embedding | 大模型 | 说明 |
|---|---|---|---|
| **纯内网离线** | 本地 bge-m3 | Ollama qwen2.5 | 数据不出网 |
| **效果优先** | OpenAI text-embedding-3-large | Claude / GPT-4o | 需外网 |
| **成本优先** | 本地 bge-small | DeepSeek | 便宜 |
| **快速验证** | 哈希占位 | Mock | 不联网，仅测链路 |
| **只检索不生成** | 任意 Embedding | 不配 | 降级为纯检索模式 |
