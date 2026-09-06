# AGENTS.md — hk02 · AI数学成绩提升系统（黄曼清的MathBoost）

本文件为 AI 编码助手（opencode / Claude Code / Codex 等）在本仓库工作时提供完整指引。`CLAUDE.md` 为指向本文件的软链，两者内容一致，以本文件为唯一维护入口。基础设施（端口、URL、nginx、systemd）以 `/data/www/AGENTS.md` 为唯一事实来源。

## 开发铁律（强制，最高优先级）

### 1. 开发前：需求澄清 + 一次性完成全部调查

- 每次接到开发任务，必须先进入「澄清阶段」与用户对话，直到明确理解需求。**宁可多问一轮，不可猜测关键需求。**
- 澄清阶段必须一次性完成全部调查，包括：
  - **需求调查**：用户目标、功能边界（做什么、不做什么）、验收标准、UI/交互预期；
  - **权限与环境调查**：涉及的文件读写权限、服务启停权限、依赖是否已安装（`requirements.txt`）、测试如何运行；
  - **影响面调查**：会改动哪些模块与文件、是否影响线上站点（https://www.top580.com/hk02/ ）、如何回退。
- **一旦进入开发阶段，不得中途停下来询问用户**：
  - 遇到未预见的小问题：选择最合理、最保守的方案继续推进，并在完成汇报中列入「假设与临时决定」；
  - 仅当遇到**不可逆 / 高风险**操作（删除数据、修改线上 nginx 配置、数据库破坏性变更等）才允许中断询问；
  - 判断标准：开始编码的那一刻，已掌握完成该任务所需的全部信息。

### 2. 开发后：提交保存 + 立即生效 + 显式版本汇报（强制）

- **每次改完代码必须立即提交保存**（提交流程见第 4 条 Git 工作流），杜绝代码只改不存。
- 修改涉及线上实际运行的资源（`app.py`、`seed_data.py`、`templates/`、`static/`、`requirements.txt`）时，提交打 tag 推送后，**必须立即让修改生效**：
  - 服务为 root 级 systemd 服务，AI 会话无 root 权限：给出命令 `sudo systemctl restart hk02` 请用户执行（用户也可在会话中输入 `! sudo systemctl restart hk02`）；
  - 并在汇报中**提醒用户打开指定地址查看变更效果**（附具体页面路径）。
- 涉及 nginx 配置等需要 root 权限的操作：给出所需命令，由用户执行，不得自行提权。
- 纯文档修改（*.md）不影响线上，不重启，仅需说明"本次为纯文档修改，不影响线上站点"。

每次开发完成，最终答复必须以固定格式收尾，**第一行即版本号**：

```
✅ 已完成 vX.Y.Z（提交 <hash>）
- 修改内容：……
- 涉及文件：……
- 影响与风险：……
- 假设与临时决定：……（无则写"无"）
- 查看效果：https://www.top580.com/hk02/…（已请用户重启生效 / 纯文档修改无需重启）
- 回退方式：git revert <hash>；如需重新生效：sudo systemctl restart hk02
```

### 3. 版本管理（强制）

- 版本号格式 `v主.次.修订`（简化 semver）：
  - **修订号 +1**：bug 修复、小调整、纯文档修改；
  - **次版本号 +1**：新功能、较大改动；
  - **主版本号 +1**：架构级重写。
- **git tag 是版本号的唯一事实来源**：每次开发完成提交后打 tag 并推送：
  ```bash
  git tag vX.Y.Z && git push origin main --tags
  ```
- 版本号必须同步写入 CHANGELOG.md 对应条目。
- 查看当前版本：`git describe --tags --abbrev=0` 或 `git tag --sort=-v:refname | head -1`。
- 一次开发会话产出多个提交时，只在最后一个提交上打版本号 tag。

### 4. Git 工作流（每次修改必须完整执行，不得跳过）

