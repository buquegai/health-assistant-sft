# 健康助手 Web 应用

首版包括账号注册与登录、按账号隔离的历史会话、聊天页模型选择、Markdown 消息、流式模型代理、停止和失败重试。模型选择按会话保存；新会话使用当前选择，同一会话更换模型后，已有对话历史仍会作为上下文发送给新模型。浏览器只请求同源 `/api`（应用接口）；模型服务地址、可选密钥、固定系统提示词和分词器均由后端读取。

**安全范围限制：** 本地真实模型抽查发现药物剂量回答存在矛盾和事实错误，紧急症状回答也可能延误就医。当前版本仅限受控的低风险界面演示，不适用于诊断、用药或急救分流；应用没有可靠的高风险输入拦截或答案安全过滤。详见 `验收记录.md`（逐项验收）和 `screenshots/A13-健康内容抽查.md`（真实模型回答及人工复核）。

## 运行条件

- Python 3.11（建议使用文档中的 WSL Ubuntu 环境）和 Node.js 20 或更新版本。
- LLaMA-Factory 已安装，微调适配器位于推理配置指定目录。
- 单个后端 worker 运行在本机，单次只允许一个模型生成任务。

本机通过 WSL Ubuntu 22.04 提供 Python 3.11、LLaMA-Factory、模型权重与 CUDA。应用后端使用独立虚拟环境；不要在训练环境中安装应用依赖。

## 1. 启动模型服务

在 WSL Ubuntu 终端执行，使用既有训练环境，不要在应用虚拟环境加载模型：

```bash
cd /home/woei/projects/LLaMA-Factory
source .venv/bin/activate
API_HOST=127.0.0.1 API_PORT=8000 API_MODEL_NAME=health-assistant \
  python '/mnt/d/Woei_Project/健康助手SFT/webapp/serve_model.py' \
  '/mnt/d/Woei_Project/健康助手SFT/configs/psychology_infer.yaml'
```

模型服务会读取 `configs/psychology_infer.yaml`（推理配置）中的基础模型与 LoRA 适配器，不需要合并权重。应用只从后端向 `http://127.0.0.1:8000/v1/chat/completions`（模型聊天接口）发起流式请求。

聊天页现在提供两个真实选项：`health-assistant`（健康助手微调版，当前仍为 `train_01`）和 `qwen3-base`（Qwen3-1.7B 原始模型，未应用本项目的微调）。服务只加载一份 4-bit（四比特）量化基础模型；原始版在整个生成线程内关闭 LoRA，完成、停止或异常退出后自动恢复，微调版启用 LoRA。两种模式共用一次生成许可，不能同时生成。此实现要求未合并、未训练偏置的 LoRA；不兼容的权重会在启动时明确报错。

原始版使用同样的健康助手系统提示词、`qwen3_nothink`（项目原有训练模板）和生成参数；它是原始权重的量化推理模式，不是去掉系统提示词的通用聊天产品，也不是全精度基准。此模板不强制禁止模型输出思考文本，原始版实测仍可能生成 `<think>`（思考标签），页面按原文显示，这部分也占用输出预算。对比效果时，建议分别新建对话并输入相同问题；在同一会话切换模型会继续携带此前问答。2026-11-05 训练的 `train_swanlab_01` 尚未在此配置中启用。

默认接口模型名分别为 `health-assistant` 和 `qwen3-1.7b-base`。若自定义，模型进程的 `API_MODEL_NAME` / `API_BASE_MODEL_NAME`（微调版/原始版接口名称）必须分别与应用后端的 `MODEL_NAME` / `BASE_MODEL_NAME` 一致。两者共用 `MODEL_BASE_URL`（模型服务地址）和 `MODEL_API_KEY`（密钥）。

`serve_model.py`（模型服务适配启动器）为当前 LLaMA-Factory HuggingFace 流式实现补上停止条件，并等待 `model.generate`（模型生成线程）真正退出后才确认取消；标准 CLI（命令行入口）的断流实现会遗留生成线程，不能用于本应用的停止验收。适配器不修改 LLaMA-Factory 源码、训练环境包或模型权重，并提供 `/v1/health/generation`（生成能力检查）供应用后端确认取消支持。

