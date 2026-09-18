# FitHealthAgent v0.1.0

本地优先的单用户健身与健康管理智能体。它把 AI 对话、Garmin 数据导入、训练记录、营养估算、训练计划、肌群恢复和本地备份整合在一个 Web 界面中。

> 发布形态：可直接部署的源码发布包，不是 PyPI 库。请在项目目录中通过 Docker Compose 或 Uvicorn 运行。

## 项目定位

FitHealthAgent 面向希望在个人电脑或私有服务器上集中管理健身健康数据的个人用户。核心数据默认保存在本地 `data/` 目录；只有在启用相关功能时，应用才会把必要信息发送给配置的模型服务或 YouTube API。

当前版本适合：

- 管理个人训练、每日状态、营养和训练计划；
- 导入 Garmin 活动 FIT、全天健康 ZIP 和睡眠 CSV；
- 结合历史训练、用户档案、已确认记忆、酸痛和肌群恢复生成建议；
- 在本地或受保护的私人环境中运行单用户服务。

当前版本不包含用户账号、登录认证、权限隔离或云同步。请勿把服务未经保护地直接暴露到公网。

## 主要功能

- **AI 健身对话**：使用 HelloAgents ReAct Agent，根据档案、近期记录、已确认记忆、健康限制和恢复状态回答问题或生成训练建议。
- **Garmin 活动解析**：解析力量训练、有氧和其他活动 FIT，展示训练组或活动分段；支持修改、合并、删除、撤销和恢复原始解析结果，确认后再保存。
- **Garmin 全天健康导入**：导入 Garmin 健康 ZIP、健康监测 FIT 和睡眠 CSV，存储心率、睡眠、步数、压力、血氧、呼吸、HRV、强度分钟及活动消耗等数据。
- **训练与每日记录**：维护训练、体重、睡眠质量、精力、疲劳、疼痛、训练 RPE、完成度和营养信息，并提供单日总览与趋势查询。
- **餐盘营养估算**：通过 OpenAI 兼容视觉模型估算食物、份量、热量和三大营养素；结果可人工修改后保存。
- **训练计划管理**：生成、上传、保存、修改和删除训练计划，支持 `.md` 与 `.txt` 文件，并在生成时检查健康限制和恢复冲突。
- **肌群恢复与酸痛跟踪**：综合动作、训练容量、主次肌群、近期负荷、Garmin 恢复小时和用户酸痛报告估算恢复状态。
- **健康风险筛查**：在普通模型对话前执行本地确定性规则，对可能紧急、需尽快就医或需谨慎处理的症状给出分流提示。
- **可确认的档案与记忆**：长期偏好、限制和训练反馈需要确认后才进入长期上下文，并支持编辑、拒绝、回滚和遗忘。
- **数据备份与恢复**：导出带校验清单的完整备份；恢复和全量重置期间启用维护保护，全量重置前自动创建恢复点。
- **数据文件审计**：检查原始 Garmin 导入文件、训练心率流和存储引用之间的不一致或孤儿文件。

## 重要安全与隐私说明

### 网络访问边界

应用是单用户本地工具，目前没有登录认证：

- Docker Compose 默认仅监听 `127.0.0.1:9999`，这是推荐配置；
- 本地 Uvicorn 也建议显式使用 `--host 127.0.0.1`；
- `python main.py` 会监听 `0.0.0.0:9999`，仅适合可信局域网或已经配置认证与 TLS 的反向代理环境；
- 如需远程访问，请在应用前增加身份认证、HTTPS、访问控制和可靠的备份策略。

### 外部数据传输

外部模型开关初始为开启状态。可在 Web 界面的“数据管理 → 外部模型与数据外发”中关闭。关闭后，本地健康数据导入、训练编辑、每日记录、查询、删除和备份仍可使用。

根据所用功能，以下信息可能发送到外部服务：

- 主对话：当前消息、近期对话、档案摘要及已确认记忆；
- 训练计划鉴定：规则无法确定时发送计划正文的一部分；
- 未知动作肌群查询：动作名称和允许的肌群枚举；
- 退出摘要：本次会话文本；
- 餐盘分析：图片及最多 500 个字符的补充说明；
- YouTube 搜索：动作关键词。

餐盘原图不会保存到本地。训练、健康、档案、记忆和备份数据可能包含敏感个人信息，请勿提交 `data/`、`.env` 或备份 ZIP 到公开仓库。

### 健康免责声明

