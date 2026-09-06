# CHANGELOG.md

## v1.3.0（2026-09-06）AI 错因分析重做：分段输出 + 年级感知 + 思维训练建议

- 提交哈希：（待回填）
- 问题背景：首版 JSON 输出在长几何证明上被截断，导致用户看到的"错因深度分析"为空。
- 重做内容：
  - AI 输出改为**分段标记**（【正确答案】【解答过程】【错因深度分析】【思维训练建议】），彻底摆脱 JSON 截断风险；max_tokens 提至 3000；分析/建议缺失时自动二次补调；
  - 错因分析改为 **AI 独立枚举**该题最可能的 2~4 种错误原因（含典型表现与自查方法），用户预选原因仅作参考，并指认最可能的一种；
  - 新增**思维训练建议**板块：门萨式思维训练（写明训练什么、怎么练）+ 本题型专项练习 + 习惯改进；
  - 新增**年级设置**（默认初二，初一~高三），AI 按年级定制讲解深度与已学范围；设置页可改；
  - `mistakes` 新增 `ai_advice` 列；解答页支持**重新生成**；错题本/复习页同步展示四个板块。
- 实测：用户真实几何证明题（三角形内点和不等式）——解答完整、错因枚举"推理链断裂/辅助线目的不清"等、建议含"条件—目标倒推训练"，全部符合预期。
- 涉及文件：`app.py`、`templates/solve.html`、`templates/mistakes.html`、`templates/review.html`、`templates/settings.html`、`static/css/style.css`、`CHANGELOG.md`。
- 回退方式：`git revert <hash>` 后热重载。

## v1.2.0（2026-09-06）AI 解答与错因深度分析闭环

- 提交哈希：4d511c1
- 核心新流程：录入错题保存后自动跳转「AI 解答页」——AI 教师先给出**正确答案 + 分步解答过程**，再根据用户选择的错误原因（含逻辑缺陷定位）输出**针对性错因深度分析**（批判性思维视角），全部结果存档并在错题本/复习页复用。
- 技术实现：
  - 复用本机既有 OpenAI 兼容网关（凭据存 `data/ai.json`，不入库，符合安全红线）；`ai_chat`/`ai_solve_mistake` 引擎函数，教师系统提示词 + JSON 结构化输出 + 稳健解析（容忍 markdown 围栏/杂文本）；
  - 新路由 `/solve`（解答页）、`/ai_solve`（POST，fetch 异步调用，含加载动画与失败重试）；
  - `mistakes` 表新增 `ai_answer`/`ai_analysis` 列（保守迁移，DEFAULT ''）；
  - 错题表单语义调整：「正确答案」→「我当时写的答案（可选，供 AI 对比分析）」；
  - 错题列表：AI 解答 / AI 错因分析 / 我的答案 三层折叠展示，未解答的可一键补生成；复习页整合 AI 解答与分析。
- 实测：真实网关调用，答案、分步过程、错因定位（含"移项后常数合并错误"的具体指出）全部正确。
- 涉及文件：`app.py`、`templates/solve.html`（新增）、`templates/mistakes.html`、`templates/review.html`、`static/css/style.css`、`CHANGELOG.md`、`AGENTS.md`。
- 回退方式：`git revert <hash>` 后热重载；AI 网关不可用时自动降级为手动模式，不影响其余功能。

## v1.1.4（2026-09-06）AGENTS.md 记录免 root 热重载方法

- 提交哈希：a6b3c8d
- 站名已通过 SIGHUP 热重载在线上生效（v1.1.3 代码）；将「kill -HUP 平滑重启 worker」写入操作规范，今后代码修改 AI 可自行生效，无需用户 sudo。
- 纯文档修改，不影响线上站点。
- 涉及文件：`AGENTS.md`、`CHANGELOG.md`。
- 回退方式：`git revert <hash>`。

## v1.1.3（2026-09-06）修复线上站名未生效：开启模板热更新

- 提交哈希：601df47
- 背景：v1.1.1 的改名未在线上生效——服务上次重启（12:36）早于改名提交（13:18），Jinja 模板缓存在 worker 内存中。
- 内容：`app.py` 开启 `TEMPLATES_AUTO_RELOAD = True`，此后模板修改无需重启即可生效；本次仍需重启一次以加载新站名与该配置。
- 涉及文件：`app.py`、`CHANGELOG.md`。
- 回退方式：`git revert <hash>` 后 `sudo systemctl restart hk02`。

## v1.1.2（2026-09-06）AGENTS.md 统一 hk03 标准（开发铁律全套）

- 提交哈希：（待回填）
- 将 AGENTS.md 升级为与 hk03 一致的完整标准：新增「开发前需求澄清 + 一次性调查」「开发后提交保存 + 立即生效 + 固定格式版本汇报」「版本管理增减规则」「Git 工作流（哈希回填/提交前检查/精准 add/禁止 force 改写历史）」「安全红线」五个强制章节。
- 明确每次修改闭环：提交 → CHANGELOG → 打 tag → push → 请用户 `sudo systemctl restart hk02` 立即生效。
- 新增 `CLAUDE.md -> AGENTS.md` 软链（与 hk03 一致，Claude Code 自动加载）。
- 回填 v1.1.1 条目哈希 52186bd。
- 涉及文件：`AGENTS.md`、`CHANGELOG.md`、`CLAUDE.md`。
- 回退方式：`git revert <hash>`（纯文档修改，不影响线上站点）。

