"""AI 고객응대 챗봇 데모 — "똑같은 말 100번 반복하지 마세요"

실행: ./run.sh  (또는 python3 app.py --port 5006)
- 첫 화면: /
- 손님용 채팅: /chat
- 사장님 관리 화면: /admin
"""
import argparse
import os
import secrets
import traceback
from datetime import datetime, timedelta

from flask import Flask, jsonify, redirect, render_template, request, session

import db
from chatbot import Engine, get_business

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "daangn-demo-06-secret")
engine = Engine()

db.init_db()


def _sess():
    """대화 상태 (없으면 초기화)"""
    if "flow" not in session:
        session["flow"] = None
        session["step"] = None
        session["data"] = {}
    if "business_id" not in session:
        session["business_id"] = 1
    return session


# ---------------- 손님용 ----------------
@app.route("/")
def index():
    return render_template("landing.html")


@app.route("/chat")
def chat_page():
    return render_template("chat.html")


@app.route("/api/businesses")
def api_businesses():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, btype, name, emoji FROM businesses ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/business", methods=["POST"])
def api_switch_business():
    data = request.get_json(force=True, silent=True) or {}
    b = get_business(data.get("business_id", 1))
    if not b:
        return jsonify({"ok": False}), 400
    s = _sess()
    s["business_id"] = b["id"]
    s["flow"], s["step"], s["data"] = None, None, {}
    return jsonify({"ok": True, "business": dict(b), "welcome": b["welcome"]})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        data = request.get_json(force=True, silent=True) or {}
        s = _sess()
        message = (data.get("message") or "").strip()
        if len(message) > 500:
            return jsonify({"reply": "질문이 너무 길어요. 짧게 다시 물어봐주세요 😊"})
        result = engine.respond(s["business_id"], message, s)
        session.modified = True
        return jsonify(result)
    except Exception:
        traceback.print_exc()
        # 손님에게는 절대 에러 화면을 보여주지 않음
        return jsonify({
            "reply": "앗, 잠깐 문제가 생겼어요 🙏\n"
                     "다시 한 번 말씀해주시겠어요?\n"
                     "급하시면 사장님께 직접 연락주세요!"
        })


@app.route("/api/reception_stats")
def api_reception_stats():
    """이번 달 AI 직원이 받아낸 예약 건수 + 예상 매출 (가격표 기준 합산).

    business_id=0 이면 세 가게 합산."""
    bid = request.args.get("business_id", 1, type=int)
    month = datetime.now().strftime("%Y-%m")
    conn = db.get_conn()
    base = """SELECT COUNT(*) AS c, COALESCE(SUM(s.price), 0) AS rev
              FROM reservations r LEFT JOIN services s
                ON s.business_id = r.business_id AND s.name = r.service"""
    if bid == 0:
        row = conn.execute(base + " WHERE r.created_at LIKE ?",
                           (month + "%",)).fetchone()
    else:
        row = conn.execute(
            base + " WHERE r.business_id = ? AND r.created_at LIKE ?",
            (bid, month + "%")).fetchone()
    conn.close()
    return jsonify({"count": row["c"], "revenue": row["rev"]})


# ---------------- 사장님 관리 화면 ----------------
@app.route("/admin")
def admin_page():
    return render_template("admin.html")