FitHealthAgent 不是医疗器械，风险筛查、恢复时间、营养估算和训练建议仅供个人健康管理参考，不能替代医生诊断、急救服务或专业营养与训练指导。出现胸痛、呼吸困难、意识异常、严重出血或其他紧急症状时，请立即联系当地急救服务。

## 系统要求

- Python 3.11 和 3.12，推荐 3.12；
- 或 Docker Desktop / Docker Engine 与 Docker Compose；
- 可访问所选 OpenAI 兼容模型服务；
- 可选的视觉模型服务和 YouTube Data API v3。

## 快速部署

### 方式一：Docker Compose（推荐）

1. 复制环境变量模板：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 编辑 `.env`，至少配置主对话模型：

   ```dotenv
   LLM_API_KEY=your_api_key
   LLM_BASE_URL=https://api.deepseek.com
   LLM_MODEL_ID=deepseek-chat
   ```

3. 构建并启动：

   ```powershell
   docker compose up -d --build
   ```

4. 检查状态：

   ```powershell
   docker compose ps
   Invoke-RestMethod http://127.0.0.1:9999/health/storage-status
   ```

5. 打开 [http://127.0.0.1:9999](http://127.0.0.1:9999)。

Docker Compose 将宿主机 `./data` 挂载到容器 `/app/data`。重新构建镜像或容器不会清空该目录，但仍应定期导出备份。

停止服务：

```powershell
docker compose down
```

不要使用 `docker compose down -v` 作为数据清理方式；本项目使用 bind mount，数据仍位于宿主机 `data/` 中。

### 方式二：Python 本地运行

在 Windows PowerShell 中：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --require-hashes --find-links=vendor -r requirements.lock
Copy-Item .env.example .env
cd frontend
npm ci
npm run build
cd ..
python -m uvicorn main:app --host 127.0.0.1 --port 9999
```

打开 [http://127.0.0.1:9999](http://127.0.0.1:9999)。

前端开发使用两个进程，后端固定在 9999，Vite 在 5173 并代理业务 API：

```powershell
# 终端 1（仓库根目录）
python -m uvicorn main:app --host 127.0.0.1 --port 9999 --reload

# 终端 2
cd frontend
npm ci
npm run dev
```

开发时打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。生产启动前运行
`cd frontend; npm run build`。阶段 6 已移除前端运行时回退开关；发布故障通过回滚
到上一稳定镜像处理。Markdown 与字体依赖均随构建产物在本地提供。

`vendor/` 中附带 `hello-agents==1.0.0` wheel，用于锁定安装。`requirements.lock` 是推荐安装入口；`requirements.txt` 是直接依赖源文件，不保证得到与发布验证完全相同的依赖组合。

> 不建议通过 `pip install .` 部署当前发布版。当前发布物按完整源码目录运行，前端模板、入口文件和容器配置都位于 Python 包之外。

## 环境变量

复制 `.env.example` 后按需填写。不要把真实 `.env` 提交到版本控制。

| 变量                        | 是否必需     | 用途                                                                      |
| --------------------------- | ------------ | ------------------------------------------------------------------------- |
| `LLM_API_KEY`               | AI 对话必需  | 主模型 API 密钥                                                           |
| `LLM_BASE_URL`              | AI 对话必需  | OpenAI 兼容 API 地址，默认示例为 DeepSeek                                 |
| `LLM_MODEL_ID`              | AI 对话必需  | 主对话模型名称                                                            |
| `LLM_TEMPERATURE`           | 可选         | 主 Agent 生成温度，范围 `0..2`，默认 `0.7`                                |
| `LLM_MAX_TOKENS`            | 可选         | 主 Agent 单次模型输出上限；留空时由模型服务决定                           |
| `LLM_TIMEOUT`               | 可选         | 主 Agent 单次模型请求超时秒数，范围 `1..600`，默认 `90`                   |
| `LLM_MAX_RETRIES`           | 可选         | 主 Agent 请求失败后的 SDK 重试次数，范围 `0..10`，默认 `0`                |
| `FITHEALTH_AGENT_MAX_STEPS` | 可选         | 主 Agent 与计划自动修正 Agent 的 ReAct 最大步数，范围 `1..100`，默认 `15` |
| `LLM_LITE_API_KEY`          | 可选         | 意图路由、计划鉴定、摘要等轻量任务的 API 密钥；留空时通常回退到主密钥     |
| `LLM_LITE_BASE_URL`         | 可选         | 轻量模型 API 地址                                                         |
| `LLM_LITE_MODE_ID`          | 可选         | 轻量模型名称；变量名按当前实现保留为 `MODE_ID`                            |
| `VISION_API_KEY`            | 餐盘识别必需 | 视觉模型 API 密钥；未设置时可回退到主模型密钥                             |
| `VISION_BASE_URL`           | 餐盘识别必需 | 视觉模型的 OpenAI 兼容 API 地址；未设置时可回退到主模型地址               |
| `VISION_MODEL_ID`           | 餐盘识别必需 | 支持图片输入的模型名称                                                    |
| `YOUTUBE_API_KEY`           | 视频搜索必需 | YouTube Data API v3 密钥                                                  |
| `FITHEALTH_DATA_DIR`        | 可选         | 数据目录；本地默认使用项目根目录下的 `data/`，容器固定为 `/app/data`      |
| `FITHEALTH_SIGNING_KEY`     | 可选         | 餐盘分析置信度签名密钥；多进程或长期部署建议设置稳定随机值                |

`.env.example` 中还保留了 HelloAgents 生态可使用的其他服务变量；仅在实际接入对应服务时填写。
修改 Agent 超参数后需要重启应用。提高最大步数或输出上限会增加响应延迟和模型费用；
配置值格式错误或越界时，应用会报告对应的环境变量名，不会静默采用其他值。
模型故障时的最长等待大致为 `LLM_TIMEOUT × (LLM_MAX_RETRIES + 1)`，另加少量重试退避时间。

## 首次使用

1. 启动服务并打开首页。
2. 按界面提示确认个人档案和训练安排。
3. 在“数据管理 → 外部模型与数据外发”检查隐私开关。
4. 可直接对话，或上传训练、计划、餐盘和 Garmin 健康数据。
5. 完成初始配置后，从“数据管理 → 本地数据”导出一份基线备份。

## 支持的上传文件

| 用途                    | 格式            | 单文件限制           |
| ----------------------- | --------------- | -------------------- |
| Garmin 活动或健康监测   | `.fit`          | 50 MiB               |
| Garmin 全天健康批量导入 | `.zip`          | 50 MiB               |
| Garmin 睡眠数据         | `.csv`          | 2 MiB                |
| 训练计划                | `.md`、`.txt`   | 1 MiB                |
| 餐盘照片                | JPEG、PNG、WebP | 10 MiB               |
| 应用完整备份            | `.zip`          | 导入压缩包最大 1 GiB |

健康批量导入每次最多选择 5 个 `.zip` 或 `.csv` 文件。压缩包还会执行文件数量、单成员大小、解压总量、压缩比和路径安全检查。

典型导入流程：

1. 上传单个 `.fit` 时，应用会自动判断它是活动记录还是全天健康监测文件。
2. 活动 FIT 进入训练编辑区，可修改动作、重量、次数或分段后确认保存。
3. 健康 ZIP/CSV 直接写入健康数据存储；ZIP 中的活动 FIT 会单独列出供选择。
4. 请保留原始 Garmin 导出文件，并定期使用应用内备份功能。

## 数据存储

所有运行数据统一位于 `FITHEALTH_DATA_DIR`：

```text
data/
├── daily_records.json            # 训练、每日状态和营养记录
├── user_profile.json             # 用户档案
├── training_plans.json           # 已保存训练计划
├── info_store.json               # 临时记忆与确认状态
├── muscle_soreness.json          # 酸痛和疼痛记录
├── pending_workout.json          # 待确认训练，存在时创建
├── external_model_settings.json  # 外部模型隐私开关
├── health.db                     # 全天健康和睡眠时序数据
├── health-imports/               # Garmin 原始导入文件
├── hr_streams/                   # 已保存训练的 1 Hz 心率流
├── workout-quarantine/           # 损坏待确认训练的隔离副本
└── recovery-points/              # 重置或恢复前创建的恢复点
```

JSON 存储使用文件锁和原子替换；健康数据使用 SQLite。若 `/health/storage-status` 报告数据目录不可写或存储降级，应用会拒绝相关写入，避免把新数据静默写入临时位置或覆盖损坏文件。

## 备份、恢复与升级

应用内“导出备份”包含：

- 训练、档案、计划、记忆、酸痛和隐私设置 JSON；
- `health.db` 的一致性快照；
- Garmin 原始导入文件；
- 训练心率流和待确认训练隔离文件；
- 每个成员的大小和 SHA-256 校验信息。

恢复前会校验备份结构和内容。恢复和全量重置会进入维护状态；重置或覆盖恢复前最多保留 3 个自动恢复点。恢复点本身也包含敏感健康数据，不需要时应在数据管理界面永久删除。

升级建议：

1. 在旧版本中导出完整备份；
2. 停止服务；
3. 替换源码或重新构建镜像，但保留原 `data/`；
4. 启动新版本；
5. 检查 `/health/storage-status`、首页和关键记录；
6. 确认无误后再清理旧镜像或旧发布目录。

不要只备份 `health.db`。训练、档案、计划、原始 Garmin 文件和心率流分布在同一个数据目录的其他文件中。

## HTTP 接口与健康检查

- 首页：`GET /`
- 服务与存储状态：`GET /health/storage-status`
- OpenAPI 文档：`GET /docs`
- ReDoc 文档：`GET /redoc`

主要接口按以下资源组织：

- `/chat`、`/logout`：对话与会话结束；
- `/upload_fit`、`/upload_health`、`/upload_plan`、`/analyze_food`：文件上传与分析；
- `/data/*`：训练、每日记录、营养、记忆、酸痛、备份、恢复点和重置；
- `/health/*`：健康明细、趋势、睡眠、导入记录和文件审计；
- `/plans/*`：训练计划；
- `/profile/*`、`/settings/*`：档案和外部模型设置；
- `/workout_state*`：待确认训练及隔离恢复。

这些接口目前没有登录认证，不应直接暴露到不可信网络。

## 发布目录结构

```text
FitHealthAgent-0.1.0/
├── main.py                  # FastAPI 应用入口
├── fithealth_agent/         # 后端业务代码
│   ├── routes/              # HTTP 路由
│   ├── workflows/           # 对话、退出和上传编排
│   ├── domain/              # 领域规则与校验
│   └── runtime/             # 共享依赖、中间件和上传限制
├── frontend/dist/           # Vite 构建后的单页前端与哈希资源
├── vendor/                  # HelloAgents 锁定 wheel
├── data/                    # 空数据目录，首次运行时初始化
├── requirements.lock       # 已锁定运行依赖
├── requirements.txt        # 直接依赖源文件
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── LICENSE
└── 发布审查与部署说明.md
```

测试、迁移脚本、IDE 配置、开发日志、设计原型、真实 `.env` 和已有个人数据不属于公开发布包。

## 技术栈

- Python 3.11–3.12
- FastAPI、Uvicorn、Starlette
- TypeScript、Vite、Vitest、Playwright
- HelloAgents 1.0.0 / ReAct Agent
- OpenAI 兼容文本与视觉模型 API
- 原生 HTML、CSS、JavaScript 单页界面
- SQLite、JSON、旁挂文件存储
- `fitparse`、`fitfile`
- YouTube Data API v3
- Docker、Docker Compose

## 当前版本限制

- 仅面向单用户，没有账号、登录、角色权限和租户隔离；
- 没有内置 HTTPS，远程访问需自行配置安全反向代理；
- 没有跨设备同步或托管云存储；
- 模型回答质量、费用和隐私边界取决于用户配置的外部服务；
- Garmin 文件格式和设备固件存在差异，少数活动或健康字段可能无法完整解析；
- 营养、恢复和健康风险结果均为辅助信息，不构成医疗结论；
- 当前发布版按源码目录部署，不提供已验证的 PyPI wheel。

## 故障排查

### 页面无法打开

```powershell
docker compose ps
docker compose logs --tail 100 fithealth
Invoke-RestMethod http://127.0.0.1:9999/health/storage-status
```

### 模型对话失败

- 检查 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL_ID`；
- 确认模型服务支持 OpenAI 兼容的聊天补全接口；
- 检查“外部模型与数据外发”是否已关闭；
- 查看容器日志或本地终端中的网络、鉴权和超时信息。

### 餐盘分析不可用

必须配置 `VISION_MODEL_ID`，并提供 `VISION_API_KEY`/`VISION_BASE_URL`，或让它们能够按实现回退到有效的主模型配置。模型本身必须支持图片输入。

### 数据写入失败

访问 `/health/storage-status`，检查：

- `data/` 是否存在且可写；
- `health.db` 是否可打开；
- `info_store.json` 等 JSON 是否仍为合法格式；
- Docker 中 `./data:/app/data` 挂载是否生效。

修复数据文件前先复制整个 `data/` 目录或导出完整备份。

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。

## 作者与致谢

- 作者：[Malones314](https://github.com/Malones314)
- 感谢 [Datawhale](https://github.com/datawhalechina) 社区与 [HelloAgents](https://github.com/datawhalechina/hello-agents) 项目。
