import os, uuid, sqlite3
from functools import wraps
from urllib.parse import quote
import requests
from flask import Flask, request, jsonify, session, render_template, send_file
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "my-ai-studio-local-secret-2026")
app.config.update(
    SESSION_COOKIE_NAME="my_ai_studio_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    PERMANENT_SESSION_LIFETIME=60*60*24*30,
)

NEW_USER_CREDITS, IMAGE_COST, AUDIO_COST, VIDEO_COST = 100, 5, 8, 21
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATED_DIR = os.path.join(BASE_DIR, "generated")
os.makedirs(GENERATED_DIR, exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)
POLLINATIONS_API_KEY = os.environ.get("POLLINATIONS_API_KEY", "")
POLLINATIONS_BASE = "https://gen.pollinations.ai"
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "CHANDANADMIN")

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None


# ---------------- DATABASE ----------------

def ph(): return "%s" if USE_POSTGRES else "?"

def db():
    if USE_POSTGRES:
        if not psycopg2: raise RuntimeError("psycopg2 is not installed.")
        return psycopg2.connect(DATABASE_URL)
    c = sqlite3.connect(os.path.join(BASE_DIR, "users.db"))
    c.row_factory = sqlite3.Row
    return c

def q(sql, params=(), one=False, many=False, commit=False):
    c, cur = db(), None
    try:
        cur = c.cursor(cursor_factory=RealDictCursor) if USE_POSTGRES else c.cursor()
        cur.execute(sql, params)
        out = cur.fetchone() if one else cur.fetchall() if many else None
        if commit: c.commit()
        return out
    finally:
        if cur: cur.close()
        c.close()

def val(row, key, idx=0, default=None):
    if row is None: return default
    try: return row.get(key, default) if isinstance(row, dict) else row[key]
    except Exception:
        try: return row[idx]
        except Exception: return default

def cols(table):
    c, cur = db(), None
    try:
        cur = c.cursor()
        if USE_POSTGRES:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,))
            return {x[0] for x in cur.fetchall()}
        cur.execute(f"PRAGMA table_info({table})")
        return {x[1] for x in cur.fetchall()}
    finally:
        if cur: cur.close()
        c.close()

def add_col(table, name, definition):
    if name in cols(table): return
    c, cur = db(), None
    try:
        cur = c.cursor()
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        c.commit()
        print("Added missing column:", table, name)
    finally:
        if cur: cur.close()
        c.close()

