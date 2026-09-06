import os
import random
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from flask import Flask, abort, g, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import seed_data

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "math.db")
KEY_PATH = os.path.join(BASE_DIR, "data", "secret_key")

app = Flask(__name__)

CAUSES = ["概念不清", "方法不会", "思路错误", "审题失误", "计算错误", "粗心大意", "其他"]
CAUSE_HIT = {"概念不清": 15, "方法不会": 12, "思路错误": 12, "审题失误": 8, "计算错误": 6, "粗心大意": 6, "其他": 5}
CAUSE_ADVICE = {
    "概念不清": "回归课本重读定义定理，用费曼技巧把概念讲给别人听，讲不清的地方就是漏洞。",
    "方法不会": "针对该知识点补典型例题，先看解答再独立复现，总结成“题型-方法”卡片。",
    "思路错误": "练习写解题思路提纲，每步问自己“由什么条件、想到什么、得到什么”。",
    "审题失误": "养成圈画关键词的习惯：数字、单位、“不”“至少”“分别”等字眼逐个标记。",
    "计算错误": "每天 10 分钟限时口算/竖式训练，做题留验算时间，关键步骤代回检验。",
    "粗心大意": "使用检查清单：抄题核对、符号核对、单位核对，错一次记一次，量化警惕。",
    "其他": "把错题归因写清楚，模糊归因本身就是失分来源。",
}
LEVEL_VALUES = {0: 10, 1: 30, 2: 50, 3: 75, 4: 90}
LEVEL_NAMES = {0: "完全不会", 1: "勉强记得", 2: "基本理解", 3: "比较熟练", 4: "非常熟练"}


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
        """
    )
    db.commit()
    if q1_static(db, "SELECT COUNT(*) c FROM kp")["c"] == 0:
        for stage, module, kps in seed_data.MODULE_TREE:
            for name in kps:
                db.execute("INSERT INTO kp (stage, module, name) VALUES (?,?,?)", (stage, module, name))
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
    defaults = {"stage": "cj", "exam_date": "", "target": "", "minutes": "40"}
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


def due_mistakes(stage):
    return q(
        """SELECT m.*, kp.name kp_name, kp.module FROM mistakes m JOIN kp ON m.kp_id=kp.id
           WHERE m.status='active' AND m.next_review<=? AND kp.stage=?
           ORDER BY m.next_review, m.id""",
        (today_iso(), stage),
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
    return q("SELECT * FROM scores ORDER BY date DESC, id DESC")


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
    due = due_mistakes(stage)
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
    return tips


@app.before_request
def auth():
    allow = request.path == "/login" or request.path.startswith("/static")
    if allow:
        return None
    if not session.get("ok"):
        return redirect("login")
    return None


@app.context_processor
def inject_common():
    stage = cfg("stage", "cj")
    return {
        "stage": stage,
        "stage_name": seed_data.STAGES.get(stage, stage),
        "nav": request.path.strip("/").split("/")[0] or "dashboard",
        "exam_days": days_to_exam(),
        "mastery_label": mastery_label,
        "today": today_iso(),
        "now": now_iso(),
    }


@app.route("/")
@app.route("/dashboard")
def dashboard():
    stage = cfg("stage", "cj")
    overall_m = overall(stage)
    label, cls = mastery_label(overall_m)
    due = due_mistakes(stage)
    mistakes_n = q1("SELECT COUNT(*) c FROM mistakes m JOIN kp ON m.kp_id=kp.id WHERE kp.stage=?", (stage,))["c"]
    practice_n = q1("SELECT COUNT(*) c FROM practice_log")["c"]
    weak = weak_kps(stage, 5)
    scores = list(reversed(q("SELECT * FROM scores ORDER BY date DESC, id DESC LIMIT 8")))
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
                return redirect("dashboard")
        else:
            if check_password_hash(cfg("pw"), pw):
                session["ok"] = True
                return redirect("dashboard")
            error = "密码不正确"
    return render_template("login.html", pw_set=pw_set, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("login")


@app.route("/mistakes")
def mistakes():
    stage = cfg("stage", "cj")
    status = request.args.get("status", "active")
    cause = request.args.get("cause", "")
    conds, args = ["kp.stage=?"], [stage]
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
    kps = stage_kps(stage)
    return render_template("mistakes.html", rows=rows, kps=kps, causes=CAUSES,
                           status=status, cause=cause, stage_name=seed_data.STAGES.get(stage, stage))


@app.route("/save_mistake", methods=["POST"])
def save_mistake():
    kp_id = request.form.get("kp_id", type=int)
    title = request.form.get("title", "").strip()
    answer = request.form.get("answer", "").strip()
    cause = request.form.get("cause", "其他")
    source = request.form.get("source", "").strip()
    diff = request.form.get("diff", 2, type=int)
    if not kp_id or not title:
        abort(400)
    run(
        """INSERT INTO mistakes (kp_id, title, answer, cause, source, diff, created, next_review)
           VALUES (?,?,?,?,?,?,?,?)""",
        (kp_id, title, answer, cause, source, diff, now_iso(), today_iso()),
    )
    apply_penalty(kp_id, cause)
    return redirect("mistakes")


@app.route("/delete_mistake", methods=["POST"])
def delete_mistake():
    mid = request.form.get("id", type=int)
    if mid:
        run("DELETE FROM mistakes WHERE id=?", (mid,))
    return redirect("mistakes")


@app.route("/review")
def review():
    stage = cfg("stage", "cj")
    due = due_mistakes(stage)
    item = due[0] if due else None
    done_today = q1("SELECT COUNT(*) c FROM reviews_done WHERE created LIKE ?", (today_iso() + "%",))["c"]
    return render_template("review.html", item=item, left=len(due), done_today=done_today)


@app.route("/review_answer", methods=["POST"])
def review_answer():
    mid = request.form.get("id", type=int)
    result = request.form.get("result", type=float)
    m = q1("SELECT * FROM mistakes WHERE id=?", (mid,))
    if m and result in (0, 0.5, 1):
        sm2_update(m, result)
        update_mastery(m["kp_id"], result)
    return redirect("review")


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
    return redirect("quiz")


@app.route("/mastery")
def mastery():
    stage = cfg("stage", "cj")
    mods = module_stats(stage)
    tree = []
    for m in mods:
        kps = q("SELECT * FROM kp WHERE stage=? AND module=? ORDER BY id", (stage, m["module"]))
        tree.append({"module": m["module"], "kps": kps})
    return render_template("mastery.html", tree=tree, levels=LEVEL_NAMES)


@app.route("/assess", methods=["POST"])
def assess():
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
    if score is None or not d:
        abort(400)
    run("INSERT INTO scores (date, kind, score, full, note) VALUES (?,?,?,?,?)",
        (d, kind, score, full, note))
    return redirect("scores")


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


@app.route("/report")
def report():
    stage = cfg("stage", "cj")
    overall_m = overall(stage)
    label, cls = mastery_label(overall_m)
    mods = module_stats(stage)
    weak = weak_kps(stage, 5)
    dist, total = cause_distribution(stage)
    scores = list(reversed(q("SELECT * FROM scores ORDER BY date DESC, id DESC LIMIT 10")))
    trend = None
    if len(scores) >= 2:
        pts = [round(s["score"] * 100 / (s["full"] or 100)) for s in scores]
        trend = pts[-1] - pts[0]
    practice_total = q1("SELECT COUNT(*) c FROM practice_log")["c"]
    reviewed = q1("SELECT COUNT(*) c FROM reviews_done")["c"]
    active_n = q1("SELECT COUNT(*) c FROM mistakes WHERE status='active'")["c"]
    advice = []
    for d in dist:
        if d["pct"] >= 20:
            advice.append({"cause": d["cause"], "pct": d["pct"], "text": CAUSE_ADVICE.get(d["cause"], "")})
    if overall_m < 60:
        advice.append({"cause": "整体策略", "pct": 0,
                       "text": "整体掌握度偏低，先用 2 周做基础排查：每个知识点自评 + 练 5 题，把漏洞全部暴露出来再谈提高。"})
    if practice_total < 20:
        advice.append({"cause": "练习量", "pct": 0,
                       "text": "练习记录尚少，数据越充分诊断越准。建议每天一组（10 题左右），两周后报告才有统计意义。"})
    if trend is not None and trend < -5:
        advice.append({"cause": "成绩趋势", "pct": 0,
                       "text": "近期成绩下滑，检查是否复习中断：错题本里的 active 题目是否堆积？优先清空到期复习。"})
    return render_template(
        "report.html", overall_m=overall_m, label=label, cls=cls, mods=mods, weak=weak,
        dist=dist, total=total, scores=scores, trend=trend,
        practice_total=practice_total, reviewed=reviewed, active_n=active_n, advice=advice,
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    saved = False
    pw_msg = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            set_cfg("stage", request.form.get("stage", "cj"))
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
                           minutes=cfg("minutes", "40"), stages=seed_data.STAGES)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8041)