## 2. 启动后端

在另一个 WSL Ubuntu 终端执行：

```bash
cd '/mnt/d/Woei_Project/健康助手SFT/webapp/backend'
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8080 --workers 1
```

后端首次启动时会在 `webapp/backend/data/app.db`（应用数据库）创建 SQLite 数据库。数据库、WAL 日志和 `.env`（本地环境配置）已排除在 Git 外。登录失败计数和注册频率计数保存在进程内存，后端重启后清空。

`.env` 可配置 `MODEL_REGISTRY_PATH`（后端模型白名单文件，默认 `model_registry.yaml`）、`MODEL_BASE_URL`（当前模型接口地址）、`MODEL_NAME`（公开模型名）、`MODEL_API_KEY`（仅后端可读的可选密钥）、`APP_ORIGIN`（允许的浏览器来源）、`SESSION_TTL_DAYS`（会话有效天数）和 `COOKIE_SECURE`（HTTPS Cookie）。`model_registry.yaml`（模型注册表）只向浏览器公开模型 `id`（标识）和 `name`（显示名称）；服务地址来自后端环境变量或后端配置，密钥只从后端环境读取。新增模型时，在注册表中添加唯一 ID、显示名、独立服务地址变量、服务模型名变量、可选密钥变量及对应推理 YAML（配置文件）；推理 YAML 中的基础模型目录用于选择对应 tokenizer（分词器）和固定系统提示词。模型服务需实现本项目使用的生成取消接口。

聊天使用注册表每个条目的 `inference_config`（推理配置路径）读取 `default_system`（固定系统提示词）和 `model_name_or_path`（基础模型分词器目录）。路径相对于后端目录解析；当前两个条目共用 `configs/psychology_infer.yaml`。若配置或模型位置变更，应修改条目和对应 YAML。旧的 `INFERENCE_CONFIG_PATH` / `TOKENIZER_PATH` 配置不覆盖注册表的聊天配置。分词器按需加载，不加载模型权重；需能读取 Qwen3 tokenizer 文件。

## 3. 启动前端

在第三个终端执行：

```bash
cd '/mnt/d/Woei_Project/健康助手SFT/webapp/frontend'
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`（开发网页地址）。Vite（前端开发服务器）将 `/api`（应用接口前缀）转发至 `127.0.0.1:8080`（应用后端），保持浏览器同源 Cookie。构建部署时，在 `frontend`（前端）执行 `npm run build`；FastAPI（后端框架）会提供 `frontend/dist`（构建后的静态页面），并支持刷新 `/chat/:conversationId`（指定会话页）。本地 HTTP 使用 `COOKIE_SECURE=false`（不限制 Cookie 仅 HTTPS）；HTTPS 部署必须改为 `true` 并配置实际 `APP_ORIGIN`（允许来源）。

## 验收记录

执行状态见仓库根目录的 `webapp/验收记录.md`（逐项验收记录）。运行 `npm run test:acceptance`（浏览器验收脚本）会通过 Edge（网页浏览器）检查实时模型流程、指定视口、刷新恢复和输入法发送行为，并把截图写入 `webapp/screenshots`（验收截图目录）；故障状态截图会使用脚本提供的受控模拟 SSE（事件流），不作为真实模型故障验证证据。

运行 `npm run test:model-picker`（模型选择器浏览器测试）会用两个受控模型选项检查切换、会话保存、发送绑定、刷新恢复和 320px 手机宽度布局；该测试使用模拟接口，不调用模型服务或占用 GPU。

在 WSL 后端环境中，从 `webapp/backend` 执行 `python scripts/model_smoke.py`（真实模型代理冒烟测试），会注册短期测试账号、向当前默认模型发送一条低风险问题，并在结束时删除测试会话和临时账号。

加上 `--all-models`（遍历全部模型）会依次验证微调版、原始版、切回微调版的真实流式回答及会话模型保存。模型切换的权重与异常恢复测试使用训练环境，在项目根目录执行 `/home/woei/projects/LLaMA-Factory/.venv/bin/python -m unittest discover -s webapp/tests -v`；测试使用本地随机初始化的小模型，无需下载权重。
