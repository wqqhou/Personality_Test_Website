from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
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

with open('questions.json') as f:
    METRIC_CONFIG = json.load(f)

app = FastAPI()
if os.getenv("ENV") == "dev":
    app.mount("/static", StaticFiles(directory="static"), name="static")
#app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY"))
app.add_middleware(GZipMiddleware, minimum_size=500)
templates = Jinja2Templates(directory="templates")
templates.env.auto_reload = False  # Enable auto reload
templates.env.cache = {}
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