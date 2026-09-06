# AGENTS.md — hk02 · AI数学成绩提升系统（MathBoost）

本文件为 AI 编码助手（opencode / Claude Code / Codex 等）在本仓库工作时提供指引。基础设施（端口、URL、nginx、systemd）以 `/data/www/AGENTS.md` 为唯一事实来源。

## 站点信息

- 目录：`/data/www/hk02`
- 对外 URL：`https://www.top580.com/hk02/`
- 端口：`127.0.0.1:8041`（严禁 0.0.0.0）
- 运行方式：systemd `hk02.service`（gunicorn，User=claudeuser）
- venv：`hk02/venv`（独立）
- 技术栈：Python + Flask + SQLite（`data/math.db`，WAL）

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
- 数据库迁移采用「新增列需带 DEFAULT」的保守策略，禁止破坏性重建。

## 开发铁律（与 hk03 一致，完整保留）

1. **中文提交**：`git commit -m "中文说明"`。
2. **CHANGELOG.md 同条目同步**：每次提交同步更新 CHANGELOG（最新在最上）。
3. **git tag 为版本号唯一事实来源**：版本号格式 `v主.次.修订`；发布打 tag 并 `git push origin main --tags`。
4. **推送**：提交后 `git push origin main`；失败必须告知用户，不得静默。

## 操作规范

- 改代码后：`sudo systemctl restart hk02`（需 root，由用户执行）。
- 改 nginx：先备份 → `nginx -t` 通过 → reload；片段见 `nginx-hk02.conf`。
- 查问题：`systemctl status hk02`、`journalctl -u hk02 -n 50`、`tail logs/error.log`、`ss -tlnp | grep :8041`。
- 模板内资源路径一律相对（`static/css/style.css`、`mistakes`），禁止 `/static/...`、`/api/...` 根绝对路径。
- 首次访问 `/login` 设置管理密码（存 `config` 表，哈希）；忘记密码可删 `data/math.db` 中 `config` 表 `pw` 行重置（会保留其他数据）。
