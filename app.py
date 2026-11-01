import os
import traceback
from flask import Flask, request, jsonify, render_template, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

# ---------- DB CONFIG ----------
db_url = os.getenv("DATABASE_URL", "").strip()

if not db_url:
    raise RuntimeError(
        "DATABASE_URL is not set! Add it in Vercel → Settings → Environment Variables, "
        "check the 'Production' box, then REDEPLOY."
    )

# SQLAlchemy refuses postgres://, must be postgresql://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

# Ensure psycopg2 driver is explicit (SQLAlchemy 2.1 defaults to psycopg v3 otherwise)
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

# Strip channel_binding which psycopg2 doesn't support
if "channel_binding=require" in db_url:
    db_url = db_url.replace("&channel_binding=require", "")
    db_url = db_url.replace("?channel_binding=require&", "?")
    db_url = db_url.replace("?channel_binding=require", "")

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}
db = SQLAlchemy(app)

ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip() or "CHANGE-ME-PLEASE"

# ---------- MODELS ----------
class Week(db.Model):
    __tablename__ = "week"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    order = db.Column(db.Integer, default=0)
    is_project = db.Column(db.Boolean, default=False)
    questions = db.relationship("Question", backref="week", cascade="all, delete-orphan")


class Question(db.Model):
    __tablename__ = "question"
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey("week.id"), nullable=False)
    
    # CHANGE THIS LINE from db.String(300) to db.Text
    title = db.Column(db.Text, nullable=False) 
    
    order = db.Column(db.Integer, default=0)
    full_theory = db.Column(db.Text, default="")
    conclusion = db.Column(db.Text, default="")
    solutions = db.relationship("Solution", backref="question", cascade="all, delete-orphan")


class Solution(db.Model):
    __tablename__ = "solution"
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # "steps" or "short_theory"
    content = db.Column(db.Text, nullable=False)
    user_name = db.Column(db.String(100), default="anonymous")
    votes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Upvote(db.Model):
    __tablename__ = "upvote"
    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solution.id"), nullable=False)
    voter_id = db.Column(db.String(100), nullable=False)
    __table_args__ = (db.UniqueConstraint("solution_id", "voter_id", name="uq_vote"),)

# ---------- DB INIT (once per cold start) ----------
_initialized = False

@app.before_request
def _init_db():
    global _initialized
    if _initialized:
        return
    try:
        with app.app_context():
            db.create_all()
        _initialized = True
    except Exception:
        _initialized = True
        print("=== DB INIT FAILED ===")
        traceback.print_exc()

# ---------- HELPERS ----------
def require_admin():
    key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    if not key or key != ADMIN_KEY:
        abort(403, description="Invalid or missing admin key")

# ---------- FRONTEND ROUTES ----------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/week/<int:week_id>")
def week_page(week_id):
    return render_template("week.html", week_id=week_id)


@app.route("/question/<int:qid>")
def question_page(qid):
    return render_template("question.html", qid=qid)


@app.route("/admin")
def admin_page():
    return render_template("admin.html")

# ---------- PUBLIC API ----------
@app.route("/api/weeks")
def api_weeks():
    weeks = Week.query.order_by(Week.order, Week.id).all()
    return jsonify([{
        "id": w.id, "name": w.name, "is_project": w.is_project,
        "question_count": len(w.questions),
    } for w in weeks])


@app.route("/api/weeks/<int:week_id>")
def api_week_detail(week_id):
    w = Week.query.get_or_404(week_id)
    qs = sorted(w.questions, key=lambda x: (x.order or 0, x.id))
    return jsonify({
        "id": w.id, "name": w.name,
        "questions": [{"id": q.id, "title": q.title} for q in qs],
    })


@app.route("/api/questions/<int:qid>")
def api_question(qid):
    q = Question.query.get_or_404(qid)
    return jsonify({
        "id": q.id, "title": q.title,
        "full_theory": q.full_theory or "",
        "conclusion": q.conclusion or "",
        "week": {"id": q.week.id, "name": q.week.name},
    })


@app.route("/api/solutions/<int:qid>")
def api_solutions(qid):
    stype = request.args.get("type")
    Question.query.get_or_404(qid)
    query = Solution.query.filter_by(question_id=qid)
    if stype in ("steps", "short_theory"):
        query = query.filter_by(type=stype)
    solutions = query.order_by(Solution.votes.desc(), Solution.created_at.asc()).all()
    return jsonify([{
        "id": s.id, "type": s.type, "content": s.content,
        "user_name": s.user_name, "votes": s.votes,
    } for s in solutions])