@app.route("/api/admin/briefing")
def api_briefing():
    """사장님 오늘 브리핑: 오늘 답한 질문·아낀 시간·어제 비교·7일 추이·월간 리포트"""
    bid = request.args.get("business_id", 1, type=int)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    conn = db.get_conn()

    def cnt(sql, params):
        return conn.execute(sql, params).fetchone()[0]

    today_a = cnt(
        "SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND kind='answered'"
        " AND created_at LIKE ?", (bid, today + "%"))
    today_h = cnt(
        "SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND kind='handoff'"
        " AND created_at LIKE ?", (bid, today + "%"))
    yest_a = cnt(
        "SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND kind='answered'"
        " AND created_at LIKE ?", (bid, yesterday + "%"))
    week = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        week.append({
            "label": "오늘" if i == 0 else f"{int(d[8:])}일",
            "count": cnt(
                "SELECT COUNT(*) FROM chat_logs WHERE business_id=?"
                " AND created_at LIKE ?", (bid, d + "%")),
        })
    top_today = conn.execute(
        """SELECT question, COUNT(*) AS c FROM chat_logs
           WHERE business_id=? AND created_at LIKE ?
           GROUP BY question ORDER BY c DESC LIMIT 1""",
        (bid, today + "%")).fetchone()
    month_a = cnt(
        "SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND kind='answered'"
        " AND created_at LIKE ?", (bid, month + "%"))
    month_r = conn.execute(
        """SELECT COUNT(*) AS c, COALESCE(SUM(s.price), 0) AS rev
           FROM reservations r LEFT JOIN services s
             ON s.business_id = r.business_id AND s.name = r.service
           WHERE r.business_id=? AND r.created_at LIKE ?""",
        (bid, month + "%")).fetchone()
    conn.close()

    saved_min = today_a * 3  # 질문 1건당 3분 절약 기준
    month_hours = round(month_a * 3 / 60, 1)
    # 재미 요소: 이번 달 성적표
    if month_a >= 100:
        grade, grade_msg = "A+", "전설의 AI 직원이에요! 🏆"
    elif month_a >= 50:
        grade, grade_msg = "A", "사장님의 든든한 오른팔이에요 💪"
    elif month_a >= 20:
        grade, grade_msg = "B+", "꽤 잘하고 있어요! 😊"
    elif month_a >= 1:
        grade, grade_msg = "B", "이제 막 일을 배우는 중이에요 🌱"
    else:
        grade, grade_msg = "-", "손님이 오면 바로 일할 준비가 돼 있어요 🙂"
    return jsonify({
        "today_answered": today_a,
        "today_handoff": today_h,
        "saved_min": saved_min,
        "delta": today_a - yest_a,
        "week": week,
        "top_today": dict(top_today) if top_today else None,
        "month_answered": month_a,
        "month_hours": month_hours,
        "month_reservations": month_r["c"],
        "month_revenue": month_r["rev"],
        "grade": grade,
        "grade_msg": grade_msg,
    })


@app.route("/api/admin/overview")
def api_overview():
    conn = db.get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    bid = request.args.get("business_id", 1, type=int)
    q = lambda sql, p=(bid,): conn.execute(sql, p).fetchone()[0]  # noqa: E731
    total = q("SELECT COUNT(*) FROM chat_logs WHERE business_id=?")
    answered = q("SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND kind='answered'")
    handoff = q("SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND kind='handoff'")
    today_q = conn.execute(
        "SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND created_at LIKE ?",
        (bid, today + "%")).fetchone()[0]
    top = conn.execute(
        """SELECT p.question, COALESCE(f.count,0) AS c
           FROM faq_stats f JOIN qa_patterns p ON p.id=f.pattern_id
           WHERE p.business_id=? ORDER BY c DESC LIMIT 10""", (bid,)).fetchall()
    unans = conn.execute(
        "SELECT id, question, created_at FROM unanswered"
        " WHERE business_id=? AND resolved=0 ORDER BY id DESC LIMIT 50",
        (bid,)).fetchall()
    resv = conn.execute(
        "SELECT * FROM reservations WHERE business_id=? ORDER BY id DESC LIMIT 50",
        (bid,)).fetchall()
    inq = conn.execute(
        "SELECT * FROM inquiries WHERE business_id=? ORDER BY id DESC LIMIT 50",
        (bid,)).fetchall()
    b = conn.execute("SELECT * FROM businesses WHERE id=?", (bid,)).fetchone()
    conn.close()
    return jsonify({
        "business": dict(b),
        "stats": {"total": total, "answered": answered, "handoff": handoff,
                  "today": today_q},
        "top_faq": [dict(r) for r in top],
        "unanswered": [dict(r) for r in unans],
        "reservations": [dict(r) for r in resv],
        "inquiries": [dict(r) for r in inq],
    })


