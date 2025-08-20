from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from questions import QUESTION_LOOKUP
import aiosqlite
from datetime import datetime
from starlette.middleware.sessions import SessionMiddleware
import re
import json
from fastapi.staticfiles import StaticFiles
import random
import os
import metrics
from starlette.middleware.gzip import GZipMiddleware

# ---- NEW: imports for the middleware ----
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from typing import Any
from opencc import OpenCC
from pathlib import Path
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


# ----------------------------------------

with open('questions.json') as f:
    METRIC_CONFIG = json.load(f)

app = FastAPI()
if os.getenv("ENV") == "dev":
    app.mount("/static", StaticFiles(directory="static"), name="static")
    
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY"))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
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

class OpenCCMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _should_skip(request):
            return await call_next(request)

        response = await call_next(request)
        media_type = (response.media_type or "").lower()

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
templates.env.auto_reload = False  # Enable auto reload
templates.env.cache = {}
# Optional: Jinja2 filter to convert specific fragments in templates
templates.env.filters["s2t"] = _convert_str

QUESTIONS = [QUESTION_LOOKUP[i] for i in range(1, len(QUESTION_LOOKUP)+1)]
QUESTION_IDS = list(range(1, len(QUESTIONS) + 1))

@app.post("/start", response_class=HTMLResponse)
async def start_quiz(request: Request, email: str = Form(...)):
    # Basic email format validation
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return templates.TemplateResponse("email.html", {"request": request, "error": "請輸入有效的電子信箱。"})

    request.session["email"] = email
    request.session["answers"] = {}
    return RedirectResponse("/quiz?page=1", status_code=302)

@app.get("/", response_class=HTMLResponse)
async def email_form(request: Request):
    return templates.TemplateResponse("email.html", {"request": request})

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
    resp = RedirectResponse(referer, status_code=303)
    v = (variant or "").lower()

    if v in {"zh-cn", "cn", "simp", "sc", "simple"}:
        resp.set_cookie(
            "lang", "zh-CN",
            max_age=60*60*24*365,  # 1 year
            path="/",
            samesite="lax",
            secure=False,  # set True if you’re on HTTPS only
            httponly=False
        )
    else:
        # Traditional: remove cookie so OpenCC runs
        resp.delete_cookie("lang", path="/")
    return resp

@app.get("/result", response_class=HTMLResponse)
async def result(request: Request):
    form_data = request.session.get("answers", {})
    shuffled_question_ids = request.session.get('shuffled_question_ids', QUESTION_IDS.copy())
    metric_scores = {metric: 0 for metric in METRIC_CONFIG}
    metric_counts = {metric: 0 for metric in METRIC_CONFIG}

    # Iterate over metrics/questions and lookup answers using stored shuffled IDs
    for metric, questions in METRIC_CONFIG.items():
        for q in questions:
            qid = str(q["question"])  # Original question ID as str
            direction = q["direction"]

            # Get answer from session answers using original question ID (stored as shuffled IDs)
            answer = form_data.get(qid)
            if answer is None:
                continue

            score = (int(answer) - 1) / 6  # normalize 0-1
            if direction == "negative":
                score = 1 - score

            metric_scores[metric] += score
            metric_counts[metric] += 1

    percentages = {
        metric: round((metric_scores[metric] / metric_counts[metric]) * 100, 2)
        if metric_counts[metric] else 0
        for metric in metric_scores
    }

    results = {
        metric: {
            "label": metrics.METRICS_ZH.get(metric, metric),
            "description": metrics.DES_ZH.get(metric, ""),
            "percent": percentages[metric]
        }
        for metric in percentages
    }
    email = request.session.get("email", "unknown@example.com")
    timestamp = datetime.utcnow().isoformat()

    columns = ', '.join(percentages.keys())
    placeholders = ', '.join(['?'] * len(percentages))
    values = list(percentages.values())

    async with aiosqlite.connect("data.db") as db:
        await db.execute(f"""
            INSERT INTO submissions (timestamp, email, {columns})
            VALUES (?, ?, {placeholders})
        """, [timestamp, email] + values)
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
        "results": sorted_results
    })

@app.get("/admin/count")
async def count_submissions():
    async with aiosqlite.connect("data.db") as db:
        async with db.execute("SELECT COUNT(*) FROM submissions") as cursor:
            row = await cursor.fetchone()
            return {"total_submissions": row[0]}

@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/donate", response_class=HTMLResponse)
async def donation(request: Request):
    return templates.TemplateResponse("donate.html", {"request": request})
