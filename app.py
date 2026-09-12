import os
import base64
import json
import random
import re
import secrets
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import seed_data

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "math.db")
KEY_PATH = os.path.join(BASE_DIR, "data", "secret_key")
AI_CFG_PATH = os.path.join(BASE_DIR, "data", "ai.json")
PHOTO_DIR = os.path.join(BASE_DIR, "data", "photos")
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)

CAUSES = ["概念不清", "方法不会", "逻辑思维", "审题失误", "计算错误", "粗心大意", "其他"]
CAUSE_HIT = {"概念不清": 15, "方法不会": 12, "逻辑思维": 12, "审题失误": 8, "计算错误": 6, "粗心大意": 6, "其他": 5}
CAUSE_ADVICE = {
    "概念不清": "回归课本重读定义定理，用费曼技巧把概念讲给别人听，讲不清的地方就是漏洞。",
    "方法不会": "针对该知识点补典型例题，先看解答再独立复现，总结成“题型-方法”卡片。",
    "逻辑思维": "已进入逻辑缺陷细分诊断：查看 AI 报告的「逻辑思维诊断」区块，按缺陷类型做对应的思维训练。",
    "审题失误": "养成圈画关键词的习惯：数字、单位、“不”“至少”“分别”等字眼逐个标记。",
    "计算错误": "每天 10 分钟限时口算/竖式训练，做题留验算时间，关键步骤代回检验。",
    "粗心大意": "使用检查清单：抄题核对、符号核对、单位核对，错一次记一次，量化警惕。",
    "其他": "把错题归因写清楚，模糊归因本身就是失分来源。",
}
LOGIC_TYPES = seed_data.LOGIC_TYPES
LOGIC_TRAIN = seed_data.LOGIC_TRAIN
PUZZLE_CATS = ["逻辑演绎", "数字规律", "图形空间", "横向思维", "策略决策", "论证分析"]
LEVEL_VALUES = {0: 10, 1: 30, 2: 50, 3: 75, 4: 90}
LEVEL_NAMES = {0: "完全不会", 1: "勉强记得", 2: "基本理解", 3: "比较熟练", 4: "非常熟练"}


def load_ai_cfg():
    try:
        with open(AI_CFG_PATH) as f:
            cfg = json.load(f)
        if cfg.get("base") and cfg.get("key") and cfg.get("model"):
            return cfg
    except Exception:
        pass
    return None


