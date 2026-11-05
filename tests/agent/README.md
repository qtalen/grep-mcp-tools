# B04 Agent 测试台

本目录是一套独立的 agent 实验设施：用同一个 LLM 跑同一批任务，对比「挂代理」
与「直挂全部上游工具」的差异，并单独消融代理的两个被动信道（description 注入
与双零路标）。产品代码与运行时依赖不受影响。

## `.env` 用法

配置**只认项目根目录的 `.env`**（`tests/agent/config.py` 用
`python-dotenv` 的 `dotenv_values()` 读字典），**不读取任何进程环境变量**。
这样不会出现「shell 里残留 export 与 `.env` 谁优先」的歧义。

首次使用：

```powershell
Copy-Item .env.example .env
```

然后填写 `.env`：

- `DEEPSEEK_BASE_URL`：DeepSeek OpenAI 兼容端点（必填）
- `DEEPSEEK_API_KEY`：API key（必填；`.env` 已在 `.gitignore` 中）
- `DEEPSEEK_MODEL`：模型名，默认 `deepseek-flash`（可缺省）
- `DEEPSEEK_REASONING_EFFORT`：推理强度，默认 `low`（可缺省）

缺少必填项时 harness 会 fail-fast，并提示复制 `.env.example`。

## 四臂

| 臂 | 含义 | 模型可见工具 |
| --- | --- | --- |
| a | 挂代理 | 代理的 3 个工具（`search_mcp_tool` / `invoke_this_tool` / `check_mcp_status`） |
| b | 直挂全部上游 | 5 个 demo 上游的 71 个工具，扁平化为 `server__tool` |
| c | 只关 description 注入 | 同臂 a，但从 `search_mcp_tool` description 中剥掉 `Connected services...` 块 |
| d | 只关双零路标 | 同臂 a，但从空结果响应中剥掉 `Available services:` 路标块 |

## 运行

```powershell
uv run python tests/agent/run.py
```

常用旗标：

| 旗标 | 用途 |
| --- | --- |
| `--arms` | 逗号分隔的臂列表，默认 `a,b,c,d`（如 `--arms a,b` 只跑主对比） |
| `--tasks` | 逗号分隔的任务 id，默认 `all`（如 `--tasks t1_explicit,t5_negative`） |
| `--reps` | 每任务重复次数，默认 5 |
| `--judge-only` | 不跑模型，只按当前判分规则重判已有 transcript |
| `--report-only` | 不跑模型也不重判，只聚合已有 transcript 出报告 |
| `--transcripts-dir` | transcript 目录，默认 `tests/agent/transcripts`，离线路径也从这里读 `resident.json` |

每次运行按 `run-id`（`{arm}-{task}-r{rep}`）落盘到：

```
tests/agent/transcripts/{run-id}.json
```

另有 `tests/agent/transcripts/resident.json`：各臂常驻 token 隔离测量的落盘结果
（有工具/无工具 prompt tokens、差值、工具清单序列化字符数）。`--report-only`
与 `--judge-only` 从该文件读取常驻数据；文件缺失时报告把常驻缩减标为「未测（n/a）」。
单次 run 抛异常时会落一条 `gave_up=True` 的 transcript 并继续后续 run，不会中断整批。

该目录已被 `.gitignore` 忽略（仅保留 `.gitkeep`），transcript 是判分与报告的
唯一真相源，可离线重算。

## 不需要 key 的测试

配置、工具转换、工具循环、两臂连接冒烟与 transcript 序列化都有不依赖 API key
的测试：

```powershell
uv run pytest tests/agent -q
```

冒烟测试会启动 `tests/e2e/demo_servers/` 下的真实假上游进程。
