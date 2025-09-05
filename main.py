from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from questions import QUESTION_LOOKUP
import aiosqlite
from datetime import datetime
from starlette.middleware.sessions import SessionMiddleware
import json, random, os, metrics, uuid, io, calculation
from urllib.parse import urlparse
from fastapi import Query
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# ---- NEW: imports for the middleware ----
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from fastapi.responses import StreamingResponse
from typing import Any
from opencc import OpenCC
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageFilter

CAPTCHA_TTL = 180
CAPTCHA_W, CAPTCHA_H = 180, 60
CAPTCHA_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
def _captcha_text(n=5):
    # Avoid ambiguous: 0 O 1 l I 5 S
    alphabet = "2346789ABCDEFGHJKLMNPQRTUVWXYZ"
    return "".join(random.choice(alphabet) for _ in range(n))

def _load_font(size=36) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        try:
            if p.exists():
                f = ImageFont.truetype(str(p), size)
                # quick sanity: measure an 'A'
                test = Image.new("RGB", (1, 1))
                db = ImageDraw.Draw(test)
                bbox = db.textbbox((0,0), "A", font=f)
                if bbox and bbox[2] > 0 and bbox[3] > 0:
                    return f
        except Exception:
            pass
    # Final fallback (bitmap) – still returns *something*
    return ImageFont.load_default()
CAPTCHA_SECRET = os.getenv("CAPTCHA_SECRET", os.getenv("SESSION_SECRET_KEY", "dev-secret"))
CAPTCHA_SIGNER = TimestampSigner(CAPTCHA_SECRET)


def make_captcha_token(text: str) -> str:
    return CAPTCHA_SIGNER.sign(text.encode("utf-8")).decode("utf-8")

def read_captcha_token(token: str) -> str:
    # raises SignatureExpired/BadSignature on failure
    return CAPTCHA_SIGNER.unsign(token, max_age=CAPTCHA_TTL).decode("utf-8")


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
BANNER_W, BANNER_H = 1500, 500
BANNER_BG = BASE_DIR / "static" / "images" / "banner_bg.png"
BADGE_DIR = BASE_DIR / "static" / "images" / "badges"
METRIC_BADGES = metrics.METRICS_BADGE