def ai_chat(messages, max_tokens=3000, timeout=120):
    cfg = load_ai_cfg()
    if not cfg:
        return None, "AI 网关未配置（缺少 data/ai.json）"
    payload = {"model": cfg["model"], "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + cfg["key"], "Content-Type": "application/json"},
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            content = data["choices"][0]["message"]["content"] or ""
            if content.strip() or attempt == 2:
                return content, None
            payload["max_tokens"] = min(8000, max(4000, payload["max_tokens"] * 2))
            req = urllib.request.Request(
                cfg["base"].rstrip("/") + "/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + cfg["key"], "Content-Type": "application/json"},
            )
            last_err = "模型返回空内容（已自动加额重试）"
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600 and attempt < 2:
                last_err = f"HTTP {e.code}"
                continue
            return None, f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return None, str(e)[:120]
    return None, (last_err or "网关无响应")


def _sections(text, marks=None, keys=None):
    if marks is None:
        marks = ["【图形识别】", "【图形JSON】", "【正确答案】", "【解答过程】", "【错因深度分析】", "【思维训练建议】"]
        keys = ["figure", "figjson", "answer", "steps", "analysis", "advice"]
    out = {k: "" for k in keys}
    pos = []
    for mk in marks:
        pos.append(text.find(mk))
    if all(p < 0 for p in pos):
        return None
    for n in range(len(marks)):
        if pos[n] < 0:
            continue
        start = pos[n] + len(marks[n])
        end = min([p for p in pos[n + 1:] if p >= 0], default=len(text))
        out[keys[n]] = text[start:end].strip()
    return out


def _complete(s):
    return bool(s) and s.rstrip().endswith(("。", "！", "？", ".", "）", ")", "：", ":"))


CHINESE_MARKS = ["【考察点】", "【标准答案】", "【答案对比】", "【答题方法】", "【提升建议】"]
CHINESE_KEYS = ["kaodian", "standard", "duibi", "method", "advice"]


def ai_solve_chinese(m, kp_name, grade, stage_name):
    my_answer = (m["answer"] or "").strip() or "未提供"
    correction = (m["correction"] or "").strip()
    logic = f"；逻辑/思路自评：{m['logic_type']}" if m["logic_type"] else ""
    user = (
        f"【年级】{grade}（{stage_name}；教材版本：{textbook_of('chinese')}）\n"
        f"【文章与题目（印刷体识别，可能含OCR噪音，请智能复原文意）】\n{m['title']}\n\n"
        f"【黄曼清的作答（手写识别，往往被判错）】\n{my_answer}\n\n"
        f"【标准答案（红笔/彩色笔手写订正，或与题目同字体的印刷体答案；未提供则留空）】\n{correction or '未提供'}\n\n"
        f"【她的错误原因自评】{m['cause']}{logic}（仅供参考，请独立分析）\n\n"
        "请严格按以下五个小节输出，直接以方括号标题开头，不要输出其他任何内容：\n"
        "【考察点】先讲这篇文章：文体、内容主旨；再逐题说明命题人想考察什么能力"
        "（如内容概括、词句理解与赏析、语句段落作用、写作手法、主旨情感、拓展启示），用她能听懂的话讲“为什么出这道题”\n"
        "【标准答案】逐题列出标准答案要点（优先综合图片中的红笔订正与印刷体标准答案；"
        "若未提供标准答案，给出你依据原文拟出的答案并注明“自拟”）\n"
        "【答案对比】逐题把她的作答与标准答案对照，判定问题类型并说明理由——"
        "方向不正确 / 不够完整（缺哪些采分点）/ 不够深入（漏了什么角度、没结合原文）/ 表述不规范；"
        "直接引用她的原话指出具体差距，不空泛\n"
        "【答题方法】针对每道题的题型给出可复用的答题框架与步骤"
        "（如赏析题=判断手法+结合原文分析+表达效果+情感主旨），并教她如何回原文定位依据\n"
        "【提升建议】2~3 条针对她这次作答暴露出的阅读习惯或思维方式的训练建议，结合作答证据，可结合她的兴趣特长"
    )
    content, err = ai_chat([
        {"role": "system", "content": "你是一名经验丰富的初中语文教师，尤其精通阅读理解精讲：善于一针见血地指出学生答案与标准答案的差距，并把考点和答题方法讲得透彻易懂。"},
        {"role": "user", "content": user},
    ])
    if err:
        return None, err
    sec = _sections(content, CHINESE_MARKS, CHINESE_KEYS)
    if sec is None:
        sec = {k: "" for k in CHINESE_KEYS}
        sec["kaodian"] = content.strip()[:3000]
    if not _complete(sec["duibi"]) or not _complete(sec["method"]) or not _complete(sec["advice"]):
        extra, err2 = ai_chat([
            {"role": "system", "content": "你是语文阅读理解精讲教师。"},
            {"role": "user", "content": (
                f"【年级】{grade}\n【文章与题目】{m['title'][:1200]}\n【她的作答】{my_answer[:600]}\n"
                f"【标准答案】{(correction or '未提供')[:600]}\n\n"
                "请严格按三个小节输出：【答案对比】逐题判定她的作答属于方向不正确/不够完整/不够深入/表述不规范，引用原话指出差距；"
                "【答题方法】每题的答题框架；【提升建议】2~3条。"
            )},
        ], max_tokens=2000)
        if not err2 and extra:
            sec2 = _sections(extra, ["【答案对比】", "【答题方法】", "【提升建议】"], ["duibi", "method", "advice"])
            if sec2:
                for k in ("duibi", "method", "advice"):
                    sec[k] = sec[k] or sec2[k]
    return sec, None


def ai_solve_mistake(m, kp_name, grade, stage_name):
    msubj = m["subject"] if "subject" in (m.keys() if hasattr(m, "keys") else []) else "math"
    if msubj == "chinese":
        sec, err = ai_solve_chinese(m, kp_name, grade, stage_name)
        if err:
            return None, err
        return {
            "figure": "",
            "answer": sec.get("kaodian", ""),
            "steps": sec.get("standard", ""),
            "analysis": sec.get("duibi", ""),
            "advice": ("\n\n".join(x for x in ("【答题方法】\n" + sec["method"] if sec.get("method") else "",
                                              "【提升建议】\n" + sec["advice"] if sec.get("advice") else "") if x)).strip(),
        }, None
    my_answer = (m["answer"] or "").strip() or "未提供"
    correction = ""
    row = m.keys() if hasattr(m, "keys") else []
    if "correction" in row:
        correction = (m["correction"] or "").strip()
    logic = f"；逻辑缺陷自评：{m['logic_type']}（{LOGIC_TYPES.get(m['logic_type'], '')}）" if m["logic_type"] else ""
    mine_block = f"【我当时的作答（手写识别）】{my_answer}\n"
    corr_block = f"【红笔订正内容（识别）】{correction}\n" if correction else "【红笔订正内容（识别）】无\n"
    photo_path = ""
    try:
        row_p = m["photo"] if "photo" in (m.keys() if hasattr(m, "keys") else []) else ""
        if row_p and os.path.exists(os.path.join(PHOTO_DIR, row_p)):
            photo_path = os.path.join(PHOTO_DIR, row_p)
    except Exception:
        photo_path = ""
    user = (
        f"【年级】{grade}（{stage_name}；教材版本：{textbook_of(msubj)}）\n"
        f"【原题（印刷体识别，请以此为准解题）】{m['title']}\n"
        "(以上文字可能来自OCR，可能有识别噪音，请智能纠错理解题意)\n"
        + mine_block + corr_block +
        f"【我的错误原因自评】{m['cause']}{logic}（仅供参考，请你独立分析，不要照抄）\n\n"
        + ("题目附有原图（含几何图形与作答笔迹），请结合图片理解题意。\n" if photo_path else "")
        + "请严格按以下小节输出，直接以方括号标题开头，不要输出其他任何内容：\n"
        + ("【图形识别】描述你从图片中读出的图形结构：各点线圆的位置关系、标记（直角/等长/平行等）、"
           "已知条件在图中的体现，以及题意理解；无图形信息则写“无”。\n" if photo_path else "")
        + FIGURE_PROMPT
        + "【正确答案】只依据【原题】（及图片）作答，最终答案简洁明确\n"
        "【解答过程】分步编号（1. 2. 3. …），每步一行，写明依据的定理/法则，几何题注明用了图中哪些关系，语言适合该年级学生自学\n"
        "【错因深度分析】对照【原题】与【我当时的作答】：定位我的作答具体错在第几步/哪个式子（直接引用我的错误内容），"
        "说明为什么会错；若提供了【红笔订正内容】，对比订正思路与我的思路的关键差异（订正好在哪里）；"
        "再补充这道题其他常见的 1~2 种错误原因及自查方法\n"
        "【思维训练建议】根据上面定位到的具体错误，给 3 条针对性建议：第 1 条为数学思维训练"
        "（门萨式逻辑推理、横向思维、批判性思维/论证分析等，写明针对我哪个薄弱点、怎么练）；"
        "第 2 条为针对本题型的专项练习方法；第 3 条为学习习惯改进"
    )
    messages = [
        {"role": "system", "content": "你是一名经验丰富的中学数学教师，精通中国大陆初中与高中数学课程，讲解条理清晰、适合学生自学，分析问题直击要害。"},
        {"role": "user", "content": user},
    ]
    if photo_path:
        vpath, vtmp = _shrink_for_vision(photo_path)
        try:
            with open(vpath, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            messages[1]["content"] = [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ]
        finally:
            if vtmp:
                try:
                    os.remove(vtmp)
                except OSError:
                    pass
    content, err = ai_chat(messages)
    if err:
        return None, err
    sec = _sections(content)
    if sec is None:
        sec = {"answer": content.strip()[:3000], "steps": "", "analysis": "", "advice": ""}
    def _complete(s):
        return bool(s) and s.rstrip().endswith(("。", "！", "？", ".", "）", ")", "：", ":"))
    need_more = (not _complete(sec["analysis"])) or (not _complete(sec["advice"]))
    if need_more:
        extra, err2 = ai_chat([
            {"role": "system", "content": "你是一名专注于批判性思维训练的数学教师。"},
            {"role": "user", "content": (
                f"【年级】{grade}\n【题目】{m['title']}\n【我的答案】{my_answer}\n"
                f"【我的错因自评】{m['cause']}{logic}\n参考解答：{sec['answer'][:600]}\n\n"
                "请严格按两个小节输出：【错因深度分析】列举最可能 2~4 种错误原因及自查方法，指认我最可能的一种；"
                "【思维训练建议】3 条：门萨式思维训练（逻辑/横向/批判性思维，写明怎么练）、本题型专项练习、习惯改进。"
            )},
        ], max_tokens=1500)
        if not err2 and extra:
            sec2 = _sections(extra)
            if sec2:
                if _complete(sec2["analysis"]) or not sec["analysis"]:
                    sec["analysis"] = sec["analysis"] or sec2["analysis"]
                if _complete(sec2["advice"]) or not sec["advice"]:
                    sec["advice"] = sec["advice"] or sec2["advice"]
    return sec, None


def _fmt_ai_answer(result, chinese=False):
    parts = []
    if chinese:
        if result.get("answer"):
            parts.append("【考察点】" + result["answer"])
        if result.get("steps"):
            parts.append("【标准答案】\n" + result["steps"])
    else:
        if result.get("figure"):
            parts.append("【图形识别】" + result["figure"])
        parts.append(result.get("answer", ""))
        if result.get("steps"):
            parts.append("【解答过程】\n" + result["steps"])
    return "\n\n".join(p for p in parts if p)


FIG_ELEM_TYPES = {"polygon", "segment", "line", "ray", "circle", "angle", "right", "tick", "text"}


def parse_figure_json(text):
    if not text or "无" in text.strip()[:4] or "{" not in text:
        return None
    try:
        start, end = text.find("{"), text.rfind("}")
        obj = json.loads(text[start:end + 1])
    except Exception:
        return None
    pts = obj.get("points")
    if not isinstance(pts, dict) or len(pts) < 2:
        return None
    points = {}
    for k, v in list(pts.items())[:24]:
        name = str(k).strip()[:3]
        if not name or any(c in name for c in "<>\"'"):
            continue
        try:
            x, y = float(v[0]), float(v[1])
        except Exception:
            continue
        if abs(x) <= 500 and abs(y) <= 500:
            points[name] = [round(x, 2), round(y, 2)]
    if len(points) < 2:
        return None
    elements = []
    for el in (obj.get("elements") or [])[:40]:
        if not isinstance(el, dict):
            continue
        t = el.get("t")
        if t not in FIG_ELEM_TYPES:
            continue
        item = {"t": t}
        if t == "circle":
            if el.get("c") in points and el.get("p") in points:
                item.update(c=el["c"], p=el["p"])
            else:
                continue
        elif t == "text":
            try:
                item.update(at=[float(el["at"][0]), float(el["at"][1])])
            except Exception:
                continue
            s = str(el.get("s", ""))[:40]
            if not s or any(c in s for c in "<>"):
                continue
            item.update(s=s)
        else:
            names = [p for p in el.get("pts", []) if p in points]
            need = 3 if t in ("polygon", "angle", "right") else 2
            if len(names) < need:
                continue
            item.update(pts=names)
            if t == "angle" and el.get("label"):
                item.update(label=str(el["label"])[:12])
            if t == "tick":
                try:
                    item.update(n=min(3, max(1, int(el.get("n", 1)))))
                except Exception:
                    item.update(n=1)
        elements.append(item)
    if not elements:
        return None
    fig = {"points": points, "elements": elements}
    try:
        bb = obj.get("bbox")
        if bb and len(bb) == 4 and all(abs(float(x)) <= 500 for x in bb):
            fig["bbox"] = [round(float(x), 2) for x in bb]
    except Exception:
        pass
    return fig


FIGURE_PROMPT = (
    "【图形JSON】若本题是几何题（或附有几何图形），请再输出一个用于绘图讲解的JSON（紧凑单行）：\n"
    '{"bbox":[xmin,ymax,xmax,ymin],"points":{"A":[x,y],"B":[x,y]},"elements":[...]}\n'
    'elements 支持这些类型（t 字段）：polygon(多边形,pts=顶点数组)、segment(线段)、line(直线)、ray(射线)、'
    'circle(c=圆心点,p=圆上点)、angle(角,pts=[边上点,顶点,边上点],label=如"60°")、'
    'right(直角符号,pts=[边上点,直角顶点,边上点])、tick(等长刻度,pts=[线段两端],n=1~3)、'
    'text(文字标注,at=[x,y],s=简短文字)。\n'
    "要求：坐标取 0~10 合理范围并尽量还原形状关系；包含题目全部关键点与线段；"
    "辅助线/关键添加线用 segment 并用 text 标注名称；已知条件（直角、等长、角度）用对应标记表达；"
    "非几何题（纯代数/无图形）此节只输出：无\n"
)


def load_ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:
        return None


_OCR = None
_OCR_TRIED = False


def get_ocr():
    global _OCR, _OCR_TRIED
    if not _OCR_TRIED:
        _OCR_TRIED = True
        _OCR = load_ocr()
    return _OCR


def load_secret():
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH) as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    with open(KEY_PATH, "w") as f:
        f.write(key)
    return key


app.secret_key = load_secret()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA busy_timeout=5000")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def q(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def q1(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def run(sql, args=()):
    db = get_db()
    db.execute(sql, args)
    db.commit()


def cfg(key, default=""):
    row = q1("SELECT value FROM config WHERE key=?", (key,))
    return row["value"] if row else default


def set_cfg(key, value):
    run("INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today_iso():
    return date.today().isoformat()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS kp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL, module TEXT NOT NULL, name TEXT NOT NULL,
            mastery REAL DEFAULT 50, attempts INTEGER DEFAULT 0, updated TEXT
        );
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kp_id INTEGER NOT NULL, diff INTEGER DEFAULT 2,
            text TEXT NOT NULL, answer TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mistakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kp_id INTEGER NOT NULL, title TEXT NOT NULL, answer TEXT DEFAULT '',
            cause TEXT NOT NULL, source TEXT DEFAULT '', diff INTEGER DEFAULT 2,
            created TEXT, reviews INTEGER DEFAULT 0,
            interval_days REAL DEFAULT 0, ease REAL DEFAULT 2.2,
            next_review TEXT, last_result REAL, status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, kind TEXT, score REAL, full REAL DEFAULT 100, note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS practice_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kp_id INTEGER, qid INTEGER, result REAL, created TEXT
        );
        CREATE TABLE IF NOT EXISTS reviews_done (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mistake_id INTEGER, result REAL, created TEXT
        );
        CREATE TABLE IF NOT EXISTS puzzles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL, diff INTEGER DEFAULT 2,
            text TEXT NOT NULL, answer TEXT NOT NULL, explain TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS thinking_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            puzzle_id INTEGER, result REAL, created TEXT
        );
        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL, kind TEXT NOT NULL, created TEXT,
            questions TEXT DEFAULT '[]', answers TEXT DEFAULT '[]',
            report TEXT DEFAULT '', done INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS subject_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL, created TEXT, report TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS subject_snap (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL, subject TEXT NOT NULL, avg REAL
        );
        CREATE TABLE IF NOT EXISTS tools_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, item_key TEXT NOT NULL, result REAL, created TEXT
        );
        CREATE TABLE IF NOT EXISTS xp_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            points INTEGER NOT NULL, reason TEXT DEFAULT '', created TEXT
        );
        CREATE TABLE IF NOT EXISTS challenge_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score INTEGER, total INTEGER, created TEXT
        );
        """
    )
    db.commit()
    scols = [r[1] for r in db.execute("PRAGMA table_info(scores)").fetchall()]
    if "subject" not in scols:
        db.execute("ALTER TABLE scores ADD COLUMN subject TEXT DEFAULT 'math'")
        db.commit()
    kcols = [r[1] for r in db.execute("PRAGMA table_info(kp)").fetchall()]
    if "subject" not in kcols:
        db.execute("ALTER TABLE kp ADD COLUMN subject TEXT DEFAULT 'math'")
        db.commit()
    mcols = [r[1] for r in db.execute("PRAGMA table_info(mistakes)").fetchall()]
    if "logic_type" not in mcols:
        db.execute("ALTER TABLE mistakes ADD COLUMN logic_type TEXT DEFAULT ''")
    if "photo" not in mcols:
        db.execute("ALTER TABLE mistakes ADD COLUMN photo TEXT DEFAULT ''")
    if "ai_answer" not in mcols:
        db.execute("ALTER TABLE mistakes ADD COLUMN ai_answer TEXT DEFAULT ''")
    if "ai_analysis" not in mcols:
        db.execute("ALTER TABLE mistakes ADD COLUMN ai_analysis TEXT DEFAULT ''")
    if "ai_advice" not in mcols:
        db.execute("ALTER TABLE mistakes ADD COLUMN ai_advice TEXT DEFAULT ''")
    if "correction" not in mcols:
        db.execute("ALTER TABLE mistakes ADD COLUMN correction TEXT DEFAULT ''")
    if "ai_figure" not in mcols:
        db.execute("ALTER TABLE mistakes ADD COLUMN ai_figure TEXT DEFAULT ''")
    if "subject" not in mcols:
        db.execute("ALTER TABLE mistakes ADD COLUMN subject TEXT DEFAULT 'math'")
    db.execute("UPDATE mistakes SET cause='逻辑思维' WHERE cause='思路错误'")
    db.commit()
    rev_row = q1_static(db, "SELECT value FROM config WHERE key='puzzle_rev'")
    need_seed = q1_static(db, "SELECT COUNT(*) c FROM puzzles")["c"] == 0
    need_reseed = not need_seed and (not rev_row or rev_row["value"] != str(seed_data.PUZZLE_REV))
    if need_seed or need_reseed:
        if need_reseed:
            db.execute("DELETE FROM puzzles")
        for cat, diff, text, answer, explain in seed_data.PUZZLES:
            db.execute("INSERT INTO puzzles (category, diff, text, answer, explain) VALUES (?,?,?,?,?)",
                       (cat, diff, text, answer, explain))
        db.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('puzzle_rev', ?)",
                   (str(seed_data.PUZZLE_REV),))
    db.commit()
    if q1_static(db, "SELECT COUNT(*) c FROM kp")["c"] == 0:
        for stage, module, kps in seed_data.MODULE_TREE:
            for name in kps:
                db.execute("INSERT INTO kp (stage, module, name, subject) VALUES (?,?,?,'math')", (stage, module, name))
        db.commit()
    for code, meta in seed_data.SUBJECTS.items():
        if code == "math":
            continue
        for board in meta["boards"]:
            exists = q1_static(db, "SELECT id FROM kp WHERE subject=? AND name=?", (code, board))
            if not exists:
                db.execute("INSERT INTO kp (stage, module, name, subject) VALUES ('cj',?,?,?)",
                           (meta["name"], board, code))
        db.commit()
    if q1_static(db, "SELECT COUNT(*) c FROM questions")["c"] == 0:
        rows = db.execute("SELECT id, stage, module, name FROM kp").fetchall()
        index = {}
        for r in rows:
            index[(r["stage"], r["module"], r["name"])] = r["id"]
        for stage, module, kp, diff, text, answer in seed_data.QUESTIONS:
            kp_id = index.get((stage, module, kp))
            if kp_id:
                db.execute("INSERT INTO questions (kp_id, diff, text, answer) VALUES (?,?,?,?)", (kp_id, diff, text, answer))
        db.commit()
    defaults = {"stage": "cj", "exam_date": "", "target": "", "minutes": "40", "grade": "初二",
                "name": "黄曼清", "region": "江苏南京", "hobbies": "马术、赛艇、钢琴",
                "textbooks": json.dumps({k: v[0] for k, v in seed_data.TEXTBOOKS.items()}, ensure_ascii=False)}
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?,?)", (k, v))
    db.commit()
    db.close()


def q1_static(db, sql, args=()):
    db.row_factory = sqlite3.Row
    return db.execute(sql, args).fetchone()


def mastery_label(m):
    if m >= 80:
        return "优秀", "good"
    if m >= 60:
        return "良好", "ok"
    if m >= 30:
        return "一般", "warn"
    return "薄弱", "bad"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def update_mastery(kp_id, result):
    row = q1("SELECT mastery, attempts FROM kp WHERE id=?", (kp_id,))
    if not row:
        return
    m, att = row["mastery"], row["attempts"]
    k = max(5.0, 20.0 / (1 + att))
    nm = clamp(m + k * (result - m / 100.0), 0, 100)
    run("UPDATE kp SET mastery=?, attempts=?, updated=? WHERE id=?", (round(nm, 1), att + 1, now_iso(), kp_id))


def apply_penalty(kp_id, cause):
    hit = CAUSE_HIT.get(cause, 5)
    row = q1("SELECT mastery FROM kp WHERE id=?", (kp_id,))
    if row:
        nm = clamp(row["mastery"] - hit, 0, 100)
        run("UPDATE kp SET mastery=?, updated=? WHERE id=?", (round(nm, 1), now_iso(), kp_id))


def sm2_update(m, result):
    ease, interval, reviews = m["ease"], m["interval_days"], m["reviews"]
    if result >= 1:
        interval = 1.0 if interval < 1 else interval * max(1.3, ease)
        ease = min(2.8, ease + 0.05)
        reviews += 1
    elif result >= 0.5:
        interval = max(1.0, interval * 1.2)
        ease = min(2.8, ease + 0.02)
        reviews += 1
    else:
        interval = 1.0
        ease = max(1.3, ease - 0.15)
        reviews = 0
    nxt = (date.today() + timedelta(days=max(interval, 0.5))).isoformat()
    status = "mastered" if reviews >= 3 else "active"
    run(
        "UPDATE mistakes SET ease=?, interval_days=?, reviews=?, next_review=?, last_result=?, status=? WHERE id=?",
        (round(ease, 2), round(interval, 1), reviews, nxt, result, status, m["id"]),
    )
    run("INSERT INTO reviews_done (mistake_id, result, created) VALUES (?,?,?)", (m["id"], result, now_iso()))


def due_mistakes(subject):
    if subject == "math":
        return q(
            """SELECT m.*, kp.name kp_name, kp.module FROM mistakes m JOIN kp ON m.kp_id=kp.id
               WHERE m.status='active' AND m.next_review<=? AND kp.subject='math' AND kp.stage=?
               ORDER BY m.next_review, m.id""",
            (today_iso(), cfg("stage", "cj")),
        )
    return q(
        """SELECT m.*, kp.name kp_name, kp.module FROM mistakes m JOIN kp ON m.kp_id=kp.id
           WHERE m.status='active' AND m.next_review<=? AND kp.subject=?
           ORDER BY m.next_review, m.id""",
        (today_iso(), subject),
    )


def stage_kps(stage):
    return q("SELECT * FROM kp WHERE stage=? ORDER BY module, id", (stage,))


def module_stats(stage):
    rows = q(
        "SELECT module, COUNT(*) n, AVG(mastery) avgm FROM kp WHERE stage=? GROUP BY module ORDER BY avgm",
        (stage,),
    )
    out = []
    for r in rows:
        label, cls = mastery_label(r["avgm"])
        out.append({"module": r["module"], "n": r["n"], "avg": round(r["avgm"], 1), "label": label, "cls": cls})
    return out


def weak_kps(stage, n=6):
    return q(
        """SELECT k.* FROM kp k WHERE k.stage=? AND EXISTS (
               SELECT 1 FROM questions qq WHERE qq.kp_id=k.id)
           ORDER BY k.mastery ASC, k.attempts DESC LIMIT ?""",
        (stage, n),
    )


def pick_questions(stage, kp_ids, count):
    if kp_ids:
        marks = ",".join("?" * len(kp_ids))
        rows = q(
            f"""SELECT qq.*, k.module, k.name FROM questions qq JOIN kp k ON qq.kp_id=k.id
                WHERE k.stage=? AND qq.kp_id IN ({marks})""",
            (stage, *kp_ids),
        )
    else:
        rows = q(
            """SELECT qq.*, k.module, k.name FROM questions qq JOIN kp k ON qq.kp_id=k.id
               WHERE k.stage=?""",
            (stage,),
        )
    buckets = {}
    for r in rows:
        buckets.setdefault(r["module"], []).append(r)
    for lst in buckets.values():
        random.shuffle(lst)
    picked = []
    mods = list(buckets.keys())
    while len(picked) < count and any(buckets.values()):
        for mod in mods:
            lst = buckets[mod]
            if lst and len(picked) < count:
                picked.append(lst.pop())
    return picked


def overall(stage):
    row = q1("SELECT AVG(mastery) a FROM kp WHERE stage=?", (stage,))
    return round(row["a"], 1) if row and row["a"] is not None else 50.0


def score_rows():
    return q("SELECT * FROM scores WHERE subject='math' ORDER BY date DESC, id DESC")


def cause_distribution(stage):
    rows = q(
        """SELECT m.cause, COUNT(*) c FROM mistakes m JOIN kp ON m.kp_id=kp.id
           WHERE kp.stage=? GROUP BY m.cause ORDER BY c DESC""",
        (stage,),
    )
    total = sum(r["c"] for r in rows)
    out = [{"cause": r["cause"], "c": r["c"], "pct": round(r["c"] * 100 / total) if total else 0} for r in rows]
    return out, total


def days_to_exam():
    d = cfg("exam_date")
    if not d:
        return None
    try:
        return (date.fromisoformat(d) - date.today()).days
    except ValueError:
        return None


def build_suggestions(stage):
    tips = []
    due = due_mistakes("math")
    if due:
        tips.append(("复习", f"今天有 {len(due)} 道错题到期，先完成复习（间隔重复最忌拖延）。", "review"))
    else:
        tips.append(("复习", "今日无到期错题，复习任务已完成，保持节奏。", "review"))
    weak = weak_kps(stage, 3)
    names = "、".join(w["name"] for w in weak)
    tips.append(("练习", f"当前最薄弱知识点：{names}。建议今日针对性练习 5~10 题。", "practice"))
    dist, total = cause_distribution(stage)
    if total >= 5 and dist:
        top = dist[0]
        if top["pct"] >= 30:
            tips.append(("错因", f"你的错题中「{top['cause']}」占 {top['pct']}%。{CAUSE_ADVICE.get(top['cause'], '')}", "report"))
    days = days_to_exam()
    if days is not None:
        if days > 0:
            tips.append(("计划", f"距考试还有 {days} 天，按学习计划的当日任务执行。", "plan"))
        else:
            tips.append(("计划", "考试已结束或就在今天，及时录入成绩并复盘。", "scores"))
    logs = q1("SELECT COUNT(*) c FROM practice_log WHERE created LIKE ?", (today_iso() + "%",))["c"]
    if logs == 0:
        tips.append(("打卡", "今天还没有练习记录，完成一组练习开启今日打卡。", "practice"))
    think_n = q1("SELECT COUNT(*) c FROM thinking_log WHERE created LIKE ?", (today_iso() + "%",))["c"]
    if think_n == 0:
        tips.append(("思维", "今日思维训练未做：一道门萨式推理题，保持大脑“逻辑肌肉”。", "thinking"))
    return tips


@app.before_request
def auth():
    allow = request.path == "/login" or request.path.startswith("/static")
    if request.path == "/weekly" and request.args.get("k", "") == weekly_token():
        allow = True
    if allow:
        return None
    if not session.get("ok"):
        return redirect(url_for_login())
    snap_today()
    return None


def url_for_login():
    root = (request.script_root + "/") if request.script_root else "/"
    return root + "login"


_TAB_BY_PATH = {"dashboard": "hub", "practice": "practice", "quiz": "practice", "mistakes": "mistakes",
                "solve": "mistakes", "review": "review", "mastery": "mastery", "scores": "scores",
                "plan": "plan", "report": "report", "draw": "draw"}


def subject_shell():
    root = (request.script_root + "/") if request.script_root else "/"
    seg = request.path.strip("/").split("/")
    if not seg[0] or (seg[0] not in _TAB_BY_PATH and seg[0] != "subject"):
        return None
    if seg[0] == "subject":
        if len(seg) < 2 or seg[1] not in seed_data.SUBJECTS:
            return None
        code = seg[1]
        tab = seg[2] if len(seg) > 2 else "hub"
    else:
        code = request.args.get("subject", "math")
        if code not in seed_data.SUBJECTS:
            code = "math"
        tab = _TAB_BY_PATH.get(seg[0], "hub")
    if code == "math":
        tabs = [
            ("hub", "总览", root + "dashboard"),
            ("practice", "练习", root + "practice"),
            ("mistakes", "错题本", root + "mistakes?subject=math"),
            ("review", "复习", root + "review?subject=math"),
            ("mastery", "掌握度", root + "mastery"),
            ("skills", "答题技巧", root + "subject/math/skills"),
            ("draw", "作图", root + "draw"),
            ("scores", "成绩", root + "scores"),
            ("plan", "计划", root + "plan"),
            ("report", "报告", root + "report"),
        ]
    else:
        base = root + "subject/" + code
        tabs = [
            ("hub", "总览", base),
            ("practice", "练习", base + "/practice"),
            ("mistakes", "错题本", root + "mistakes?subject=" + code),
            ("review", "复习", root + "review?subject=" + code),
            ("mastery", "掌握度", base + "/mastery"),
            ("skills", "答题技巧", base + "/skills"),
            ("scores", "成绩", base + "/scores"),
            ("report", "报告", base + "/report"),
        ]
    return {"code": code, "meta": seed_data.SUBJECTS[code], "tab": tab, "tabs": tabs}


def rel(path):
    segs = [s for s in request.path.strip("/").split("/") if s]
    ups = max(len(segs) - 1, 0)
    if not ups:
        return path if path else "./"
    return ("../" * ups) + (path if path else "")


@app.context_processor
def inject_common():
    stage = cfg("stage", "cj")
    segs = [s for s in request.path.strip("/").split("/") if s]
    return {
        "stage": stage,
        "stage_name": seed_data.STAGES.get(stage, stage),
        "nav": segs[0] if segs else "home",
        "exam_days": days_to_exam(),
        "mastery_label": mastery_label,
        "plainify": plainify,
        "today": today_iso(),
        "now": now_iso(),
        "site_name": "黄曼清的AI全科学习系统",
        "student_name": cfg("name", "黄曼清"),
        "region": cfg("region", "江苏南京"),
        "hobbies": cfg("hobbies", "马术、赛艇、钢琴"),
        "subjects": seed_data.SUBJECTS,
        "kind_labels": seed_data.ASSESS_KIND_LABEL,
        "sb": subject_shell(),
        "R": (request.script_root + "/") if request.script_root else "/",
        "home_url": (request.script_root + "/") if request.script_root else "/",
        "cur_subject": (subject_shell() or {}).get("code", "") if request.path != "/login" else "",
        "xp_lv": None if request.path == "/login" else xp_level(xp_total()),
        "streak_n": None if request.path == "/login" else streak_days(),
        "pony_name": cfg("pony_name", "奶糖"),
        "xp_today": (q1("SELECT COALESCE(SUM(points),0) s FROM xp_log WHERE created LIKE ?", (today_iso() + "%",))["s"] or 0) if request.path != "/login" else 0,
    }


LEVEL_TITLES = ["见习骑手", "小骑士", "青铜骑士", "白银骑士", "黄金骑士", "铂金骑手",
                "钻石骑手", "大师骑手", "宗师骑手", "传奇骑手"]

PONY_STAGES = [
    (0, "🐴", "小马驹"),
    (3, "🐎", "活力小马"),
    (5, "🐎✨", "银鞍骏马"),
    (7, "🌟🐎", "星辉千里马"),
    (10, "🦄", "传奇独角兽"),
]

STORY_THEMES = {
    "regatta": {"name": "赛艇冠军之路", "icon": "🚣", "c1": "#2396b5", "c2": "#64c4da",
                "desc": "风浪、对手与默契——用数学与逻辑赢下每一桨"},
    "equest": {"name": "马术学院之谜", "icon": "🏇", "c1": "#a9742f", "c2": "#d0a26c",
               "desc": "老马场的秘密契约，解开谜题保住心爱的小马"},
    "piano": {"name": "月光钢琴城堡", "icon": "🎹", "c1": "#6d5ae0", "c2": "#9a7bff",
              "desc": "每一段乐章藏着一道谜题，唤醒沉睡的音乐厅"},
    "campus": {"name": "校园特工队", "icon": "🕵", "c1": "#e0457a", "c2": "#f284ab",
               "desc": "全科知识就是你的装备，完成特工任务"},
}


def story_state():
    try:
        return json.loads(cfg("story_state", "{}"))
    except Exception:
        return {}


def level_threshold(n):
    return 100 * n * (n + 1) // 2


def xp_level(xp):
    lv = 0
    n = 1
    while n <= 30 and xp >= level_threshold(n):
        lv = n
        n += 1
    cur = level_threshold(lv)
    nxt = level_threshold(lv + 1)
    pct = int((xp - cur) * 100 / (nxt - cur)) if nxt > cur else 0
    title = LEVEL_TITLES[min(lv, len(LEVEL_TITLES) - 1)]
    return lv, title, pct, nxt - xp


def xp_total():
    return int(q1("SELECT COALESCE(SUM(points),0) s FROM xp_log")["s"])


def streak_days():
    days = {r["d"] for r in q("SELECT DISTINCT substr(created,1,10) d FROM xp_log")}
    n = 0
    d = date.today()
    if today_iso() not in days:
        d = d - timedelta(days=1)
    while d.isoformat() in days:
        n += 1
        d = d - timedelta(days=1)
    return n


def add_xp(points, reason=""):
    run("INSERT INTO xp_log (points, reason, created) VALUES (?,?,?)", (points, reason[:60], now_iso()))
    lv, _, _, _ = xp_level(xp_total())
    old = int(cfg("last_level", "0") or 0)
    leveled = lv > old
    if leveled:
        set_cfg("last_level", str(lv))
    return leveled, lv


def pony_stage(lv):
    icon, name = PONY_STAGES[0][1], PONY_STAGES[0][2]
    for minlv, ic, nm in PONY_STAGES:
        if lv >= minlv:
            icon, name = ic, nm
    return icon, name


def textbooks():
    try:
        return json.loads(cfg("textbooks", "{}"))
    except Exception:
        return {k: v[0] for k, v in seed_data.TEXTBOOKS.items()}


def textbook_of(subject):
    return textbooks().get(subject) or ""


def snap_today():
    if cfg("last_snap") == today_iso():
        return
    for code in seed_data.SUBJECTS:
        if code == "math":
            avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject='math' AND stage=?", (cfg("stage", "cj"),))["a"]
        else:
            avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject=?", (code,))["a"]
        if avg is None:
            continue
        if not q1("SELECT id FROM subject_snap WHERE day=? AND subject=?", (today_iso(), code)):
            run("INSERT INTO subject_snap (day, subject, avg) VALUES (?,?,?)", (today_iso(), code, round(avg, 1)))
    set_cfg("last_snap", today_iso())


def profile_text():
    tb = textbook_of("math")
    return (f"学生：{cfg('name', '黄曼清')}，{cfg('region', '江苏南京')}，{cfg('grade', '初二')}"
            f"（江苏教材体系{('，数学' + tb) if tb else ''}）；兴趣特长：{cfg('hobbies', '马术、赛艇、钢琴')}；"
            f"性格特点：自信开朗、气质出众、爱运动爱艺术。")


@app.route("/")
def home():
    stage = cfg("stage", "cj")
    cards = []
    for code, meta in seed_data.SUBJECTS.items():
        if code == "math":
            avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject='math' AND stage=?", (stage,))["a"]
            mistakes_n = q1("SELECT COUNT(*) c FROM mistakes WHERE subject='math'")["c"]
            latest = q1("SELECT report FROM assessments WHERE subject='math' AND done=1 ORDER BY id DESC LIMIT 1")
        else:
            avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject=?", (code,))["a"]
            mistakes_n = q1("SELECT COUNT(*) c FROM mistakes WHERE subject=?", (code,))["c"]
            latest = q1("SELECT report FROM assessments WHERE subject=? AND done=1 ORDER BY id DESC LIMIT 1", (code,))
        due_n = q1("""SELECT COUNT(*) c FROM mistakes m JOIN kp ON m.kp_id=kp.id
                      WHERE m.status='active' AND m.next_review<=? AND kp.subject=?""", (today_iso(), code))["c"]
        cards.append({
            "code": code, "name": meta["name"], "tag": meta["tag"],
            "c1": meta["c1"], "c2": meta["c2"], "icon": meta["icon"],
            "avg": round(avg, 1) if avg is not None else None,
            "label": mastery_label(avg)[0] if avg is not None else "未开始",
            "cls": mastery_label(avg)[1] if avg is not None else "",
            "mistakes_n": mistakes_n, "due_n": due_n,
            "assessed": bool(latest and latest["report"]),
        })
    due_total = sum(c["due_n"] for c in cards)
    assess_n = q1("SELECT COUNT(*) c FROM assessments WHERE done=1")["c"]
    practice_today = q1("SELECT COUNT(*) c FROM practice_log WHERE created LIKE ?", (today_iso() + "%",))["c"] > 0
    thinking_today = q1("SELECT COUNT(*) c FROM thinking_log WHERE created LIKE ?", (today_iso() + "%",))["c"] > 0
    return render_template("home.html", cards=cards, due_total=due_total, assess_n=assess_n,
                           practice_today=practice_today, thinking_today=thinking_today)


@app.route("/dashboard")
@app.route("/subject/math")
def dashboard():
    stage = cfg("stage", "cj")
    overall_m = overall(stage)
    label, cls = mastery_label(overall_m)
    due = due_mistakes("math")
    mistakes_n = q1("SELECT COUNT(*) c FROM mistakes m JOIN kp ON m.kp_id=kp.id WHERE kp.subject='math' AND kp.stage=?", (stage,))["c"]
    practice_n = q1("SELECT COUNT(*) c FROM practice_log")["c"]
    weak = weak_kps(stage, 5)
    think_done = q1("SELECT COUNT(*) c FROM thinking_log WHERE created LIKE ?", (today_iso() + "%",))["c"] > 0
    scores = list(reversed(q("SELECT * FROM scores WHERE subject='math' ORDER BY date DESC, id DESC LIMIT 8")))
    mods = module_stats(stage)
    tips = build_suggestions(stage)
    trend = None
    if len(scores) >= 2:
        pts = [round(s["score"] * 100 / (s["full"] or 100)) for s in scores]
        trend = pts[-1] - pts[0]
    return render_template(
        "dashboard.html",
        overall_m=overall_m, label=label, cls=cls,
        due_n=len(due), mistakes_n=mistakes_n, practice_n=practice_n,
        weak=weak, mods=mods, tips=tips, scores=scores, trend=trend,
        think_done=think_done,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    pw_set = bool(cfg("pw"))
    error = None
    if request.method == "POST":
        pw = request.form.get("pw", "")
        if not pw_set:
            if len(pw) < 4:
                error = "密码至少 4 位"
            else:
                set_cfg("pw", generate_password_hash(pw))
                session["ok"] = True
                root = (request.script_root + "/") if request.script_root else "/"
                return redirect(root)
        else:
            if check_password_hash(cfg("pw"), pw):
                session["ok"] = True
                root = (request.script_root + "/") if request.script_root else "/"
                return redirect(root)
            error = "密码不正确"
    return render_template("login.html", pw_set=pw_set, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("login")


@app.route("/mistakes")
def mistakes():
    stage = cfg("stage", "cj")
    subject = request.args.get("subject", "math")
    if subject not in seed_data.SUBJECTS:
        subject = "math"
    status = request.args.get("status", "active")
    cause = request.args.get("cause", "")
    if subject == "math":
        conds, args = ["kp.stage=?", "kp.subject='math'"], [stage]
    else:
        conds, args = ["kp.subject=?"], [subject]
    if status in ("active", "mastered", "all"):
        if status != "all":
            conds.append("m.status=?")
            args.append(status)
    rows = q(
        f"""SELECT m.*, kp.name kp_name, kp.module FROM mistakes m JOIN kp ON m.kp_id=kp.id
            WHERE {' AND '.join(conds)} ORDER BY m.id DESC""",
        args,
    )
    if cause:
        rows = [r for r in rows if r["cause"] == cause]
    if subject == "math":
        kps = stage_kps(stage)
    else:
        kps = q("SELECT * FROM kp WHERE subject=? ORDER BY id", (subject,))
    return render_template("mistakes.html", rows=rows, kps=kps, causes=CAUSES,
                           logic_types=LOGIC_TYPES, prefill_photo=request.args.get("photo", ""),
                           status=status, cause=cause, stage_name=seed_data.STAGES.get(stage, stage))


def save_photo(filestor):
    if not filestor or not filestor.filename:
        return ""
    ext = os.path.splitext(filestor.filename)[1].lower()
    if ext not in PHOTO_EXTS:
        return ""
    os.makedirs(PHOTO_DIR, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(6) + ext
    filestor.save(os.path.join(PHOTO_DIR, name))
    return name


def valid_photo_name(name):
    base = os.path.basename(name or "")
    if not re.fullmatch(r"[\w.-]+\.(jpg|jpeg|png|webp|gif|bmp)", base, re.I):
        return ""
    if os.path.exists(os.path.join(PHOTO_DIR, base)):
        return base
    return ""


def kp_subject(kp_id):
    r = q1("SELECT subject FROM kp WHERE id=?", (kp_id,))
    return r["subject"] if r else "math"


@app.route("/save_mistake", methods=["POST"])
def save_mistake():
    kp_id = request.form.get("kp_id", type=int)
    title = request.form.get("title", "").strip()
    answer = request.form.get("answer", "").strip()
    correction = request.form.get("correction", "").strip()
    cause = request.form.get("cause", "其他")
    logic_type = request.form.get("logic_type", "").strip()
    if cause != "逻辑思维":
        logic_type = ""
    source = request.form.get("source", "").strip()
    diff = request.form.get("diff", 2, type=int)
    if not kp_id or not title:
        abort(400)
    photo = save_photo(request.files.get("photo")) or valid_photo_name(request.form.get("photo_name", ""))
    subject = kp_subject(kp_id)
    run(
        """INSERT INTO mistakes (kp_id, title, answer, correction, cause, logic_type, source, diff, photo, subject, created, next_review)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (kp_id, title, answer, correction, cause, logic_type, source, diff, photo, subject, now_iso(), today_iso()),
    )
    apply_penalty(kp_id, cause)
    mid = q1("SELECT last_insert_rowid() id")["id"]
    leveled, lv = add_xp(5, "录入错题")
    return redirect("solve?id=" + str(mid) + "&gx=5" + (f"&lv={lv}" if leveled else ""))