1. 每完成一处修改即 `git commit`，提交说明用**中文**，简明描述做了什么、为什么。
2. 同一提交中更新 `CHANGELOG.md`（最新条目在最上方），条目包含：日期时间、版本号、标题、提交哈希、修改说明、涉及文件、回退命令。
3. **哈希回填**：新条目的哈希提交时留空，下一次提交前用 `git rev-parse --short HEAD` 将最上方条目的空缺哈希补上。
4. 提交后立即 `git push origin main`；打了 tag 则 `git push origin main --tags`；推送失败必须告知用户原因，不得静默跳过。
5. 提交前必须检查 `git status` 与 `git diff`，绝不提交密钥与敏感信息。
6. 精准 `git add` 相关文件，禁止盲目 `git add -A`。
7. 未经用户明确要求，不使用 `--force` 推送、不改写已推送的历史（rebase/amend）。

## 安全红线

- `data/`（`math.db`、`secret_key`、`photos/` 错题原图）、`logs/`、`venv/`、`__pycache__/` 永不提交（已被 `.gitignore` 排除，必须保持）。
- 密钥只放 `data/secret_key` 或环境变量，严禁写入代码、模板或前端 JS。
- 不删除 `data/math.db` 中的学习数据（错题、成绩、复习记录），除非用户明确要求。
- 数据库迁移采用「新增列需带 DEFAULT」的保守策略，禁止破坏性重建。

## 站点信息

- 目录：`/data/www/hk02`
- 对外 URL：`https://www.top580.com/hk02/`
- 端口：`127.0.0.1:8041`（严禁 0.0.0.0）
- 运行方式：systemd `hk02.service`（gunicorn，User=claudeuser，**root 级服务，重启需 sudo，由用户执行**）
- venv：`hk02/venv`（独立）
- 技术栈：Python + Flask + SQLite（`data/math.db`，WAL）
- 仓库：git@github.com:51ak/hk02.git，分支 `main`

## 项目结构

```
app.py            # Flask 入口 + 掌握度引擎 + 间隔重复(SM-2简化版) + OCR + 全部路由
seed_data.py      # 知识点树 + 题库（254 题）+ 逻辑缺陷分类 + 思维训练题库（49 题）
templates/        # Jinja2 模板（相对路径，禁止以 / 开头的绝对路径）
static/css/       # 样式
static/js/        # 表单交互（拍照 OCR、逻辑缺陷联动）
data/             # math.db、secret_key、photos/（错题原图，均不入库）
hk02.service      # systemd 服务文件（入库）
nginx-hk02.conf   # nginx location 片段（入库）
deploy.sh         # 部署指引
setup-root.sh     # 一键上线脚本（root 执行，幂等）
```

## 核心模型（改动需谨慎，勿破坏既有数据）

- **掌握度**：`kp.mastery` 0~100，自适应加权更新（练习次数越多波动越小）；错题按错因扣减（概念15/方法12/逻辑12/审题8/计算6）。
- **复习调度**：简化 SM-2，`mistakes.next_review` 到期入队；连续通过 3 次毕业（status=mastered）。
- **选题引擎**：弱项优先 + 模块交错（interleaving）。
- **逻辑思维诊断**：错因=逻辑思维时定位 `mistakes.logic_type`（10 类），报告页给出缺陷画像与思维训练处方。
- **拍照录题**：`mistakes.photo` 存原图文件名（`data/photos/`），RapidOCR（rapidocr-onnxruntime）离线识别手写，懒加载每 worker 一次。

## 操作规范

- 改代码后免 root 生效（AI 可直接执行）：`kill -HUP $(systemctl show hk02 -p MainPID --value)`——gunicorn master 以 claudeuser 运行，SIGHUP 平滑重启 worker 加载新代码；若无效再请用户 `sudo systemctl restart hk02`。
- 改 nginx：先备份 → `nginx -t` 通过 → reload；片段见 `nginx-hk02.conf`；批量上线用 `setup-root.sh`。
- 查问题：`systemctl status hk02`、`journalctl -u hk02 -n 50`、`tail logs/error.log`、`ss -tlnp | grep :8041`。
- 模板内资源路径一律相对（`static/css/style.css`、`mistakes`），禁止 `/static/...`、`/api/...` 根绝对路径。
- 首次访问 `/login` 设置管理密码（存 `config` 表，哈希）；忘记密码可删 `data/math.db` 中 `config` 表 `pw` 行重置（会保留其他数据）。