BADGE_SIZE = (180, 200)  # (w,h) each badge
BADGE_POS = [
    (380,  145),
    (600,  145),
    (820,  145),
    (1040, 145),
    (1260, 145),
]
# ⬇️ Use BASE_DIR here
FONT_CANDIDATES = [
    BASE_DIR / "static" / "fonts" / "DejaVuSans-Bold.ttf",           # bundled font
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),    # system fallback
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect("data.db") as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          session_id TEXT,
          client_id TEXT,
          ip TEXT,
          ua TEXT,
          event TEXT NOT NULL,
          url TEXT,
          referrer TEXT,
          props TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_events_event ON events(event);
        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
        """)
        await db.commit()
    yield

with open('questions.json') as f:
    METRIC_CONFIG = json.load(f)

app = FastAPI(lifespan=lifespan)
app.router.lifespan_context = lifespan
if os.getenv("ENV") == "dev":
    app.mount("/static", StaticFiles(directory="static"), name="static")
    
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY"))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# proxy headers before other middlewares
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# cookie security flag
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"

# ---------- NEW: OpenCC middleware (Simplified → Traditional) ----------
# Choose a profile:
# 's2t'  = Simplified → General Traditional
# 's2tw' = Simplified → Taiwan Traditional (phrases localized)
# 's2hk' = Simplified → Hong Kong Traditional
CC_PROFILE = "s2tw"
_cc = OpenCC(CC_PROFILE)

def _convert_str(s: str) -> str:
    try:
        return _cc.convert(s)
    except Exception:
        return s  # fail-safe

def _convert_json(obj: Any) -> Any:
    if isinstance(obj, str):
        return _convert_str(obj)
    if isinstance(obj, list):
        return [_convert_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _convert_json(v) for k, v in obj.items()}
    return obj

def _should_skip(request: Request) -> bool:
    """
    Skip conversion when:
      - Opt-out via query (?lang=zh-CN / ?lang=cn / ?lang=simp)
      - Opt-out via cookie lang=zh-CN
      - Static assets under /static
    """
    lang = (request.query_params.get("lang") or "").lower()
    if lang in {"zh-cn", "cn", "simp", "simple", "sc"}:
        return True

    cookie_lang = (request.cookies.get("lang") or "").lower()
    if cookie_lang in {"zh-cn", "cn", "simp", "simple", "sc"}:
        return True

    if request.url.path.startswith("/static"):
        return True

    return False

def _safe_headers(headers) -> dict:
    d = dict(headers)
    # Remove headers that would be stale after transformation
    d.pop("content-length", None)
    d.pop("Content-Length", None)
    d.pop("content-encoding", None)
    d.pop("Content-Encoding", None)
    return d

CAPTCHA_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

class OpenCCMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip conversion for opt-out and /static
        if _should_skip(request):
            return await call_next(request)

        response = await call_next(request)

        # 🚫 Never touch images or other non-text media
        mt = (response.media_type or "").lower()
        if request.url.path == "/captcha.png" or request.url.path == "/banner.png":
            return response
            
        if mt.startswith("image/") or mt.startswith("video/") or mt.startswith("audio/"):
            return response
        media_type = (response.media_type or "").lower()
        # ✅ Streams: leave them alone
        if isinstance(response, StreamingResponse):
            return response

        # Handle JSON
        if isinstance(response, JSONResponse) or "application/json" in media_type:
            try:
                body_bytes = b""
                async for chunk in response.body_iterator:
                    body_bytes += chunk

                charset = response.charset or "utf-8"
                data = json.loads(body_bytes.decode(charset, errors="ignore"))
                converted = _convert_json(data)

                new_resp = JSONResponse(
                    content=converted,
                    status_code=response.status_code,
                    headers=_safe_headers(response.headers),
                    background=response.background,
                )
                return new_resp
            except Exception:
                # Fallback to original
                try:
                    return Response(
                        content=body_bytes,
                        status_code=response.status_code,
                        headers=_safe_headers(response.headers),
                        media_type=response.media_type,
                        background=response.background,
                    )
                except Exception:
                    return response

        # Text-like / HTML
        text_like_types = (
            "text/plain",
            "text/html",
            "application/xhtml+xml",
            "application/xml",
            "text/xml",
            "text/css",
            "application/javascript",
            "text/javascript",
        )
        if isinstance(response, (PlainTextResponse, HTMLResponse)) or any(
            t in media_type for t in text_like_types
        ):
            try:
                body_bytes = b""
                async for chunk in response.body_iterator:
                    body_bytes += chunk
                charset = response.charset or "utf-8"
                original_text = body_bytes.decode(charset, errors="ignore")
                converted_text = _convert_str(original_text)

                return Response(
                    content=converted_text.encode(charset),
                    status_code=response.status_code,
                    headers=_safe_headers(response.headers),
                    media_type=response.media_type,
                    background=response.background,
                )
            except Exception:
                return response

        # Skip streaming/binary
        if isinstance(response, StreamingResponse):
            return response
        if media_type and not media_type.startswith("text/") and "json" not in media_type:
            return response

        # Default best-effort as text
        try:
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk
            charset = response.charset or "utf-8"
            text = body_bytes.decode(charset, errors="ignore")
            converted_text = _convert_str(text)
            return Response(
                content=converted_text.encode(charset),
                status_code=response.status_code,
                headers=_safe_headers(response.headers),
                media_type=response.media_type,
                background=response.background,
            )
        except Exception:
            return response
# ----------------------------------------------------------------------

# IMPORTANT: add OpenCC BEFORE GZip so we convert then compress
app.add_middleware(OpenCCMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)

templates = Jinja2Templates(directory="templates")
templates.env.globals["CURRENT_YEAR"] = datetime.utcnow().year
templates.env.auto_reload = False  # Enable auto reload
templates.env.cache = {}
# Optional: Jinja2 filter to convert specific fragments in templates
templates.env.filters["s2t"] = _convert_str

QUESTIONS = [QUESTION_LOOKUP[i] for i in range(1, len(QUESTION_LOOKUP)+1)]
QUESTION_IDS = list(range(1, len(QUESTIONS) + 1))

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    err = request.session.pop("start_error", None)
    txt = _captcha_text()                     # e.g., 5 chars
    token = make_captcha_token(txt)

    return templates.TemplateResponse(
        "land.html",
        {"request": request, "error": err, "captcha_token": token}
    )


@app.get("/quiz", response_class=HTMLResponse)
async def show_quiz_page(request: Request, page: int = 1):
    per_page = 7

    if page == 1 or 'shuffled_question_ids' not in request.session:
        shuffled_question_ids = QUESTION_IDS.copy()
        random.shuffle(shuffled_question_ids)
        request.session['shuffled_question_ids'] = shuffled_question_ids
    else:
        shuffled_question_ids = request.session.get('shuffled_question_ids', QUESTION_IDS.copy())

    start = (page - 1) * per_page
    end = start + per_page

    current_question_ids = shuffled_question_ids[start:end]
    paginated_questions = [QUESTION_LOOKUP[qid] for qid in current_question_ids]

    total_pages = (len(shuffled_question_ids) + per_page - 1) // per_page

    return templates.TemplateResponse("quiz_paginated.html", {
        "request": request,
        "questions": paginated_questions,
        "current_page": page,
        "total_pages": total_pages
    })

@app.post("/quiz", response_class=HTMLResponse)
async def handle_quiz_page(request: Request, page: int = Form(...)):
    form = await request.form()
    answers = request.session.get("answers", {})

    shuffled_question_ids = request.session.get('shuffled_question_ids', QUESTION_IDS.copy())
    per_page = 7
    start = (page - 1) * per_page
    current_question_ids = shuffled_question_ids[start:start+per_page]

    for qid in current_question_ids:
        answer_key = f"question_{qid}"
        if answer_key in form:
            answers[str(qid)] = form[answer_key]  # explicitly store as string keys
    request.session["answers"] = answers

    total_pages = (len(shuffled_question_ids) + per_page - 1) // per_page
    if page < total_pages:
        return RedirectResponse(f"/quiz?page={page+1}", status_code=302)

    return RedirectResponse("/result", status_code=302)

@app.get("/lang/{variant}")
def set_language(variant: str, request: Request):
    """
    zh-CN (or cn/simp/sc/simple) => set cookie so middleware SKIPS conversion (stay Simplified)
    anything else (zh-TW, tw, etc.) => delete cookie so middleware converts to Traditional
    """
    referer = request.headers.get("referer") or "/"
    # extract path only
    path = urlparse(referer).path or "/"
    # avoid POST-only or unsafe paths
    if path in {"/start"}:
        path = "/"
    resp = RedirectResponse(path, status_code=303)

    v = (variant or "").lower()
    if v in {"zh-cn", "cn", "simp", "sc", "simple"}:
        resp.set_cookie("lang", "zh-CN", max_age=60*60*24*365, path="/",
                        samesite="lax", secure=True, httponly=False)
    else:
        resp.delete_cookie("lang", path="/")
    return resp

@app.get("/result", response_class=HTMLResponse)
async def result(request: Request):
    form_data = request.session.get("answers", {})
    shuffled_question_ids = request.session.get('shuffled_question_ids', QUESTION_IDS.copy())
            # submission count
    async with aiosqlite.connect("data.db") as db:
        async with db.execute("SELECT COUNT(*) FROM submissions") as cursor:
            row = await cursor.fetchone()
            total = row[0]

    percentages = calculation.compute_percentages_from_session(form_data)

    results = {
        metric: {
            "label": metrics.METRICS_ZH.get(metric, metric),
            "description": metrics.DES_ZH.get(metric, ""),
            "percent": percentages[metric]
        }
        for metric in percentages
    }
    timestamp = datetime.utcnow().isoformat()

    columns = ', '.join(percentages.keys())
    placeholders = ', '.join(['?'] * len(percentages))
    values = list(percentages.values())

    async with aiosqlite.connect("data.db") as db:
        await db.execute(f"""
            INSERT INTO submissions (timestamp, {columns})
            VALUES (?, {placeholders})
        """, [timestamp] + values)
        await db.commit()
    # Prepare mandarin labels
    percentages_zh = {
        metrics.METRICS_ZH.get(metric, metric): percent
        for metric, percent in percentages.items()
    }
    descriptions = {
        metrics.DES_ZH.get(metric, metric)
        for metric in percentages.keys()
    }

    sorted_results = dict(
        sorted(
            results.items(),
            key=lambda item: item[1]['percent'],
            reverse=True
        )
    )

    return templates.TemplateResponse("result.html", {
        "request": request,
        "results": sorted_results,
        "total_submissions": total
    })

@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/donate", response_class=HTMLResponse)
async def donation(request: Request):
    return templates.TemplateResponse("donate.html", {"request": request})

@app.get("/start")
async def start_get():
    return RedirectResponse("/", status_code=303)

@app.post("/start", response_class=HTMLResponse)
async def start_quiz(
    request: Request,
    captcha: str = Form(...),
    captcha_token: str = Form(...)
):
    try:
        text = read_captcha_token(captcha_token)
    except SignatureExpired:
        request.session["start_error"] = "验证码已过期，请重试。"
        return RedirectResponse("/", status_code=303)
    except BadSignature:
        request.session["start_error"] = "验证码无效，请重试。"
        return RedirectResponse("/", status_code=303)

    if captcha.strip().upper() != text.upper():
        request.session["start_error"] = "验证码错误，请重试。"
        return RedirectResponse("/", status_code=303)

    # Success
    request.session["answers"] = {}
    return RedirectResponse("/quiz?page=1", status_code=302)

@app.post("/e")
async def track_event(request: Request):
    payload = await request.json()
    ev = payload.get("event")
    if not ev:  # require an event name
        return JSONResponse({"ok": False}, status_code=400)

    # session id cookie (not PII)
    sid = request.cookies.get("sid") or str(uuid.uuid4())
    cid = payload.get("client_id")  # from localStorage

    # basic context
    ts = datetime.utcnow().isoformat()
    ip = request.client.host
    ua = request.headers.get("user-agent","")[:300]
    url = payload.get("url","")
    ref = payload.get("referrer","")
    props = json.dumps(payload.get("props", {}), ensure_ascii=False)

    async with aiosqlite.connect("data.db") as db:
        await db.execute(
            "INSERT INTO events (ts,session_id,client_id,ip,ua,event,url,referrer,props) VALUES (?,?,?,?,?,?,?,?,?)",
            [ts,sid,cid,ip,ua,ev,url,ref,props]
        )
        await db.commit()

    resp = JSONResponse({"ok": True})
    if "sid" not in request.cookies:
        resp.set_cookie("sid", sid, max_age=60*60*24*365, samesite="Lax", path="/", secure=COOKIE_SECURE)
    return resp


@app.api_route("/captcha.png", methods=["HEAD"])
async def captcha_head():
    # some clients hit HEAD; just say OK with no cache headers
    return Response(status_code=200, headers=CAPTCHA_NO_CACHE)

@app.api_route("/captcha.png", methods=["GET"])
async def captcha_png(token: str = Query(...)):
    # Decode the text from the signed token (no session writes)
    try:
        text = read_captcha_token(token)
    except SignatureExpired:
        return Response(status_code=410, headers=CAPTCHA_NO_CACHE)  # gone/expired
    except BadSignature:
        return Response(status_code=400, headers=CAPTCHA_NO_CACHE)  # invalid

    W, H = CAPTCHA_W, CAPTCHA_H
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    for _ in range(10):
        x1, y1 = random.randint(0, W), random.randint(0, H)
        x2, y2 = random.randint(0, W), random.randint(0, H)
        draw.line([(x1, y1), (x2, y2)], fill=(200, 200, 200), width=1)

    font = _load_font(size=36)
    slot = W // (len(text) + 1)
    for i, ch in enumerate(text, start=1):
        bbox = draw.textbbox((0, 0), ch, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        cx = i * slot - tw // 2 + random.randint(-3, 3)
        cy = (H - th) // 2 + random.randint(-3, 3)
        draw.text((cx, cy), ch, font=font, fill=(40, 40, 40))

    img = img.filter(ImageFilter.GaussianBlur(radius=0.4))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png", headers=CAPTCHA_NO_CACHE)

@app.get("/banner.png")
async def banner_png(request: Request):
    # 1) Compute top 5 metrics from session
    answers = request.session.get("answers", {}) or {}
    percentages = calculation.compute_percentages_from_session(answers)
    top5 = calculation.top_n_metrics(percentages, n=5)
    if not top5:
        # no answers → send a tiny 1x1 image to avoid 500
        tiny = Image.new("RGB", (1,1), "white")
        buf = io.BytesIO(); tiny.save(buf, format="PNG"); buf.seek(0)
        return StreamingResponse(buf, media_type="image/png", headers=CAPTCHA_NO_CACHE)

    # 2) Load background
    try:
        base = Image.open(BANNER_BG).convert("RGBA")
    except Exception:
        # fallback: plain gradient white
        base = Image.new("RGBA", (BANNER_W, BANNER_H), (255,255,255,255))

    # 3) Overlay badges
    for idx, (metric_key, _pct) in enumerate(top5):
        if idx >= len(BADGE_POS):
            break
        badge_name = METRIC_BADGES.get(metric_key)
        if not badge_name:
            continue
        badge_path = BADGE_DIR / badge_name
        if not badge_path.exists():
            continue
        try:
            badge = Image.open(badge_path).convert("RGBA")
            if BADGE_SIZE:
                badge = badge.resize(BADGE_SIZE, Image.LANCZOS)
            x, y = BADGE_POS[idx]
            # Bounds check (optional)
            x = max(0, min(x, BANNER_W - badge.width))
            y = max(0, min(y, BANNER_H - badge.height))
            base.alpha_composite(badge, (x, y))
        except Exception:
            continue

    # 4) Return as downloadable PNG
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG", optimize=True)
    buf.seek(0)
    headers = {
        **CAPTCHA_NO_CACHE,
        "Content-Disposition": 'attachment; filename="the_potion_banner.png"',
    }
    return StreamingResponse(buf, media_type="image/png", headers=headers)