@app.route("/solve")
def solve():
    mid = request.args.get("id", type=int)
    m = q1("""SELECT m.*, kp.name kp_name, kp.module, kp.stage FROM mistakes m JOIN kp ON m.kp_id=kp.id WHERE m.id=?""", (mid,))
    if not m:
        abort(404)
    return render_template("solve.html", m=m, stage_name=seed_data.STAGES.get(m["stage"], m["stage"]))


@app.route("/ai_solve", methods=["POST"])
def ai_solve():
    mid = request.form.get("id", type=int)
    m = q1("""SELECT m.*, kp.name kp_name, kp.module, kp.stage FROM mistakes m JOIN kp ON m.kp_id=kp.id WHERE m.id=?""", (mid,))
    if not m:
        return jsonify({"ok": False, "msg": "错题不存在"}), 404
    stage_name = seed_data.STAGES.get(m["stage"], m["stage"])
    grade = cfg("grade", "初二")
    msubj = m["subject"] or "math"
    result, err = ai_solve_mistake(m, m["kp_name"], grade, stage_name)
    if err:
        return jsonify({"ok": False, "msg": "AI 解答失败：" + err + "，可稍后重试"})
    fig = parse_figure_json(result.get("figjson", "")) if msubj != "chinese" else None
    run("UPDATE mistakes SET ai_answer=?, ai_analysis=?, ai_advice=?, ai_figure=? WHERE id=?",
        (_fmt_ai_answer(result, chinese=(msubj == "chinese")), result["analysis"], result["advice"],
         json.dumps(fig, ensure_ascii=False) if fig else "", mid))
    result["figure"] = fig
    return jsonify({"ok": True, **result})