## v1.1.1（2026-09-06）网站更名为「黄曼清的MathBoost」

- 提交哈希：52186bd
- 全站名称由「AI 数学成绩提升系统 / MathBoost」统一改为「黄曼清的MathBoost」，覆盖导航品牌、登录页、全部 14 个模板的页面标题。
- 涉及文件：`templates/*.html`。
- 回退方式：`git revert 52186bd` 后 `sudo systemctl restart hk02`。

## v1.1.0（2026-09-06）逻辑思维诊断 + 拍照录题（OCR）

- 提交哈希：ce57c71
- 新增「逻辑思维诊断」（Critical Thinking）：
  - 错因「思路错误」升级为「逻辑思维」，录入时进一步定位 10 类逻辑缺陷（条件识别/推理链断裂/概念混淆/方向选择/特殊到一般/忽视边界/循环论证/因果倒置/分类讨论缺失/命题逻辑错误）；
  - AI 报告新增「逻辑思维诊断」区块：缺陷分布画像 + 定义解析 + 按缺陷→题型的针对性训练处方（一键开练）；
  - 仪表盘「今日建议」接入思维训练打卡提醒。
- 新增「思维训练」模块（门萨式横向/纵向思维题库 49 题，六大类：逻辑演绎/数字规律/图形空间/横向思维/策略决策/论证分析）：每日一题、随机混合、分类专项，答题后展示答案与解析，累计战绩与正确率统计。
- 新增「拍照录题」：错题表单支持拍照/图片上传（手机调起相机），RapidOCR 离线手写识别自动填入题干（可修正），原图（含几何图形）永久保存，错题列表与复习页均可查看原图；文字删除/修改不影响原图。
- 技术变更：mistakes 表新增 logic_type/photo 列（保守迁移）；新增 puzzles/thinking_log 表；新增 /ocr /photo /thinking /thinking_start /daily_start /puzzle /puzzle_answer 路由；引入 rapidocr-onnxruntime 依赖；新增 static/js/app.js。
- 涉及文件：`app.py`、`seed_data.py`、`templates/`（base/mistakes/review/report/thinking/puzzle）、`static/js/app.js`、`static/css/style.css`、`requirements.txt`。
- 回退方式：`git revert <hash>` 后 `sudo systemctl restart hk02`；数据库新列可保留不影响旧逻辑。

## v1.0.1（2026-09-06）新增一键上线脚本 setup-root.sh

- 提交哈希：f04926f
- 背景：AI 会话内 sudo 被禁用（容器 no new privileges），服务安装与 nginx 变更无法代执行，站点仍显示关停页。
- 内容：新增 `setup-root.sh`（root 一键执行：备份 nginx → 装 systemd 服务并启动 → 将 /hk02/ 关停提示块替换为 8041 反向代理 → `nginx -t` 通过后 reload → curl 验证）；幂等设计，重复执行安全；替换文本已用真实配置副本验证精确匹配。
- 涉及文件：`setup-root.sh`、`CHANGELOG.md`。
- 回退方式：`git revert <hash>`；nginx 回退用 `/tmp/top580-hstock.bak.<时间戳>` 覆盖回。

## v1.0.0（2026-09-06）建仓首版：AI数学成绩提升系统 MathBoost

- 提交哈希：2b3ecd3
- 站点由关停状态重建为「AI 数学成绩提升系统」，部署于 /hk02/，端口 127.0.0.1:8041。
- 功能：
  - 仪表盘：综合掌握度、今日待复习、模块掌握度条形图、薄弱知识点 TOP5、今日建议、成绩速览；
  - 错题本：录入（题干/答案/知识点/错因/难度/出处）、错因筛选、删除；错因七分类（概念/方法/思路/审题/计算/粗心/其他）；
  - 复习：简化 SM-2 间隔重复，到期队列逐题回忆自评（又错了/记模糊/记住了），连续通过 3 次自动毕业；
  - 智能练习：三种模式（智能弱项 / 混合摸底 / 自选专项），模块交错出题，自评对/半对/错，答错自动入错题本；
  - 掌握度图谱：知识点条形图 + 五级自评校准；
  - 成绩记录：录入考试得分/满分，SVG 得分率趋势折线图；
  - 学习计划：按考试倒计时与薄弱模块生成三阶段计划、每日任务模板、时间分配建议；
  - AI 诊断报告：掌握度总览、模块诊断、错因结构、薄弱清单、规则引擎个性化建议、学习科学模型说明；
  - 设置：学段切换（初中/高中，图谱与题库独立）、考试日期、目标、修改密码。
- 学习科学内核：掌握学习（Bloom）、刻意练习、间隔重复（Ebbinghaus）、检索练习、交错练习、错误驱动学习、费曼技巧。
- 内置数据：知识点 53 个（初中 33 + 高中 20）、题库 254 题（初中 135 + 高中 119）。
- 涉及文件：全部（建仓首版）。
- 回退方式：`git revert <hash>`；站点回退为关停态需恢复 nginx 提示页并 `systemctl disable --now hk02`。