@app.route("/api/admin/services", methods=["GET", "POST"])
def api_services():
    if request.method == "GET":
        bid = request.args.get("business_id", 1, type=int)
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT * FROM services WHERE business_id=? ORDER BY id",
            (bid,)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    data = request.get_json(force=True, silent=True) or {}
    bid = data.get("business_id", 1)
    items = data.get("services", [])
    conn = db.get_conn()
    keep_ids = [it["id"] for it in items if it.get("id")]
    if keep_ids:
        conn.execute(
            f"DELETE FROM services WHERE business_id=? AND id NOT IN"
            f" ({','.join('?' * len(keep_ids))})", (bid, *keep_ids))
    else:
        conn.execute("DELETE FROM services WHERE business_id=?", (bid,))
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        try:
            price = int(str(it.get("price", 0)).replace(",", ""))
        except ValueError:
            price = 0
        if it.get("id"):
            conn.execute(
                "UPDATE services SET name=?, price=?, duration=?, note=?"
                " WHERE id=? AND business_id=?",
                (name, price, it.get("duration", ""), it.get("note", ""),
                 it["id"], bid))
        else:
            conn.execute(
                "INSERT INTO services (business_id, name, price, duration, note, keywords)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (bid, name, price, it.get("duration", ""), it.get("note", ""),
                 name.replace(" ", "")))
    conn.commit()
    conn.close()
    engine.refresh(bid)
    return jsonify({"ok": True})


@app.route("/api/admin/business", methods=["GET", "POST"])
def api_business():
    if request.method == "GET":
        bid = request.args.get("business_id", 1, type=int)
        b = get_business(bid)
        return jsonify(dict(b) if b else {})
    data = request.get_json(force=True, silent=True) or {}
    bid = data.get("business_id", 1)
    fields = ["phone", "address", "hours_weekday", "hours_sat", "hours_sun",
              "parking", "payment", "cancel_policy", "welcome"]
    conn = db.get_conn()
    conn.execute(
        f"UPDATE businesses SET {', '.join(f+'=?' for f in fields)} WHERE id=?",
        tuple(data.get(f, "") for f in fields) + (bid,))
    conn.commit()
    conn.close()
    engine.refresh(bid)
    return jsonify({"ok": True})