@app.route("/ai_solve_form", methods=["POST"])
def ai_solve_form():
    mid = request.form.get("id", type=int)
    if not mid or not q1("SELECT id FROM mistakes WHERE id=?", (mid,)):
        abort(404)
    m = q1("""SELECT m.*, kp.name kp_name, kp.module, kp.stage FROM mistakes m JOIN kp ON m.kp_id=kp.id WHERE m.id=?""", (mid,))
    stage_name = seed_data.STAGES.get(m["stage"], m["stage"])
    grade = cfg("grade", "初二")
    msubj = m["subject"] or "math"
    result, err = ai_solve_mistake(m, m["kp_name"], grade, stage_name)
    if not err:
        fig = parse_figure_json(result.get("figjson", "")) if msubj != "chinese" else None
        run("UPDATE mistakes SET ai_answer=?, ai_analysis=?, ai_advice=?, ai_figure=? WHERE id=?",
            (_fmt_ai_answer(result, chinese=(msubj == "chinese")), result["analysis"], result["advice"],
             json.dumps(fig, ensure_ascii=False) if fig else "", mid))
    return redirect("solve?id=" + str(mid))


def _shrink_for_vision(photo_path):
    tmp = os.path.join("/tmp", "hk02_shrink_" + secrets.token_hex(6) + ".jpg")
    try:
        from PIL import Image
        import io
        img = Image.open(photo_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) <= 1400 and os.path.getsize(photo_path) < 300 * 1024:
            return photo_path, None
        img.thumbnail((1400, 1400))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        with open(tmp, "wb") as f:
            f.write(buf.getvalue())
        return tmp, tmp
    except Exception:
        return photo_path, None


def vision_split(photo_path):
    path, tmp = _shrink_for_vision(photo_path)
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        prompt = (
            "你是专业的试卷识别助手。请把图片内容按三类分开转录：\n"
            "1) original——印刷体文字：文章原文、题目题干（黑色印刷为主）；\n"
            "2) mine——学生手写作答（蓝色或黑色手写笔迹，含草稿演算）；\n"
            "3) correction——正确答案：①红色或其他颜色笔手写的订正/批改；"
            "②也可能是与题目相同印刷字体的“标准答案”（答案栏/答案页/教师用书样式）——同样归入此类，并注明“（印刷体标准答案）”。\n"
            '只输出一个JSON：{"original":"...","mine":"...","correction":"..."}，'
            "没有的类别留空字符串；每类文字按阅读顺序排列，换行用\\n；"
            "文章较长的请完整转录不要省略；数学式尽量按原样保留（分数写为 a/b，根号写为√）；识别不确定的字用？标注。"
        )
        last_err = None
        for attempt in range(2):
            content, err = ai_chat([
                {"role": "system", "content": "你是一个精确的OCR转录引擎，只输出JSON。"},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                ]},
            ], max_tokens=3500)
            if err:
                last_err = err
                continue
            if not content or not content.strip():
                last_err = "模型返回空内容"
                continue
            try:
                text = content.strip()
                text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
                start, end = text.find("{"), text.rfind("}")
                obj = json.loads(text[start:end + 1])
                parts = {k: str(obj.get(k, "")).strip() for k in ("original", "mine", "correction")}
                if parts["original"] or parts["mine"] or parts["correction"]:
                    return parts, None
                last_err = "三区内容均为空"
            except Exception:
                last_err = "视觉识别返回异常"
        return None, last_err
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def ocr_plain(photo_path):
    engine = get_ocr()
    if engine is None:
        return ""
    result, _ = engine(photo_path)
    if not result:
        return ""
    return "\n".join(r[1] for r in result)


@app.route("/ocr", methods=["POST"])
def ocr():
    f = request.files.get("photo")
    if not f or not f.filename:
        return jsonify({"ok": False, "msg": "未收到图片"})
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in PHOTO_EXTS:
        return jsonify({"ok": False, "msg": "不支持的图片格式"})
    name = save_photo(f)
    if not name:
        return jsonify({"ok": False, "msg": "图片保存失败"})
    path = os.path.join(PHOTO_DIR, name)
    parts, err = vision_split(path)
    if parts:
        return jsonify({"ok": True, "mode": "vision", "photo": name, **parts})
    if err:
        print(f"[ocr] vision failed, fallback to plain: {err}", flush=True)
    fallback = ocr_plain(path)
    if fallback:
        return jsonify({"ok": True, "mode": "plain", "photo": name,
                        "original": fallback, "mine": "", "correction": "",
                        "msg": "视觉分区识别暂不可用，已整体识别，请手动划分原题/作答/订正"})
    return jsonify({"ok": False, "photo": name, "msg": "未识别到文字，请手动输入（原图已保存）"})


@app.route("/draw")
def draw():
    return render_template("draw.html")