def create_tables():
    c, cur = db(), None
    try:
        cur = c.cursor()
        if USE_POSTGRES:
            cur.execute("""CREATE TABLE IF NOT EXISTS users(
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 0, is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                blocked BOOLEAN NOT NULL DEFAULT FALSE, admin_initialized BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS generation_history(
                id SERIAL PRIMARY KEY, user_id INTEGER, type TEXT NOT NULL DEFAULT 'unknown',
                prompt TEXT NOT NULL DEFAULT '', credits INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS credit_history(
                id SERIAL PRIMARY KEY, user_id INTEGER, amount INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 0, is_admin INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0, admin_initialized INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS generation_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT NOT NULL DEFAULT 'unknown',
                prompt TEXT NOT NULL DEFAULT '', credits INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS credit_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        c.commit()
    finally:
        if cur: cur.close()
        c.close()

def migrate():
    create_tables()
    if USE_POSTGRES:
        specs = [
            ("users","is_admin","BOOLEAN NOT NULL DEFAULT FALSE"),
            ("users","blocked","BOOLEAN NOT NULL DEFAULT FALSE"),
            ("users","admin_initialized","BOOLEAN NOT NULL DEFAULT FALSE"),
            ("users","created_at","TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("generation_history","user_id","INTEGER"),
            ("generation_history","type","TEXT NOT NULL DEFAULT 'unknown'"),
            ("generation_history","prompt","TEXT NOT NULL DEFAULT ''"),
            ("generation_history","credits","INTEGER NOT NULL DEFAULT 0"),
            ("generation_history","created_at","TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("credit_history","user_id","INTEGER"),
            ("credit_history","amount","INTEGER NOT NULL DEFAULT 0"),
            ("credit_history","reason","TEXT NOT NULL DEFAULT ''"),
            ("credit_history","created_at","TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ]
    else:
        specs = [
            ("users","is_admin","INTEGER NOT NULL DEFAULT 0"),
            ("users","blocked","INTEGER NOT NULL DEFAULT 0"),
            ("users","admin_initialized","INTEGER NOT NULL DEFAULT 0"),
            ("users","created_at","TIMESTAMP"),
            ("generation_history","user_id","INTEGER DEFAULT 0"),
            ("generation_history","type","TEXT NOT NULL DEFAULT 'unknown'"),
            ("generation_history","prompt","TEXT NOT NULL DEFAULT ''"),
            ("generation_history","credits","INTEGER NOT NULL DEFAULT 0"),
            ("generation_history","created_at","TIMESTAMP"),
            ("credit_history","user_id","INTEGER DEFAULT 0"),
            ("credit_history","amount","INTEGER NOT NULL DEFAULT 0"),
            ("credit_history","reason","TEXT NOT NULL DEFAULT ''"),
            ("credit_history","created_at","TIMESTAMP"),
        ]
    for t, n, d in specs: add_col(t, n, d)
    print("Database migration completed.")

migrate()


# ---------------- HELPERS ----------------

def user_id_by_name(name):
    p = ph()
    return q(f"SELECT id,username,password,credits,is_admin,blocked,admin_initialized,created_at FROM users WHERE username={p}", (name,), one=True)

def user_id(uid):
    p = ph()
    return q(f"SELECT id,username,password,credits,is_admin,blocked,admin_initialized,created_at FROM users WHERE id={p}", (uid,), one=True)

def balance(uid):
    return int(val(user_id(uid), "credits", 3, 0) or 0)

def data():
    x = request.get_json(silent=True)
    if isinstance(x, dict): return x
    if request.form: return request.form.to_dict()
    raw = request.get_data(as_text=True).strip()
    return {"prompt": raw, "text": raw} if raw else {}

def login_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        u = user_id(session.get("user_id")) if session.get("user_id") else None
        if not u: session.clear(); return jsonify(error="Login required."), 401
        if val(u,"blocked",5,False): session.clear(); return jsonify(error="Your account is blocked."), 403
        return fn(*a, **kw)
    return w

def admin_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        u = user_id(session.get("user_id")) if session.get("user_id") else None
        if not u: session.clear(); return jsonify(error="Login required."), 401
        if val(u,"blocked",5,False): session.clear(); return jsonify(error="Your account is blocked."), 403
        if not val(u,"is_admin",4,False): return jsonify(error="Admin access required."), 403
        return fn(*a, **kw)
    return w

def add_credits(uid, amount, reason):
    uid, amount = int(uid), int(amount)
    if amount <= 0: return False
    p = ph(); c, cur = db(), None
    try:
        cur = c.cursor()
        cur.execute(f"UPDATE users SET credits=credits+{p} WHERE id={p}", (amount,uid))
        cur.execute(f"INSERT INTO credit_history(user_id,amount,reason) VALUES({p},{p},{p})", (uid,amount,reason))
        c.commit(); return True
    except Exception:
        c.rollback(); raise
    finally:
        if cur: cur.close()
        c.close()

def use_credits(uid, amount, reason):
    if uid is None: return False
    try: uid, amount = int(uid), int(amount)
    except Exception: return False
    if amount <= 0: return False
    p = ph(); c, cur = db(), None
    try:
        cur = c.cursor()
        cur.execute(f"UPDATE users SET credits=credits-{p} WHERE id={p} AND credits>={p}", (amount,uid,amount))
        if cur.rowcount != 1:
            c.rollback(); return False
        cur.execute(f"INSERT INTO credit_history(user_id,amount,reason) VALUES({p},{p},{p})", (uid,-amount,reason))
        c.commit(); return True
    except Exception as e:
        c.rollback(); print("USE CREDITS ERROR:", repr(e)); return False
    finally:
        if cur: cur.close()
        c.close()

def save_generation(uid, kind, prompt, cost):
    p = ph()
    q(f"INSERT INTO generation_history(user_id,type,prompt,credits) VALUES({p},{p},{p},{p})",
      (int(uid),kind,prompt,int(cost)), commit=True)

def headers():
    if not POLLINATIONS_API_KEY: raise RuntimeError("POLLINATIONS_API_KEY is not configured.")
    return {"Authorization":"Bearer "+POLLINATIONS_API_KEY,"User-Agent":"My-AI-Studio/1.0"}

# Make configured admin an admin if the account already exists.
# Also give the existing admin a one-time 100-credit starter balance if
# the account was created before the admin credit system existed.
_admin = user_id_by_name(ADMIN_USERNAME)
if _admin:
    p = ph()
    admin_value = True if USE_POSTGRES else 1
    false_value = False if USE_POSTGRES else 0
    q(f"UPDATE users SET is_admin={p}, blocked={p} WHERE username={p}",
      (admin_value, false_value, ADMIN_USERNAME), commit=True)

    # The bonus is protected by admin_initialized, so server restarts
    # cannot repeatedly add credits after the admin spends them.
    if not bool(val(_admin, "admin_initialized", 6, False)):
        if int(val(_admin, "credits", 3, 0) or 0) == 0:
            add_credits(val(_admin, "id", 0), NEW_USER_CREDITS, "One-time admin starter credits")
        q(f"UPDATE users SET admin_initialized={p} WHERE username={p}",
          (admin_value, ADMIN_USERNAME), commit=True)


# ---------------- ROUTES ----------------

@app.route("/")
def home(): return render_template("index.html")

@app.route("/health")
def health(): return jsonify(status="ok", service="My AI Studio")

@app.route("/register", methods=["POST"])
def register():
    d=data(); name=str(d.get("username","")).strip(); pw=str(d.get("password",""))
    if not name or not pw: return jsonify(error="Username and password are required."),400
    if len(name)<3: return jsonify(error="Username must be at least 3 characters."),400
    if len(pw)<6: return jsonify(error="Password must be at least 6 characters."),400
    if user_id_by_name(name): return jsonify(error="Username already exists."),409
    p=ph(); c,cur=db(),None
    try:
        cur=c.cursor()
        cur.execute(f"INSERT INTO users(username,password,credits,is_admin,blocked,admin_initialized) VALUES({p},{p},{p},{p},{p},{p})",
                    (name,generate_password_hash(pw),0,False if USE_POSTGRES else 0,False if USE_POSTGRES else 0,False if USE_POSTGRES else 0))
        c.commit()
    except Exception as e:
        c.rollback(); print("REGISTER ERROR:",repr(e)); return jsonify(error="Registration failed."),500
    finally:
        if cur: cur.close()
        c.close()
    u=user_id_by_name(name); add_credits(val(u,"id",0),NEW_USER_CREDITS,"New account bonus")
    return jsonify(success=True,message="Registration successful.")

@app.route("/login", methods=["POST"])
def login():
    d=data(); name=str(d.get("username","")).strip(); pw=str(d.get("password",""))
    if not name or not pw: return jsonify(error="Username and password are required."),400
    u=user_id_by_name(name)
    if not u: return jsonify(error="Invalid username or password."),401
    try: ok=check_password_hash(val(u,"password",2,""),pw)
    except Exception: ok=False
    if not ok: return jsonify(error="Invalid username or password."),401
    if val(u,"blocked",5,False): return jsonify(error="Your account is blocked."),403
    session.clear(); session.permanent=True
    session["user_id"]=int(val(u,"id",0)); session["username"]=name; session["is_admin"]=bool(val(u,"is_admin",4,False)); session.modified=True
    return jsonify(success=True,logged_in=True,username=name,credits=balance(val(u,"id",0)),is_admin=bool(val(u,"is_admin",4,False)))

@app.route("/me")
def me():
    u=user_id(session.get("user_id")) if session.get("user_id") else None
    if not u or val(u,"blocked",5,False): session.clear(); return jsonify(logged_in=False)
    return jsonify(logged_in=True,username=val(u,"username",1,""),credits=val(u,"credits",3,0),is_admin=bool(val(u,"is_admin",4,False)))

@app.route("/logout", methods=["POST"])
def logout(): session.clear(); return jsonify(success=True)

@app.route("/credits")
@login_required
def get_credits(): return jsonify(credits=balance(session["user_id"]))

@app.route("/generate", methods=["POST"])
@login_required
def generate_image():
    d=data()
    prompt=str(d.get("prompt",d.get("text",""))).strip()
    if not prompt: return jsonify(error="Prompt is required."),400
    uid=session["user_id"]
    if balance(uid)<IMAGE_COST or not use_credits(uid,IMAGE_COST,"Image generation"):
        return jsonify(error=f"Not enough credits. Your balance is {balance(uid)} credits; this generation requires {IMAGE_COST} credits."),402
    filename=uuid.uuid4().hex+".png"; path=os.path.join(GENERATED_DIR,filename)
    try:
        r=requests.get(POLLINATIONS_BASE+"/image/"+quote(prompt,safe=""),headers=headers(),
                       params={"model":"black-forest-labs/flux.1-schnell","width":1024,"height":1024,"n":1},timeout=300)
        r.raise_for_status()
        open(path,"wb").write(r.content)
        save_generation(uid,"image",prompt,IMAGE_COST)
        return jsonify(success=True,type="image",credits=balance(uid),url="/generated/"+filename)
    except Exception as e:
        print("IMAGE GENERATION ERROR:",repr(e)); add_credits(uid,IMAGE_COST,"Image generation refund")
        return jsonify(error="Image generation failed."),500

@app.route("/audio", methods=["POST"])
@login_required
def audio():
    d=data(); text=str(d.get("text",d.get("prompt",""))).strip()
    if not text: return jsonify(error="Text is required."),400
    uid=session["user_id"]
    if balance(uid)<AUDIO_COST or not use_credits(uid,AUDIO_COST,"Audio generation"): return jsonify(error=f"Not enough credits. Your balance is {balance(uid)} credits; this generation requires {AUDIO_COST} credits."),402
    filename=uuid.uuid4().hex+".mp3"; path=os.path.join(GENERATED_DIR,filename)
    try:
        r=requests.get(POLLINATIONS_BASE+"/audio/"+quote(text,safe=""),headers=headers(),params={"voice":"nova"},timeout=300)
        r.raise_for_status(); open(path,"wb").write(r.content); save_generation(uid,"audio",text,AUDIO_COST)
        return jsonify(success=True,type="audio",credits=balance(uid),url="/generated/"+filename)
    except Exception as e:
        print("AUDIO GENERATION ERROR:",repr(e)); add_credits(uid,AUDIO_COST,"Audio generation refund")
        return jsonify(error="Audio generation failed."),500

@app.route("/video", methods=["POST"])
@login_required
def video():
    d=data(); prompt=str(d.get("prompt",d.get("text",""))).strip()
    if not prompt: return jsonify(error="Prompt is required."),400
    uid=session["user_id"]
    if balance(uid)<VIDEO_COST or not use_credits(uid,VIDEO_COST,"Video generation"): return jsonify(error=f"Not enough credits. Your balance is {balance(uid)} credits; this generation requires {VIDEO_COST} credits."),402
    filename=uuid.uuid4().hex+".mp4"; path=os.path.join(GENERATED_DIR,filename)
    try:
        r=requests.get(POLLINATIONS_BASE+"/video/"+quote(prompt,safe=""),headers=headers(),
                       params={"model":"google/veo-3.1-fast","duration":4,"aspectRatio":"16:9","audio":"false"},timeout=600)
        r.raise_for_status(); open(path,"wb").write(r.content); save_generation(uid,"video",prompt,VIDEO_COST)
        return jsonify(success=True,type="video",credits=balance(uid),url="/generated/"+filename)
    except Exception as e:
        print("VIDEO GENERATION ERROR:",repr(e)); add_credits(uid,VIDEO_COST,"Video generation refund")
        return jsonify(error="Video generation failed."),500

@app.route("/generated/<path:filename>")
def generated(filename):
    path=os.path.join(GENERATED_DIR,os.path.basename(filename))
    if not os.path.isfile(path): return jsonify(error="File not found."),404
    return send_file(path)

@app.route("/history")
@login_required
def history():
    p=ph()
    rows=q(f"SELECT type,prompt,credits,created_at FROM generation_history WHERE user_id={p} ORDER BY id DESC LIMIT 100",
           (session["user_id"],),many=True)
    return jsonify(history=[{"type":val(r,"type",0,""),"prompt":val(r,"prompt",1,""),"credits":val(r,"credits",2,0),"created_at":str(val(r,"created_at",3,""))} for r in rows or []])

@app.route("/admin/stats")
@admin_required
def admin_stats():
    a=q("SELECT COUNT(*) total FROM users",one=True); b=q("SELECT COUNT(*) total FROM generation_history",one=True)
    c=q("SELECT COALESCE(SUM(credits),0) total FROM users",one=True)
    d=q("SELECT COUNT(*) total FROM users WHERE blocked="+("TRUE" if USE_POSTGRES else "1"),one=True)
    return jsonify(total_users=val(a,"total",0,0),total_generations=val(b,"total",0,0),total_credits=val(c,"total",0,0),blocked_users=val(d,"total",0,0))

@app.route("/admin/users")
@admin_required
def admin_users():
    rows=q("SELECT id,username,credits,is_admin,blocked,created_at FROM users ORDER BY id DESC",many=True)
    return jsonify(users=[{"id":val(r,"id",0,0),"username":val(r,"username",1,""),"credits":val(r,"credits",2,0),"is_admin":bool(val(r,"is_admin",3,False)),"blocked":bool(val(r,"blocked",4,False)),"created_at":str(val(r,"created_at",5,""))} for r in rows or []])

@app.route("/admin/credits", methods=["POST"])
@admin_required
def admin_credits():
    d=data()
    try: uid=int(d.get("user_id")); amount=int(d.get("amount"))
    except Exception: return jsonify(error="Invalid user ID or amount."),400
    reason=str(d.get("reason","Admin credit adjustment")).strip(); u=user_id(uid)
    if not u: return jsonify(error="User not found."),404
    if int(val(u,"credits",3,0) or 0)+amount<0: return jsonify(error="Credits cannot become negative."),400
    ok=add_credits(uid,amount,reason) if amount>0 else use_credits(uid,abs(amount),reason)
    return jsonify(success=True,credits=balance(uid)) if ok else (jsonify(error="Credit adjustment failed."),400)

@app.route("/admin/block", methods=["POST"])
@admin_required
def admin_block():
    d=data()
    try: uid=int(d.get("user_id"))
    except Exception: return jsonify(error="Invalid user ID."),400
    u=user_id(uid)
    if not u: return jsonify(error="User not found."),404
    if val(u,"username",1,"")==ADMIN_USERNAME: return jsonify(error="Configured admin cannot be blocked."),400
    blocked=bool(d.get("blocked",True)); p=ph()
    q(f"UPDATE users SET blocked={p} WHERE id={p}",(blocked if USE_POSTGRES else int(blocked),uid),commit=True)
    return jsonify(success=True,blocked=blocked)

@app.route("/admin/generations")
@admin_required
def admin_generations():
    rows=q("""SELECT generation_history.type,generation_history.prompt,generation_history.credits,
              generation_history.created_at,users.username FROM generation_history
              JOIN users ON users.id=generation_history.user_id
              ORDER BY generation_history.id DESC LIMIT 500""",many=True)
    return jsonify(generations=[{"username":val(r,"username",4,""),"type":val(r,"type",0,""),"prompt":val(r,"prompt",1,""),"credits":val(r,"credits",2,0),"created_at":str(val(r,"created_at",3,""))} for r in rows or []])

@app.route("/admin/credits/history")
@admin_required
def admin_credit_history():
    rows=q("""SELECT credit_history.amount,credit_history.reason,credit_history.created_at,users.username
              FROM credit_history JOIN users ON users.id=credit_history.user_id
              ORDER BY credit_history.id DESC LIMIT 500""",many=True)
    return jsonify(history=[{"username":val(r,"username",3,""),"amount":val(r,"amount",0,0),"reason":val(r,"reason",1,""),"created_at":str(val(r,"created_at",2,""))} for r in rows or []])

@app.route("/recharge",methods=["POST"])
@login_required
def recharge(): return jsonify(error="Online payment gateway is not connected yet."),501

@app.route("/about")
def about(): return jsonify(name="My AI Studio",description="AI Image, Video and Audio Studio")
@app.route("/contact")
def contact(): return jsonify(message="Contact My AI Studio")
@app.route("/privacy")
def privacy(): return jsonify(privacy="Privacy information for My AI Studio")
@app.route("/terms")
def terms(): return jsonify(terms="Terms and conditions for My AI Studio")
@app.route("/robots.txt")
def robots(): return "User-agent: *\nAllow: /\n",200,{"Content-Type":"text/plain"}

@app.errorhandler(404)
def e404(e): return jsonify(error="Page not found"),404
@app.errorhandler(500)
def e500(e):
    print("INTERNAL SERVER ERROR:",repr(e))
    return jsonify(error="Internal server error"),500

if __name__=="__main__":
    app.run(host="127.0.0.1",port=5000,debug=True)