@app.route("/api/admin/unanswered/answer", methods=["POST"])
def api_answer_unanswered():
    data = request.get_json(force=True, silent=True) or {}
    uid, answer = data.get("id"), (data.get("answer") or "").strip()
    if not uid or not answer:
        return jsonify({"ok": False, "msg": "답변을 입력해주세요."}), 400
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM unanswered WHERE id=?",
                       (uid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False}), 404
    conn.execute(
        "INSERT INTO qa_patterns (business_id, question, answer, kind)"
        " VALUES (?, ?, ?, 'faq')",
        (row["business_id"], row["question"], answer))
    conn.execute("UPDATE unanswered SET resolved=1 WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    engine.refresh(row["business_id"])
    return jsonify({"ok": True})


@app.route("/api/admin/done", methods=["POST"])
def api_done():
    data = request.get_json(force=True, silent=True) or {}
    kind, rid = data.get("kind"), data.get("id")
    table = {"reservation": "reservations", "inquiry": "inquiries"}.get(kind)
    if not table or not rid:
        return jsonify({"ok": False}), 400
    conn = db.get_conn()
    conn.execute(f"UPDATE {table} SET done=1 WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------- 바이럴 · 감동 · 입소문 ----------------
SYSTEM_NAME = "AI 직원"
BADGE_TEXT = "이 가게는 AI 직원 시스템을 사용합니다 💬"


def fomo_count():
    """경쟁 FOMO: 같은 동네에서 쓰고 있는 가게 수 (날짜 기준 고정값)."""
    doy = datetime.now().timetuple().tm_yday
    return 6 + (doy % 4)  # 6~9


def greeting_for(now):
    """매일 루틴 인사: 아침/저녁/월말."""
    last_day = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    days_left = (last_day - now).days
    if days_left <= 2:
        return "🌟 이번 달도 정말 수고 많으셨어요. 사장님 최고예요!"
    if 5 <= now.hour < 12:
        return "☀️ 좋은 아침이에요. 오늘도 힘내세요 😊"
    if now.hour >= 18:
        return "🌙 오늘 하루도 수고하셨어요. 푹 쉬세요."
    return "😊 오늘도 AI 직원이 곁에서 도와드릴게요."


@app.route("/referral")
def referral_page():
    """바이럴 허브: 추천 리워드 + 소개 링크 생성 + 경쟁 FOMO + 성장 시뮬레이터."""
    return render_template("referral.html", fomo=fomo_count())


@app.route("/api/referral", methods=["POST"])
def api_referral_create():
    """소개 링크 생성: '옆 가게 소개하면 둘 다 1개월 무료'"""
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()[:20]
    code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8] or secrets.token_hex(4)
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO referrals (code, name, created_at) VALUES (?, ?, ?)",
            (code, name, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
    except Exception:
        conn.close()
        return jsonify({"ok": False, "message": "다시 눌러주세요 🙏"}), 500
    conn.close()
    link = request.host_url.rstrip("/") + "/ref/" + code
    return jsonify({"ok": True, "code": code, "link": link})


@app.route("/api/referral/<code>")
def api_referral_status(code):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM referrals WHERE code=?", (code,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"ok": False}), 404
    return jsonify({"ok": True, "name": row["name"],
                    "referred_count": row["referred_count"]})


@app.route("/ref/<code>")
def referral_landing(code):
    """소개받은 사장님의 진입 화면 (FOMO + 체험 CTA)."""
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM referrals WHERE code=?", (code,)).fetchone()
    conn.close()
    if not row:
        return render_template("chat.html"), 404
    seen = session.get("ref_seen") or []
    if code not in seen:
        conn = db.get_conn()
        conn.execute("UPDATE referrals SET referred_count = referred_count + 1"
                     " WHERE code=?", (code,))
        conn.commit()
        conn.close()
        seen.append(code)
        session["ref_seen"] = seen
    referrer = row["name"] or "옆 가게 사장님"
    return render_template("ref.html", referrer=referrer, fomo=fomo_count())


@app.route("/about")
def about_page():
    """레거시 철학: 왜 만들었는가 · 7가지 가치 원칙 · 존엄 · 가게 이어받기."""
    return render_template("about.html")


@app.route("/report")
def report_page():
    """월간 리포트: '단톡방에 공유하기' 버튼."""
    return render_template("report.html")


@app.route("/api/admin/touch")
def api_touch():
    """쓰나미 모먼트: 오늘의 감동 브리핑 (감정톤 · 루틴 인사 · 존엄 카운트)."""
    bid = request.args.get("business_id", 1, type=int)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    conn = db.get_conn()
    today_a = conn.execute(
        "SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND kind='answered'"
        " AND created_at LIKE ?", (bid, today + "%")).fetchone()[0]
    month_a = conn.execute(
        "SELECT COUNT(*) FROM chat_logs WHERE business_id=? AND kind='answered'"
        " AND created_at LIKE ?", (bid, month + "%")).fetchone()[0]
    month_r = conn.execute(
        "SELECT COUNT(*) FROM reservations WHERE business_id=?"
        " AND created_at LIKE ?", (bid, month + "%")).fetchone()[0]
    conn.close()
    if today_a > 0:
        tsunami = (f"오늘 {SYSTEM_NAME}가 {today_a}개의 질문에 답했어요. "
                   "사장님은 일에만 집중하셨어요. 수고하셨어요. 🙏")
    else:
        tsunami = (f"오늘은 아직 질문이 없네요. {SYSTEM_NAME}가 대기 중이에요. "
                   "손님이 오시면 바로 답해드릴게요. 편하게 계세요 😊")
    dignity = (f"오늘 {today_a}명의 손님이 사장님 덕분에 "
               "기다림 없이 바로 답을 받았어요.")
    return jsonify({
        "greeting": greeting_for(now),
        "tsunami": tsunami,
        "dignity": dignity,
        "today_answered": today_a,
        "month_answered": month_a,
        "month_reservations": month_r,
        "badge_text": BADGE_TEXT,
        "fomo": fomo_count(),
    })


# ---------------- 에러 처리 (traceback 노출 금지) ----------------
@app.errorhandler(404)
def not_found(e):
    return render_template("chat.html"), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"reply": "잠깐 문제가 생겼어요. 다시 시도해주세요 🙏"}), 500


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5006)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=False)