@app.route("/save_drawing", methods=["POST"])
def save_drawing():
    data = request.get_json(silent=True) or {}
    image = data.get("image", "")
    m = re.match(r"^data:image/(png|jpeg);base64,(.+)$", image, re.S)
    if not m:
        return jsonify({"ok": False, "msg": "图片数据无效"})
    ext = "png" if m.group(1) == "png" else "jpg"
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return jsonify({"ok": False, "msg": "图片解码失败"})
    if len(raw) > 10 * 1024 * 1024:
        return jsonify({"ok": False, "msg": "图片过大"})
    os.makedirs(PHOTO_DIR, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d%H%M%S") + "_draw_" + secrets.token_hex(6) + "." + ext
    with open(os.path.join(PHOTO_DIR, name), "wb") as f:
        f.write(raw)
    return jsonify({"ok": True, "photo": name})


@app.route("/photo/<path:name>")
def photo(name):
    return send_from_directory(PHOTO_DIR, name)


@app.route("/delete_mistake", methods=["POST"])
def delete_mistake():
    mid = request.form.get("id", type=int)
    if mid:
        run("DELETE FROM mistakes WHERE id=?", (mid,))
    return redirect("mistakes")


@app.route("/review")
def review():
    subject = request.args.get("subject", "math")
    if subject not in seed_data.SUBJECTS:
        subject = "math"
    due = due_mistakes(subject)
    item = due[0] if due else None
    done_today = q1("SELECT COUNT(*) c FROM reviews_done WHERE created LIKE ?", (today_iso() + "%",))["c"]
    return render_template("review.html", item=item, left=len(due), done_today=done_today, subject=subject)


@app.route("/review_answer", methods=["POST"])
def review_answer():
    mid = request.form.get("id", type=int)
    result = request.form.get("result", type=float)
    m = q1("SELECT * FROM mistakes WHERE id=?", (mid,))
    if m and result in (0, 0.5, 1):
        sm2_update(m, result)
        update_mastery(m["kp_id"], result)
        leveled, lv = add_xp(8, "复习错题")
        url = "review?subject=" + kp_subject(m["kp_id"]) + "&gx=8" + (f"&lv={lv}" if leveled else "")
        return redirect(url)
    return redirect("review")


@app.route("/subject/<code>")
def subject_hub(code):
    if code == "math":
        return redirect("dashboard")
    if code not in seed_data.SUBJECTS:
        abort(404)
    meta = seed_data.SUBJECTS[code]
    boards = q("SELECT * FROM kp WHERE subject=? ORDER BY id", (code,))
    avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject=?", (code,))["a"]
    mistakes_n = q1("SELECT COUNT(*) c FROM mistakes WHERE subject=?", (code,))["c"]
    due_n = len(due_mistakes(code))
    rows = q(
        """SELECT m.*, kp.name kp_name, kp.module FROM mistakes m JOIN kp ON m.kp_id=kp.id
           WHERE kp.subject=? ORDER BY m.id DESC LIMIT 10""", (code,))
    latest = q1("SELECT * FROM assessments WHERE subject=? AND done=1 ORDER BY id DESC LIMIT 1", (code,))
    return render_template("hub.html", code=code, meta=meta, boards=boards,
                           avg=round(avg, 1) if avg is not None else None,
                           label=mastery_label(avg)[0] if avg is not None else "未开始",
                           cls=mastery_label(avg)[1] if avg is not None else "",
                           mistakes_n=mistakes_n, due_n=due_n, rows=rows, latest=latest)


@app.route("/subject/<code>/practice")
def subject_practice(code):
    if code == "math":
        return redirect("practice")
    if code not in seed_data.SUBJECTS:
        abort(404)
    meta = seed_data.SUBJECTS[code]
    history = q("SELECT * FROM assessments WHERE subject=? AND done=1 ORDER BY id DESC LIMIT 6", (code,))
    return render_template("subject_practice.html", code=code, meta=meta, history=history)


@app.route("/subject/<code>/mastery")
def subject_mastery(code):
    if code == "math":
        return redirect("mastery")
    if code not in seed_data.SUBJECTS:
        abort(404)
    meta = seed_data.SUBJECTS[code]
    boards = q("SELECT * FROM kp WHERE subject=? ORDER BY id", (code,))
    return render_template("subject_mastery.html", code=code, meta=meta, boards=boards)


@app.route("/subject/<code>/scores")
def subject_scores(code):
    if code == "math":
        return redirect("scores")
    if code not in seed_data.SUBJECTS:
        abort(404)
    meta = seed_data.SUBJECTS[code]
    rows = q("SELECT * FROM scores WHERE subject=? ORDER BY date DESC, id DESC", (code,))
    pts = []
    seq = list(reversed(rows))[-12:]
    for s in seq:
        pts.append({"date": s["date"], "kind": s["kind"] or "考试",
                    "pct": round(s["score"] * 100 / (s["full"] or 100), 1)})
    w, h, pad = 640, 220, 30
    chart = None
    if len(pts) >= 2:
        step = (w - 2 * pad) / (len(pts) - 1)
        coords = []
        for idx, p in enumerate(pts):
            x = round(pad + idx * step)
            y = round(h - pad - (p["pct"] / 100) * (h - 2 * pad))
            coords.append((x, y))
        chart = {"w": w, "h": h, "coords": coords, "labels": [p["date"][5:] for p in pts]}
    return render_template("subject_scores.html", code=code, meta=meta, rows=rows, pts=pts, chart=chart)


@app.route("/subject/<code>/report")
def subject_report(code):
    if code == "math":
        return redirect("report")
    if code not in seed_data.SUBJECTS:
        abort(404)
    meta = seed_data.SUBJECTS[code]
    boards = q("SELECT * FROM kp WHERE subject=? ORDER BY id", (code,))
    avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject=?", (code,))["a"]
    rows = q("""SELECT m.*, kp.name kp_name FROM mistakes m JOIN kp ON m.kp_id=kp.id
                WHERE kp.subject=? ORDER BY m.id DESC LIMIT 8""", (code,))
    dist, total = cause_distribution(code)
    scores = q("SELECT * FROM scores WHERE subject=? ORDER BY date DESC, id DESC LIMIT 6", (code,))
    latest = q1("SELECT * FROM subject_reports WHERE subject=? ORDER BY id DESC LIMIT 1", (code,))
    latest_assess = q1("SELECT * FROM assessments WHERE subject=? AND done=1 ORDER BY id DESC LIMIT 1", (code,))
    return render_template("subject_report.html", code=code, meta=meta, boards=boards,
                           avg=round(avg, 1) if avg is not None else None,
                           label=mastery_label(avg)[0] if avg is not None else "未开始",
                           cls=mastery_label(avg)[1] if avg is not None else "",
                           rows=rows, dist=dist, total=total, scores=scores,
                           latest=latest, latest_assess=latest_assess,
                           ai_error=request.args.get("err", ""))


@app.route("/subject/<code>/gen_report", methods=["POST"])
def subject_gen_report(code):
    if code not in seed_data.SUBJECTS:
        abort(404)
    meta = seed_data.SUBJECTS[code]
    boards = q("SELECT name, mastery, attempts FROM kp WHERE subject=? ORDER BY id", (code,))
    dist, total = cause_distribution(code)
    scores = q("SELECT date, kind, score, full FROM scores WHERE subject=? ORDER BY date DESC LIMIT 6", (code,))
    assess = q("SELECT kind, created, report FROM assessments WHERE subject=? AND done=1 ORDER BY id DESC LIMIT 2", (code,))
    board_txt = "；".join(f"{b['name']}掌握度{b['mastery']:.0f}（自评/练习{b['attempts']}次）" for b in boards)
    cause_txt = "；".join(f"{d['cause']}{d['c']}次" for d in dist) or "暂无"
    score_txt = "；".join(f"{s['date']}{s['kind']}{s['score']:g}/{s['full']:g}" for s in scores) or "暂无"
    assess_txt = "\n".join(f"- {seed_data.ASSESS_KIND_LABEL.get(a['kind'], a['kind'])}（{a['created'][:10]}）：{a['report'][:300]}" for a in assess) or "暂无"
    ask = (
        f"{profile_text()}\n科目：【{meta['name']}】（教材版本：{textbook_of(code)}）\n\n"
        f"板块掌握度：{board_txt}\n错因分布：{cause_txt}\n近期成绩：{score_txt}\n"
        f"近期测评结论：\n{assess_txt}\n\n"
        "请严格按以下小节输出，直接以方括号标题开头：\n"
        "【总体判断】结合数据与她的个人特点，评价当前该科学习状态（3~4 句）\n"
        "【优势亮点】2~3 条（结合作答证据与她的兴趣特长）\n"
        "【问题诊断】2~3 条（指向具体板块/错因，说明可能的根源）\n"
        "【四周提升方案】按周列出行动要点，把内容与她的兴趣场景结合，具体可执行\n"
        "【本周三件事】立刻能做的三件小事"
    )
    content, err = ai_chat([
        {"role": "system", "content": "你是因材施教的教育分析师，评语具体真诚，善于用数据说话。"},
        {"role": "user", "content": ask},
    ], max_tokens=3000)
    if err:
        return redirect(f"../{code}/report?err=" + err[:80])
    run("INSERT INTO subject_reports (subject, report, created) VALUES (?,?,?)",
        (code, content.strip(), now_iso()))
    return redirect(f"../{code}/report")


@app.route("/subject/<code>/skills")
def subject_skills(code):
    if code not in seed_data.SUBJECTS:
        abort(404)
    meta = seed_data.SUBJECTS[code]
    groups = {}
    for cat, title, body in seed_data.SKILLS.get(code, []):
        groups.setdefault(cat, []).append({"title": title, "body": body})
    return render_template("skills.html", code=code, meta=meta, groups=groups)


@app.route("/story")
def story_page():
    st = story_state()
    themes = []
    for code, meta in STORY_THEMES.items():
        entry = st.get(code, {})
        themes.append({**meta, "code": code, "chapter": entry.get("chapter", 0),
                       "summary": entry.get("summary", "")})
    total_ch = sum(t["chapter"] for t in themes)
    return render_template("story.html", themes=themes, total_ch=total_ch)


@app.route("/story_start", methods=["POST"])
def story_start():
    theme = request.form.get("theme", "")
    if theme not in STORY_THEMES:
        abort(400)
    meta = STORY_THEMES[theme]
    st = story_state()
    entry = st.get(theme, {})
    ch = entry.get("chapter", 0) + 1
    summary = entry.get("summary", "")
    ask = (
        f"{profile_text()}\n请为她创作互动学习冒险《{meta['name']}》第 {ch} 章。\n"
        + (f"上一章结局梗概（要衔接）：{summary}\n" if summary else "这是第一章：交代背景、目标与悬念钩子。\n")
        + "输出结构：剧情引子（80~120字，扣人心弦，场景结合她的兴趣）→ 3 道由剧情自然引出的题目"
        "（覆盖数学/逻辑推理/学科常识，难度中等，题干带剧情情境）→ 每题参考答案。\n"
        '只输出JSON：{"title":"章节标题","intro":"剧情引子","questions":[{"text":"题干","ref":"参考答案"}×3]}'
    )
    content, err = ai_chat([
        {"role": "system", "content": "你是青少年互动小说作家兼命题专家，剧情生动、题目巧妙。只输出JSON。"},
        {"role": "user", "content": ask},
    ], max_tokens=3500)
    qs, title, intro = None, "", ""
    if not err and content:
        try:
            text = re.sub(r"^```(json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
            start, end = text.find("{"), text.rfind("}")
            obj = json.loads(text[start:end + 1])
            title = str(obj.get("title", f"第{ch}章"))
            intro = str(obj.get("intro", ""))
            qs = [{"text": str(it.get("text", "")), "ref": str(it.get("ref", "")), "board": "剧情"}
                  for it in obj.get("questions", []) if it.get("text")][:5]
        except Exception:
            qs = None
    if not qs or len(qs) < 2:
        return render_template("story.html", themes=[], total_ch=0,
                               error="剧情生成失败（AI 繁忙），稍后再试一次")
    run("INSERT INTO assessments (subject, kind, questions, answers, created) VALUES (?,?,?,?,?)",
        ("story:" + theme, "story", json.dumps(qs, ensure_ascii=False),
         json.dumps({"title": title, "intro": intro}, ensure_ascii=False), now_iso()))
    aid = q1("SELECT last_insert_rowid() id")["id"]
    return redirect("ceping_do?id=" + str(aid))


def badges_data():
    xpn = xp_total()
    lv = xp_level(xpn)[0]
    stk = streak_days()
    rows = []

    def cnt(sql, *a):
        return q1(sql, a)["c"] or 0
    items = [
        ("🌱", "初试身手", "完成首次 AI 摸底", cnt("SELECT COUNT(*) c FROM assessments WHERE done=1"), 1),
        ("📖", "错题猎人", "累计录入 10 道错题", cnt("SELECT COUNT(*) c FROM mistakes"), 10),
        ("🔥", "三日之约", "连续学习 3 天", stk, 3),
        ("🔥", "七连胜火", "连续学习 7 天", stk, 7),
        ("🧠", "思维大师", "思维训练累计 50 题", cnt("SELECT COUNT(*) c FROM thinking_log"), 50),
        ("♻️", "复习达人", "完成 100 次错题复习", cnt("SELECT COUNT(*) c FROM reviews_done"), 100),
        ("🏹", "百步穿杨", "智能练习累计 100 题次", cnt("SELECT COUNT(*) c FROM practice_log"), 100),
        ("🏇", "小马成长", "等级达到 5 级", lv, 5),
        ("🦄", "传奇骑手", "等级达到 10 级", lv, 10),
        ("🗺", "冒险小说家", "剧情冒险完成 5 章", sum(t.get("chapter", 0) for t in story_state().values()), 5),
        ("⚡", "极速挑战者", "Boss 战单局答对 ≥15 题", cnt("SELECT COALESCE(MAX(score),0) c FROM challenge_log"), 15),
        ("🧰", "工具达人", "知识卡片自测 60 次", cnt("SELECT COUNT(*) c FROM tools_log"), 60),
    ]
    for icon, name, desc, cur, target in items:
        rows.append({"icon": icon, "name": name, "desc": desc, "cur": min(cur, target),
                     "target": target, "done": cur >= target,
                     "pct": min(100, int(cur * 100 / target)) if target else 100})
    return rows


@app.route("/badges")
def badges():
    rows = badges_data()
    return render_template("badges.html", rows=rows, got=sum(1 for r in rows if r["done"]))


@app.route("/challenge")
def challenge():
    best = q1("SELECT COALESCE(MAX(score),0) b FROM challenge_log")["b"]
    history = q("SELECT * FROM challenge_log ORDER BY id DESC LIMIT 8")
    return render_template("challenge.html", best=best, history=history,
                           conf=request.args.get("done", ""))


@app.route("/challenge_start", methods=["POST"])
def challenge_start():
    stage = cfg("stage", "cj")
    rows = q("""SELECT qq.id FROM questions qq JOIN kp ON qq.kp_id=kp.id
                WHERE kp.subject='math' AND kp.stage=?""", (stage,))
    ids = [r["id"] for r in rows]
    random.shuffle(ids)
    import time as _t
    session["ch_q"] = ids[:25]
    session["chi"] = 0
    session["ch_score"] = 0
    session["ch_end"] = _t.time() + 75
    return redirect("challenge_do")


@app.route("/challenge_do")
def challenge_do():
    import time as _t
    ids = session.get("ch_q") or []
    i = session.get("chi", 0)
    left = int(session.get("ch_end", 0) - _t.time())
    if not ids or i >= len(ids) or left <= 0:
        return redirect("challenge_finish")
    row = q1("""SELECT qq.*, k.name kp_name, k.module FROM questions qq JOIN kp k ON qq.kp_id=k.id WHERE qq.id=?""", (ids[i],))
    if not row:
        return redirect("challenge_finish")
    return render_template("challenge_do.html", item=row, i=i, n=len(ids),
                           left=left, score=session.get("ch_score", 0))


@app.route("/challenge_answer", methods=["POST"])
def challenge_answer():
    import time as _t
    result = request.form.get("result", type=float)
    if _t.time() < session.get("ch_end", 0) and result in (0, 1):
        if result == 1:
            session["ch_score"] = session.get("ch_score", 0) + 1
    session["chi"] = session.get("chi", 0) + 1
    return redirect("challenge_do")


@app.route("/challenge_finish")
def challenge_finish():
    score = session.get("ch_score", 0)
    if session.get("ch_q"):
        run("INSERT INTO challenge_log (score, total, created) VALUES (?,?,?)",
            (score, len(session.get("ch_q") or []), now_iso()))
        best = q1("SELECT COALESCE(MAX(score),0) b FROM challenge_log WHERE id < last_insert_rowid()")["b"]
        rec = score > best
        leveled, lv = add_xp(max(5, score * 5), "Boss战")
        session["ch_q"] = []
        return redirect("challenge?done=1&rec=" + ("1" if rec else "0") +
                        f"&gx={max(5, score * 5)}" + (f"&lv={lv}" if leveled else "") + f"&sc={score}")
    return redirect("challenge")


@app.route("/play")
def play():
    rows = badges_data()
    got = sum(1 for r in rows if r["done"])
    st = story_state()
    total_ch = sum(t.get("chapter", 0) for t in st.values())
    best = q1("SELECT COALESCE(MAX(score),0) b FROM challenge_log")["b"]
    think_n = q1("SELECT COUNT(*) c FROM thinking_log")["c"]
    think_today = q1("SELECT COUNT(*) c FROM thinking_log WHERE created LIKE ?", (today_iso() + "%",))["c"]
    lv_info = xp_level(xp_total())
    pony_icon, pony_nm = pony_stage(lv_info[0])
    return render_template("play.html", rows=rows, got=got, total_ch=total_ch, best=best,
                           think_n=think_n, think_today=think_today,
                           lv_info=lv_info, pony_icon=pony_icon, pony_nm=pony_nm)


def plainify(text):
    t = str(text or "")
    t = t.replace("\\n", "\n").replace("\\t", " ")
    t = re.sub(r"\\[\(\[]|\\[\)\]]", "", t)
    t = re.sub(r"\\(?:quad|text|mathrm|mathbf|begin\{[a-z*]+\}|end\{[a-z*]+\}|hline)", " ", t)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*[-*]\s+", "· ", t, flags=re.MULTILINE)
    t = re.sub(r"\$[^$]*\$", lambda m: m.group(0).strip("$"), t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


@app.route("/ceping_fill")
def ceping_fill():
    aid = request.args.get("id", type=int)
    a = q1("SELECT * FROM assessments WHERE id=?", (aid,))
    if not a or not a["done"]:
        abort(404)
    questions = json.loads(a["questions"])
    answers = json.loads(a["answers"])
    empty_idx = [i for i, x in enumerate(answers) if not str(x).strip()]
    if not empty_idx:
        return redirect("ceping_result?id=" + str(aid))
    story_meta = None
    if a["kind"] == "story":
        theme = a["subject"][6:] if a["subject"].startswith("story:") else ""
        story_meta = {"theme": STORY_THEMES.get(theme, {}), "code": theme}
    return render_template("fill.html", a=a, questions=questions, answers=answers,
                           empty_idx=empty_idx, story_meta=story_meta,
                           meta=seed_data.SUBJECTS.get(a["subject"], {}))


@app.route("/ceping_fill_submit", methods=["POST"])
def ceping_fill_submit():
    aid = request.form.get("id", type=int)
    a = q1("SELECT * FROM assessments WHERE id=?", (aid,))
    if not a or not a["done"]:
        abort(404)
    questions = json.loads(a["questions"])
    answers = json.loads(a["answers"])
    while len(answers) < len(questions):
        answers.append("")
    for i in range(len(questions)):
        v = request.form.get(f"a{i}", "").strip()
        if v:
            answers[i] = v
    report, err = ai_grade_assessment(a, answers)
    if err:
        return render_template("fill.html", a=a, questions=questions, answers=answers,
                               story_meta=None, meta=seed_data.SUBJECTS.get(a["subject"], {}),
                               error="AI 重新批改失败：" + err + "，请重试（你的作答已保留在表单中）")
    run("UPDATE assessments SET answers=?, report=? WHERE id=?",
        (json.dumps(answers, ensure_ascii=False), report, aid))
    leveled, lv = add_xp(15, "补答完成")
    return redirect("ceping_result?id=" + str(aid) + "&gx=15" + (f"&lv={lv}" if leveled else ""))


def weekly_token():
    with open(KEY_PATH) as f:
        key = f.read().strip()
    import hashlib
    return hashlib.sha256((key + "|weekly").encode()).hexdigest()[:20]


@app.route("/weekly")
def weekly():
    shared = request.args.get("k", "") == weekly_token()
    week7 = (date.today() - timedelta(days=7)).isoformat()
    like = today_iso()[:8] + "%"
    def cnt7(table, col="created"):
        return q1(f"SELECT COUNT(*) c FROM {table} WHERE {col} >= ?", (week7,))["c"]
    stats = {
        "practice": cnt7("practice_log"),
        "review": cnt7("reviews_done"),
        "mistakes": cnt7("mistakes"),
        "thinking": cnt7("thinking_log"),
        "assess": q1("SELECT COUNT(*) c FROM assessments WHERE done=1 AND created >= ?", (week7,))["c"],
    }
    subj_rows = []
    for code, meta in seed_data.SUBJECTS.items():
        if code == "math":
            avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject='math' AND stage=?", (cfg("stage", "cj"),))["a"]
        else:
            avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject=?", (code,))["a"]
        if avg is None:
            continue
        snap = q1("SELECT avg FROM subject_snap WHERE subject=? AND day<=? ORDER BY day DESC LIMIT 1",
                  (code, week7))
        delta = round(avg - snap["avg"], 1) if snap else None
        weak = q("SELECT name FROM kp WHERE subject=? ORDER BY mastery ASC LIMIT 1", (code,))
        subj_rows.append({"name": meta["name"], "icon": meta["icon"], "c1": meta["c1"], "c2": meta["c2"],
                          "avg": round(avg, 1), "delta": delta, "weak": weak[0]["name"] if weak else ""})
    subj_rows.sort(key=lambda r: -(r["delta"] or 0))
    scores = q("SELECT * FROM scores WHERE date >= ? ORDER BY date", (week7,))
    highlights, advice = [], []
    if stats["review"]:
        highlights.append(f"本周完成 {stats['review']} 次错题复习，间隔重复节奏保持得不错")
    if stats["thinking"]:
        highlights.append(f"完成 {stats['thinking']} 道思维训练题，逻辑肌肉持续在线")
    if any(r["delta"] and r["delta"] >= 2 for r in subj_rows):
        up = [f"{r['name']}(+{r['delta']})" for r in subj_rows if r["delta"] and r["delta"] >= 2]
        highlights.append("掌握度显著提升：" + "、".join(up))
    if stats["mistakes"]:
        advice.append(f"新增 {stats['mistakes']} 道错题，建议 48 小时内完成 AI 精讲与归因")
    if stats["practice"] < 20:
        advice.append("练习量还可以再加一点：每天一组（10 题）效果最佳")
    if not advice:
        advice.append("节奏很好，保持当前习惯即可")
    return render_template("weekly.html", stats=stats, subj_rows=subj_rows, scores=scores,
                           highlights=highlights, advice=advice, shared=shared,
                           token=weekly_token(), week7=week7)


@app.route("/weekly_ai", methods=["POST"])
def weekly_ai():
    data = []
    for code, meta in seed_data.SUBJECTS.items():
        if code == "math":
            avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject='math' AND stage=?", (cfg("stage", "cj"),))["a"]
        else:
            avg = q1("SELECT AVG(mastery) a FROM kp WHERE subject=?", (code,))["a"]
        if avg is not None:
            data.append(f"{meta['name']}掌握度{avg:.0f}")
    week7 = (date.today() - timedelta(days=7)).isoformat()
    n_pr = q1("SELECT COUNT(*) c FROM practice_log WHERE created >= ?", (week7,))["c"]
    n_rv = q1("SELECT COUNT(*) c FROM reviews_done WHERE created >= ?", (week7,))["c"]
    n_th = q1("SELECT COUNT(*) c FROM thinking_log WHERE created >= ?", (week7,))["c"]
    n_mk = q1("SELECT COUNT(*) c FROM mistakes WHERE created >= ?", (week7,))["c"]
    ask = (
        f"{profile_text()}\n本周数据：练习 {n_pr} 题、复习 {n_rv} 次、思维训练 {n_th} 题、新增错题 {n_mk} 道；"
        f"各科掌握度：{'；'.join(data)}。\n\n"
        "请以班主任口吻写一段给家长的周评（150~250字）：先肯定亮点（结合她的兴趣与性格），"
        "再委婉指出 1 个需要关注的点，最后给家长 1 条可操作的家庭配合建议。真诚具体，不说套话。\n"
        "格式硬性要求：纯中文白话，给家长看；严禁出现任何数学公式、LaTeX、代码、Markdown 符号"
        "（不要 ** 加粗、不要 \\( \\) 包裹数字，数字直接写）；分段用自然换行。"
    )
    content, err = ai_chat([{"role": "system", "content": "你是了解学生的班主任，评语温暖而有分寸，只用纯文本写作。"},
                            {"role": "user", "content": ask}], max_tokens=800)
    if err:
        return jsonify({"ok": False, "msg": "AI 评语生成失败：" + err})
    return jsonify({"ok": True, "text": plainify(content)})


TOOL_KINDS = {"formula": "数学公式", "poem": "古诗文", "word": "英语词卡"}


@app.route("/toolbox")
def toolbox():
    kind = request.args.get("tab", "formula")
    if kind not in TOOL_KINDS:
        kind = "formula"
    stats = {r["item_key"]: (r["n"], r["rig"]) for r in q(
        "SELECT item_key, COUNT(*) n, SUM(CASE WHEN result >= 1 THEN 1 ELSE 0 END) rig FROM tools_log GROUP BY item_key")}
    items = []
    if kind == "formula":
        for mod, name, formula, note in seed_data.MATH_FORMULAS:
            k = name
            s = stats.get(k)
            items.append({"key": k, "front": f"{mod} · {name}", "back": formula, "note": note,
                          "n": s[0] if s else 0, "rate": round(s[1] * 100 / s[0]) if s and s[0] else None})
    elif kind == "poem":
        for title, author, line, theme in seed_data.CLASSIC_POEMS:
            s = stats.get(title)
            items.append({"key": title, "front": f"《{title}》· {author}", "back": line, "note": theme,
                          "n": s[0] if s else 0, "rate": round(s[1] * 100 / s[0]) if s and s[0] else None})
    else:
        for w, meaning in seed_data.WORD_CORE:
            s = stats.get(w)
            items.append({"key": w, "front": w, "back": meaning, "note": "",
                          "n": s[0] if s else 0, "rate": round(s[1] * 100 / s[0]) if s and s[0] else None})
    items.sort(key=lambda x: (x["rate"] is not None and x["rate"] >= 80, x["rate"] is not None, x["key"]))
    return render_template("toolbox.html", kind=kind, kinds=TOOL_KINDS, items=items)


@app.route("/tool_rate", methods=["POST"])
def tool_rate():
    kind = request.form.get("kind", "")
    key = request.form.get("key", "")
    result = request.form.get("result", type=float)
    if kind in TOOL_KINDS and key and result in (0, 0.5, 1):
        run("INSERT INTO tools_log (kind, item_key, result, created) VALUES (?,?,?,?)",
            (kind, key[:60], result, now_iso()))
    leveled, lv = add_xp(3, "卡片自测")
    return redirect("toolbox?tab=" + kind + "&gx=3" + (f"&lv={lv}" if leveled else ""))


def gen_questions_ai(subject_code, kind):
    meta = seed_data.SUBJECTS.get(subject_code, {})
    n = 5 if kind == "practice" else 6
    if kind in ("subject", "practice"):
        purpose = "一套学科摸底卷" if kind == "subject" else "一组课后专项练习"
        ask = (
            f"请为{profile_text()}出{purpose}【{meta.get('name', subject_code)}】（教材版本：{textbook_of(subject_code)}）共 {n} 题，"
            f"覆盖板块：{'、'.join(meta.get('boards', []))}，难度由易到难阶梯分布。"
            "其中至少 1 题情境结合她的兴趣（马术/赛艇/钢琴），让题目亲切有趣。"
            "题型以简答为主（可含 1 道默写/计算/赏析）。"
            '只输出JSON数组：[{"text":"题干","ref":"参考答案与考点简析","board":"所属板块"}，…]'
        )
    else:
        ask = (
            f"请为{profile_text()}设计一套认知能力摸底卷，共 8 题：数字推理 3 题、"
            "图形规律（用文字精确描述图形序列）3 题、逻辑矩阵/类比 2 题，难度递增。"
            '只输出JSON数组：[{"text":"题干","ref":"参考答案与解析要点","board":"数字推理/图形规律/逻辑矩阵"}，…]'
        )
    content, err = ai_chat([
        {"role": "system", "content": "你是江苏南京的资深教研员与命题专家，只输出JSON数组，不输出其他内容。"},
        {"role": "user", "content": ask},
    ], max_tokens=3500)
    if err:
        return None, err
    try:
        text = re.sub(r"^```(json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        start, end = text.find("["), text.rfind("]")
        arr = json.loads(text[start:end + 1])

        def pick(d, *keys):
            for k in keys:
                if d.get(k):
                    return str(d[k])
            return ""
        out = []
        for it in arr:
            if not isinstance(it, dict):
                continue
            stem = pick(it, "text", "stem", "question", "title")
            if not stem:
                continue
            ref = pick(it, "ref", "answer")
            if it.get("analysis"):
                ref = (ref + "｜解析：" + str(it["analysis"])).strip("｜")
            if it.get("options"):
                ref = "选项：" + "；".join(str(o) for o in it["options"]) + "｜答案：" + ref
            out.append({"text": stem, "ref": ref, "board": pick(it, "board", "module", "type", "category") or "综合"})
        if len(out) >= 3:
            return out, None
        return None, "生成题目数量不足"
    except Exception as e:
        return None, "题目解析失败：" + str(e)[:100]


@app.route("/ceping")
def ceping():
    subject = request.args.get("subject", "")
    history = q("SELECT * FROM assessments WHERE kind != 'story' ORDER BY id DESC LIMIT 8")
    return render_template("assess.html", subject=subject, kinds=seed_data.ASSESS_KINDS, history=history)


@app.route("/ceping_start", methods=["POST"])
def ceping_start():
    subject = request.form.get("subject", "math")
    kind = request.form.get("kind", "subject")
    if kind not in seed_data.ASSESS_KINDS or subject not in seed_data.SUBJECTS:
        abort(400)
    if kind == "thinking":
        rows = q("SELECT id, category, diff, text, answer FROM puzzles")
        picked = random.sample(list(rows), min(10, len(rows)))
        questions = [{"text": r["text"], "ref": r["answer"], "board": r["category"]} for r in picked]
        err = None
    else:
        questions, err = gen_questions_ai(subject, kind)
    if err:
        return render_template("assess.html", subject=subject, kinds=seed_data.ASSESS_KINDS,
                               history=q("SELECT * FROM assessments ORDER BY id DESC LIMIT 8"),
                               error="出题失败：" + err + "，请稍后重试")
    run("INSERT INTO assessments (subject, kind, questions, created) VALUES (?,?,?,?)",
        (subject, kind, json.dumps(questions, ensure_ascii=False), now_iso()))
    aid = q1("SELECT last_insert_rowid() id")["id"]
    return redirect("ceping_do?id=" + str(aid))


@app.route("/ceping_do")
def ceping_do():
    aid = request.args.get("id", type=int)
    a = q1("SELECT * FROM assessments WHERE id=?", (aid,))
    if not a:
        abort(404)
    questions = json.loads(a["questions"])
    story_meta = None
    if a["kind"] == "story":
        sm = json.loads(a["answers"] or "{}")
        theme = a["subject"][6:] if a["subject"].startswith("story:") else ""
        story_meta = {"title": sm.get("title", ""), "intro": sm.get("intro", ""),
                      "theme": STORY_THEMES.get(theme, {}), "code": theme}
    return render_template("assess_do.html", a=a, questions=questions,
                           meta=seed_data.SUBJECTS.get(a["subject"], {}), story_meta=story_meta)


def ai_grade_assessment(a, answers):
    questions = json.loads(a["questions"])
    meta = seed_data.SUBJECTS.get(a["subject"], {})
    kind_name = seed_data.ASSESS_KIND_LABEL.get(a["kind"], a["kind"])
    lines = []
    for i, (qs, ans) in enumerate(zip(questions, answers), 1):
        lines.append(f"第{i}题（板块：{qs.get('board', '')}）：{qs['text']}\n参考答案：{qs.get('ref', '')}\n她的作答：{ans or '（未作答）'}")
    if a["kind"] == "story":
        right = sum(1 for qs, ans in zip(questions, answers) if ans.strip() and any(k in ans for k in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", qs.get("ref", ""))[:6]))
        mood = "全部或大部分答对" if right >= len(questions) - 1 else ("对错参半" if right >= 1 else "大部分答错")
        ask = (
            f"{profile_text()}\n她在互动剧情冒险一章的作答如下（{mood}）：\n\n" +
            "\n\n".join(lines) +
            "\n\n请以小说笔法写本章结局（120~180字）：根据作答推进剧情——答好则凯旋或获得线索，"
            "答错则遭遇波折但获得提示与鼓励；结尾留下下一章悬念钩子。最后加【逐题点评】小节逐题简评。"
        )
    else:
        ask = (
            f"{profile_text()}\n"
            f"教材版本：{textbook_of(a['subject'])}\n"
            f"测评类型：{meta.get('name', '')}{kind_name}\n\n以下是逐题信息（题目/参考答案/她的作答）：\n" +
        "\n\n".join(lines) +
        "\n\n请严格按以下小节输出，直接以方括号标题开头：\n"
        "【逐题判定】每题一行：题号 ✓/◐/✗ + 一句话点评（对在哪/错在哪）\n"
        "【总体画像】用 3~5 个关键词条刻画她在本测评中展现的资质与特点"
        "（如逻辑推理强、知识面广、图形敏感度高、语言表达优秀等，结合作答证据），"
        "并给出与她的兴趣特长（马术/赛艇/钢琴）相结合的理解\n"
        "【优势】列 2~3 条具体优势\n"
        "【薄弱】列 2~3 条具体短板与可能的成因\n"
        "【定制方案】给一份 4 周的定制提升方案：按周列出训练重点，"
        "把学习内容与她的兴趣场景结合（如赛艇配速算速率、马术路线设计几何、钢琴节奏与分数）"
    )
    content, err = ai_chat([
        {"role": "system", "content": "你是因材施教的教育评估专家，评语真诚具体、避免空话，善于发现学生的闪光点。"},
        {"role": "user", "content": ask},
    ], max_tokens=3500)
    if err:
        return None, err
    return content.strip(), None


@app.route("/ceping_submit", methods=["POST"])
def ceping_submit():
    aid = request.form.get("id", type=int)
    a = q1("SELECT * FROM assessments WHERE id=?", (aid,))
    if not a or a["done"]:
        return redirect("ceping")
    questions = json.loads(a["questions"])
    answers = [request.form.get(f"q{i}", "").strip() for i in range(1, len(questions) + 1)]
    report, err = ai_grade_assessment(a, answers)
    if err:
        return render_template("assess_do.html", a=a, questions=questions,
                               meta=seed_data.SUBJECTS.get(a["subject"], {}), story_meta=None,
                               prev_answers=answers,
                               error="AI 批改失败：" + err + "，请重试提交（你的作答已保留）")
    run("UPDATE assessments SET answers=?, report=?, done=1 WHERE id=?",
        (json.dumps(answers, ensure_ascii=False), report, aid))
    if a["kind"] == "story":
        theme = a["subject"][6:] if a["subject"].startswith("story:") else ""
        st = story_state()
        entry = st.setdefault(theme, {"chapter": 0, "summary": ""})
        entry["chapter"] = entry.get("chapter", 0) + 1
        entry["summary"] = (report or "")[:120]
        set_cfg("story_state", json.dumps(st, ensure_ascii=False))
        leveled, lv = add_xp(40, "剧情冒险")
        return redirect("ceping_result?id=" + str(aid) + "&gx=40" + (f"&lv={lv}" if leveled else ""))
    if a["kind"] == "subject":
        boards = {r["name"]: r["id"] for r in q("SELECT id, name FROM kp WHERE subject=?", (a["subject"],))}
        for qs, ans in zip(questions, answers):
            kid = boards.get(qs.get("board", ""))
            if not kid:
                continue
            ref = (qs.get("ref") or "").strip()
            hit = sum(k in ans for k in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", ref)[:8])
            if ans and hit >= 2:
                update_mastery(kid, 1 if hit >= 4 else 0.5)
            else:
                update_mastery(kid, 0)
    leveled, lv = add_xp(30, "完成测评")
    return redirect("ceping_result?id=" + str(aid) + "&gx=30" + (f"&lv={lv}" if leveled else ""))


@app.route("/ceping_result")
def ceping_result():
    aid = request.args.get("id", type=int)
    a = q1("SELECT * FROM assessments WHERE id=?", (aid,))
    if not a or not a["done"]:
        abort(404)
    questions = json.loads(a["questions"])
    answers = json.loads(a["answers"])
    story_meta = None
    if a["kind"] == "story":
        theme = a["subject"][6:] if a["subject"].startswith("story:") else ""
        story_meta = {"theme": STORY_THEMES.get(theme, {}), "code": theme}
    return render_template("assess_result.html", a=a, questions=questions, answers=answers,
                           meta=seed_data.SUBJECTS.get(a["subject"], {}), story_meta=story_meta,
                           kind_name=seed_data.ASSESS_KIND_LABEL.get(a["kind"], a["kind"]))


@app.route("/practice")
def practice():
    stage = cfg("stage", "cj")
    kps = stage_kps(stage)
    weak = weak_kps(stage, 6)
    weak_ids = [w["id"] for w in weak]
    counts = {r["kp_id"]: r["c"] for r in q("SELECT kp_id, COUNT(*) c FROM questions GROUP BY kp_id")}
    modules = []
    for r in q("SELECT DISTINCT module FROM kp WHERE stage=? ORDER BY module", (stage,)):
        modules.append(r["module"])
    return render_template("practice.html", kps=kps, weak=weak, weak_ids=weak_ids,
                           counts=counts, modules=modules)


@app.route("/practice_start", methods=["POST"])
def practice_start():
    stage = cfg("stage", "cj")
    mode = request.form.get("mode", "weak")
    count = request.form.get("count", 10, type=int)
    count = clamp(count, 3, 30)
    if mode == "weak":
        kp_ids = [w["id"] for w in weak_kps(stage, 6)]
    elif mode == "custom":
        kp_ids = request.form.getlist("kp", type=int)
    else:
        kp_ids = []
    picked = pick_questions(stage, kp_ids, count)
    session["quiz"] = [p["id"] for p in picked]
    session["qi"] = 0
    session["quiz_r"] = []
    return redirect("quiz")


@app.route("/quiz", methods=["GET"])
def quiz():
    ids = session.get("quiz") or []
    i = session.get("qi", 0)
    if i >= len(ids):
        results = session.get("quiz_r") or []
        return render_template("quiz.html", done=True, results=results, item=None, i=i, n=len(ids))
    row = q1(
        """SELECT qq.*, k.name kp_name, k.module, k.id real_kp FROM questions qq JOIN kp k ON qq.kp_id=k.id WHERE qq.id=?""",
        (ids[i],),
    )
    return render_template("quiz.html", done=False, item=row, i=i, n=len(ids), results=None)


@app.route("/quiz_answer", methods=["POST"])
def quiz_answer():
    qid = request.form.get("qid", type=int)
    result = request.form.get("result", type=float)
    ids = session.get("quiz") or []
    if qid and result in (0, 0.5, 1) and session.get("qi", 0) < len(ids):
        row = q1("SELECT * FROM questions WHERE id=?", (qid,))
        if row:
            run("INSERT INTO practice_log (kp_id, qid, result, created) VALUES (?,?,?,?)",
                (row["kp_id"], qid, result, now_iso()))
            update_mastery(row["kp_id"], result)
            if result == 0:
                run(
                    """INSERT INTO mistakes (kp_id, title, answer, cause, source, diff, created, next_review)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (row["kp_id"], row["text"], row["answer"], "其他", "智能练习", row["diff"], now_iso(), today_iso()),
                )
                apply_penalty(row["kp_id"], "其他")
            results = session.get("quiz_r") or []
            kp = q1("SELECT name FROM kp WHERE id=?", (row["kp_id"],))
            results.append({"text": row["text"], "kp_name": kp["name"] if kp else "", "result": result})
            session["quiz_r"] = results
    session["qi"] = session.get("qi", 0) + 1
    leveled, lv = add_xp(10, "智能练习")
    return redirect("quiz?gx=10" + (f"&lv={lv}" if leveled else ""))


@app.route("/mastery")
def mastery():
    stage = cfg("stage", "cj")
    mods = module_stats(stage)
    tree = []
    for m in mods:
        kps = q("SELECT * FROM kp WHERE stage=? AND module=? ORDER BY id", (stage, m["module"]))
        tree.append({"module": m["module"], "kps": kps})
    return render_template("mastery.html", tree=tree, levels=LEVEL_NAMES)


@app.route("/self_rate", methods=["POST"])
def self_rate():
    kp_id = request.form.get("kp_id", type=int)
    level = request.form.get("level", type=int)
    if kp_id and level in LEVEL_VALUES:
        run("UPDATE kp SET mastery=?, updated=? WHERE id=?",
            (LEVEL_VALUES[level], now_iso(), kp_id))
    return redirect("mastery")


@app.route("/scores")
def scores():
    rows = score_rows()
    pts = []
    seq = list(reversed(rows))[-12:]
    for s in seq:
        pts.append({"date": s["date"], "kind": s["kind"] or "考试",
                    "pct": round(s["score"] * 100 / (s["full"] or 100), 1)})
    w, h, pad = 640, 220, 30
    chart = None
    if len(pts) >= 2:
        step = (w - 2 * pad) / (len(pts) - 1)
        coords = []
        for idx, p in enumerate(pts):
            x = round(pad + idx * step)
            y = round(h - pad - (p["pct"] / 100) * (h - 2 * pad))
            coords.append((x, y))
        chart = {"w": w, "h": h, "coords": coords, "labels": [p["date"][5:] for p in pts]}
    return render_template("scores.html", rows=rows, pts=pts, chart=chart)


@app.route("/save_score", methods=["POST"])
def save_score():
    d = request.form.get("date") or today_iso()
    kind = request.form.get("kind", "周测")
    score = request.form.get("score", type=float)
    full = request.form.get("full", 100, type=float) or 100
    note = request.form.get("note", "").strip()
    subject = request.form.get("subject", "math")
    if subject not in seed_data.SUBJECTS:
        subject = "math"
    if score is None or not d:
        abort(400)
    run("INSERT INTO scores (date, kind, score, full, note, subject) VALUES (?,?,?,?,?,?)",
        (d, kind, score, full, note, subject))
    if subject == "math":
        return redirect("scores")
    return redirect(f"subject/{subject}/scores")


@app.route("/delete_score", methods=["POST"])
def delete_score():
    sid = request.form.get("id", type=int)
    if sid:
        run("DELETE FROM scores WHERE id=?", (sid,))
    return redirect("scores")


@app.route("/plan", methods=["GET", "POST"])
def plan():
    stage = cfg("stage", "cj")
    result = None
    if request.method == "POST":
        exam = request.form.get("exam_date", "")
        target = request.form.get("target", "").strip()
        minutes = clamp(request.form.get("minutes", 40, type=int) or 40, 10, 300)
        if exam:
            set_cfg("exam_date", exam)
        if target:
            set_cfg("target", target)
        set_cfg("minutes", str(minutes))
        result = build_plan(stage, exam or cfg("exam_date"), target, minutes)
    else:
        exam = cfg("exam_date")
        if exam:
            result = build_plan(stage, exam, cfg("target"), int(cfg("minutes", "40") or 40))
    return render_template("plan.html", result=result,
                           exam_date=cfg("exam_date"), target=cfg("target"),
                           minutes=cfg("minutes", "40"))


def build_plan(stage, exam, target, minutes):
    if not exam:
        return None
    try:
        days = (date.fromisoformat(exam) - date.today()).days
    except ValueError:
        return None
    mods = module_stats(stage)
    weak_mods = sorted(mods, key=lambda m: m["avg"])[:3]
    gap = []
    for m in weak_mods:
        gap.append((m["module"], round(100 - m["avg"])))
    if days <= 0:
        phases = [("今日冲刺", "只做两件事：过错题本（全部 active 错题过一遍）+ 默写核心公式清单。")]
    elif days <= 15:
        phases = [
            ("第 1 阶段 · 全面排查（前 40% 时间）", "用「智能弱项练习」把每个模块过一遍，暴露漏洞，全部录入错题本。"),
            ("第 2 阶段 · 错题清零（中间 40% 时间）", "每天完成到期复习，连续答对 3 次的题目自动毕业；新错题当天归因。"),
            ("第 3 阶段 · 限时模拟（最后 20% 时间）", "每两天一套限时卷，训练时间分配；只复盘错题，不刷新题。"),
        ]
    elif days <= 45:
        phases = [
            ("第 1 阶段 · 基础巩固（前 40% 时间）", "按模块顺序过知识点，掌握度低于 60 的模块每天练 10 题。"),
            ("第 2 阶段 · 专项突破（中间 40% 时间）", "针对最薄弱的 3 个模块集中刷题，配合错题本间隔复习。"),
            ("第 3 阶段 · 综合模拟（最后 20% 时间）", "每周两套限时模拟，考后 24 小时内完成错题归因与录入。"),
        ]
    else:
        phases = [
            ("第 1 阶段 · 基础全面梳理（前 50% 时间）", "地毯式过知识点，自评掌握度，把不会的标记出来并录错题。"),
            ("第 2 阶段 · 弱项专项突破（后 30% 时间）", "掌握度最低的 3 个模块每天专项练习，每周日复盘一次掌握度变化。"),
            ("第 3 阶段 · 模拟冲刺（最后 20% 时间）", "限时模拟 + 错题清零 + 公式默写，调整作息与考试生物钟。"),
        ]
    review_min = min(15, max(8, minutes // 4))
    q_minutes = max(10, minutes - review_min - 5)
    q_count = max(3, q_minutes // 3)
    daily = [
        ("间隔复习", f"{review_min} 分钟 · 完成今日到期错题（先回忆再对答案）"),
        ("专项练习", f"{q_count} 题 · 优先最薄弱知识点，交错练习效果更好"),
        ("错因归因", "5 分钟 · 把今天的错题归类（概念/方法/审题/计算）并记一句话教训"),
    ]
    shares = []
    total_gap = sum(g for _, g in gap) or 1
    for name, g in gap:
        shares.append({"module": name, "pct": round(g * 100 / total_gap), "gap": g})
    return {
        "days": days, "exam": exam, "target": target, "minutes": minutes,
        "phases": phases, "daily": daily, "weak_mods": weak_mods, "shares": shares,
    }


@app.route("/thinking")
def thinking():
    stats = {c: {"n": 0, "right": 0} for c in PUZZLE_CATS}
    for r in q("""SELECT p.category, COUNT(*) n, SUM(CASE WHEN t.result >= 1 THEN 1 ELSE 0 END) rig
                  FROM thinking_log t JOIN puzzles p ON t.puzzle_id=p.id GROUP BY p.category"""):
        if r["category"] in stats:
            stats[r["category"]] = {"n": r["n"], "right": r["rig"] or 0}
    total = q1("SELECT COUNT(*) c FROM puzzles")["c"]
    day_n = q1("SELECT COUNT(*) c FROM thinking_log WHERE created LIKE ?", (today_iso() + "%",))["c"]
    daily = q("SELECT id FROM puzzles ORDER BY (id * 7919 + ?) % 100000 LIMIT 1", (date.today().toordinal() % 100000,))[0]["id"] \
        if total else None
    return render_template("thinking.html", stats=stats, day_n=day_n, daily=daily, cats=PUZZLE_CATS)


@app.route("/thinking_start", methods=["POST"])
def thinking_start():
    cat = request.form.get("cat", "")
    count = clamp(request.form.get("count", 5, type=int) or 5, 1, 20)
    if cat and cat not in PUZZLE_CATS:
        cat = ""
    if cat:
        rows = q("SELECT id FROM puzzles WHERE category=?", (cat,))
    else:
        rows = q("SELECT id FROM puzzles")
    ids = [r["id"] for r in rows]
    random.shuffle(ids)
    session["puzzles"] = ids[:count]
    session["pi"] = 0
    session["puzz_r"] = []
    return redirect("puzzle")


@app.route("/daily_start", methods=["POST"])
def daily_start():
    total = q1("SELECT COUNT(*) c FROM puzzles")["c"]
    if not total:
        return redirect("thinking")
    pid = q("SELECT id FROM puzzles ORDER BY (id * 7919 + ?) % 100000 LIMIT 1",
            (date.today().toordinal() % 100000,))[0]["id"]
    session["puzzles"] = [pid]
    session["pi"] = 0
    session["puzz_r"] = []
    return redirect("puzzle")


@app.route("/puzzle")
def puzzle():
    ids = session.get("puzzles") or []
    i = session.get("pi", 0)
    if i >= len(ids):
        results = session.get("puzz_r") or []
        return render_template("puzzle.html", done=True, item=None, i=i, n=len(ids), results=results)
    row = q1("SELECT * FROM puzzles WHERE id=?", (ids[i],))
    return render_template("puzzle.html", done=False, item=row, i=i, n=len(ids), results=None)


@app.route("/puzzle_answer", methods=["POST"])
def puzzle_answer():
    pid = request.form.get("id", type=int)
    result = request.form.get("result", type=float)
    ids = session.get("puzzles") or []
    if pid and result in (0, 0.5, 1) and session.get("pi", 0) < len(ids):
        row = q1("SELECT * FROM puzzles WHERE id=?", (pid,))
        if row:
            run("INSERT INTO thinking_log (puzzle_id, result, created) VALUES (?,?,?)", (pid, result, now_iso()))
            results = session.get("puzz_r") or []
            results.append({"text": row["text"], "cat": row["category"], "result": result})
            session["puzz_r"] = results
    session["pi"] = session.get("pi", 0) + 1
    leveled, lv = add_xp(15, "思维训练")
    return redirect("puzzle?gx=15" + (f"&lv={lv}" if leveled else ""))


def logic_stats(stage):
    rows = q(
        """SELECT m.logic_type, COUNT(*) c FROM mistakes m JOIN kp ON m.kp_id=kp.id
           WHERE kp.stage=? AND m.logic_type != '' GROUP BY m.logic_type ORDER BY c DESC""",
        (stage,),
    )
    total = sum(r["c"] for r in rows)
    out = [{"t": r["logic_type"], "desc": LOGIC_TYPES.get(r["logic_type"], ""), "c": r["c"],
            "pct": round(r["c"] * 100 / total) if total else 0} for r in rows]
    return out, total


@app.route("/report")
def report():
    stage = cfg("stage", "cj")
    overall_m = overall(stage)
    label, cls = mastery_label(overall_m)
    mods = module_stats(stage)
    weak = weak_kps(stage, 5)
    dist, total = cause_distribution(stage)
    scores = list(reversed(q("SELECT * FROM scores WHERE subject='math' ORDER BY date DESC, id DESC LIMIT 10")))
    trend = None
    if len(scores) >= 2:
        pts = [round(s["score"] * 100 / (s["full"] or 100)) for s in scores]
        trend = pts[-1] - pts[0]
    practice_total = q1("SELECT COUNT(*) c FROM practice_log")["c"]
    reviewed = q1("SELECT COUNT(*) c FROM reviews_done")["c"]
    active_n = q1("SELECT COUNT(*) c FROM mistakes WHERE status='active'")["c"]
    lstats, ltotal = logic_stats(stage)
    train_map = {}
    for s in lstats:
        cats = LOGIC_TRAIN.get(s["t"], [])
        for c in cats:
            train_map[c] = train_map.get(c, 0) + s["c"]
    recommended = sorted(train_map, key=train_map.get, reverse=True)
    tstats = {c: {"n": 0, "right": 0} for c in PUZZLE_CATS}
    for r in q("""SELECT p.category, COUNT(*) n, SUM(CASE WHEN t.result >= 1 THEN 1 ELSE 0 END) rig
                  FROM thinking_log t JOIN puzzles p ON t.puzzle_id=p.id GROUP BY p.category"""):
        if r["category"] in tstats:
            tstats[r["category"]] = {"n": r["n"], "right": r["rig"] or 0}
    thinking_total = q1("SELECT COUNT(*) c FROM thinking_log")["c"]
    advice = []
    for d in dist:
        if d["pct"] >= 20:
            advice.append({"cause": d["cause"], "pct": d["pct"], "text": CAUSE_ADVICE.get(d["cause"], "")})
    if ltotal:
        top = lstats[0]
        advice.append({"cause": "逻辑思维 · " + top["t"], "pct": top["pct"],
                       "text": "这是你最主要的逻辑缺陷。针对性训练：" + "、".join(LOGIC_TRAIN.get(top["t"], [])) + "（见下方思维训练建议）。"})
    if overall_m < 60:
        advice.append({"cause": "整体策略", "pct": 0,
                       "text": "整体掌握度偏低，先用 2 周做基础排查：每个知识点自评 + 练 5 题，把漏洞全部暴露出来再谈提高。"})
    if practice_total < 20:
        advice.append({"cause": "练习量", "pct": 0,
                       "text": "练习记录尚少，数据越充分诊断越准。建议每天一组（10 题左右），两周后报告才有统计意义。"})
    if trend is not None and trend < -5:
        advice.append({"cause": "成绩趋势", "pct": 0,
                       "text": "近期成绩下滑，检查是否复习中断：错题本里的 active 题目是否堆积？优先清空到期复习。"})
    if thinking_total == 0 and ltotal >= 2:
        advice.append({"cause": "思维训练", "pct": 0,
                       "text": "已有多道逻辑思维错题，但思维训练尚未开始。每天 1~2 道门萨式题目即可显著改善推理链质量。"})
    return render_template(
        "report.html", overall_m=overall_m, label=label, cls=cls, mods=mods, weak=weak,
        dist=dist, total=total, scores=scores, trend=trend,
        practice_total=practice_total, reviewed=reviewed, active_n=active_n, advice=advice,
        lstats=lstats, ltotal=ltotal, recommended=recommended, tstats=tstats,
        thinking_total=thinking_total, logic_types=LOGIC_TYPES,
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    saved = False
    pw_msg = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            set_cfg("stage", request.form.get("stage", "cj"))
            set_cfg("grade", request.form.get("grade", "初二"))
            set_cfg("name", (request.form.get("name", "") or "黄曼清").strip()[:20])
            set_cfg("region", (request.form.get("region", "") or "江苏南京").strip()[:20])
            set_cfg("hobbies", (request.form.get("hobbies", "") or "马术、赛艇、钢琴").strip()[:100])
            tbs = {}
            for code in seed_data.TEXTBOOKS:
                v = request.form.get("tb_" + code, "").strip()
                if v:
                    tbs[code] = v
            set_cfg("textbooks", json.dumps(tbs, ensure_ascii=False))
            set_cfg("exam_date", request.form.get("exam_date", ""))
            set_cfg("target", request.form.get("target", "").strip())
            set_cfg("minutes", str(clamp(request.form.get("minutes", 40, type=int) or 40, 10, 300)))
            saved = True
        elif action == "pw":
            old = request.form.get("old", "")
            new = request.form.get("new", "")
            if not check_password_hash(cfg("pw"), old):
                pw_msg = "旧密码不正确"
            elif len(new) < 4:
                pw_msg = "新密码至少 4 位"
            else:
                set_cfg("pw", generate_password_hash(new))
                pw_msg = "密码已更新"
    return render_template("settings.html", saved=saved, pw_msg=pw_msg,
                           exam_date=cfg("exam_date"), target=cfg("target"),
                           minutes=cfg("minutes", "40"), stages=seed_data.STAGES,
                           grade=cfg("grade", "初二"),
                           grades=["初一", "初二", "初三", "高一", "高二", "高三"],
                           name=cfg("name", "黄曼清"), region=cfg("region", "江苏南京"),
                           hobbies=cfg("hobbies", "马术、赛艇、钢琴"),
                           tb=seed_data.TEXTBOOKS, cur_tb=textbooks())


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8041)