@app.route("/api/solutions", methods=["POST"])
def api_add_solution():
    data = request.get_json(force=True, silent=True) or {}
    qid = data.get("question_id")
    stype = data.get("type")
    content = (data.get("content") or "").strip()
    user_name = (data.get("user_name") or "anonymous").strip()[:100] or "anonymous"

    if not qid or stype not in ("steps", "short_theory") or not content:
        return jsonify({"error": "invalid payload"}), 400
    if not Question.query.get(qid):
        return jsonify({"error": "question not found"}), 404

    s = Solution(question_id=qid, type=stype, content=content, user_name=user_name)
    db.session.add(s)
    db.session.commit()
    return jsonify({"id": s.id, "votes": 0}), 201


@app.route("/api/upvote/<int:sid>", methods=["POST"])
def api_upvote(sid):
    data = request.get_json(force=True, silent=True) or {}
    voter_id = (data.get("voter_id") or "").strip()
    if not voter_id:
        return jsonify({"error": "voter_id required"}), 400

    s = Solution.query.get(sid)
    if not s:
        return jsonify({"error": "solution not found"}), 404

    existing = Upvote.query.filter_by(solution_id=sid, voter_id=voter_id).first()
    if existing:
        return jsonify({"error": "already voted", "votes": s.votes}), 409

    db.session.add(Upvote(solution_id=sid, voter_id=voter_id))
    s.votes = (s.votes or 0) + 1
    db.session.commit()
    return jsonify({"votes": s.votes})

# ---------- ADMIN API ----------
@app.route("/api/admin/verify", methods=["POST"])
def admin_verify():
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("key", "")
    if key != ADMIN_KEY:
        return jsonify({"ok": False}), 403
    return jsonify({"ok": True})


@app.route("/api/admin/week", methods=["POST"])
def admin_create_week():
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    w = Week(
        name=data.get("name", "Untitled"),
        order=data.get("order", 0),
        is_project=bool(data.get("is_project", False)),
    )
    db.session.add(w)
    db.session.commit()
    return jsonify({"id": w.id}), 201


@app.route("/api/admin/question", methods=["POST"])
def admin_create_question():
    require_admin()
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("week_id") or not data.get("title"):
        return jsonify({"error": "week_id and title required"}), 400
    if not Week.query.get(data["week_id"]):
        return jsonify({"error": "week not found"}), 404

    q = Question(
        week_id=data["week_id"],
        title=data["title"],
        order=data.get("order", 0),
        full_theory=data.get("full_theory", ""),
        conclusion=data.get("conclusion", ""),
    )
    db.session.add(q)
    db.session.commit()
    return jsonify({"id": q.id}), 201


@app.route("/api/admin/question/<int:qid>", methods=["PUT"])
def admin_update_question(qid):
    require_admin()
    q = Question.query.get_or_404(qid)
    data = request.get_json(force=True, silent=True) or {}
    if "title" in data:
        q.title = data["title"]
    if "full_theory" in data:
        q.full_theory = data["full_theory"]
    if "conclusion" in data:
        q.conclusion = data["conclusion"]
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/week/<int:wid>", methods=["DELETE"])
def admin_delete_week(wid):
    require_admin()
    w = Week.query.get_or_404(wid)
    db.session.delete(w)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/question/<int:qid>", methods=["DELETE"])
def admin_delete_question(qid):
    require_admin()
    q = Question.query.get_or_404(qid)
    db.session.delete(q)
    db.session.commit()
    return jsonify({"ok": True})

# ---------- DIAGNOSTIC ----------
@app.route("/api/health")
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
        db_ok = True
        db_err = None
    except Exception as e:
        db_ok = False
        db_err = str(e)

    return jsonify({
        "database_url_set": bool(os.getenv("DATABASE_URL")),
        "database_url_prefix": (os.getenv("DATABASE_URL") or "")[:20] + "...",
        "admin_key_set": ADMIN_KEY != "CHANGE-ME-PLEASE",
        "db_connection": "ok" if db_ok else "failed",
        "db_error": db_err,
    })

# ---------- LOCAL RUN ----------
if __name__ == "__main__":
    app.run(debug=True)
