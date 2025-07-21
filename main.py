from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from questions import QUESTION_LOOKUP
import aiosqlite
from datetime import datetime
from starlette.middleware.sessions import SessionMiddleware
import re
import json

with open('questions.json') as f:
    METRIC_CONFIG = json.load(f)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecretkey")
templates = Jinja2Templates(directory="templates")
templates.env.auto_reload = True  # Enable auto reload
templates.env.cache = {}
QUESTIONS = [QUESTION_LOOKUP[i] for i in range(1, 89)]

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
    start = (page - 1) * per_page
    end = start + per_page
    paginated_questions = QUESTIONS[start:end]
    total_pages = (len(QUESTIONS) + per_page - 1) // per_page

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

    for key in form:
        if key.startswith("question_"):
            answers[key] = form[key]
    request.session["answers"] = answers

    total_pages = (len(QUESTIONS) + 6) // 7
    if page < total_pages:
        return RedirectResponse(f"/quiz?page={page+1}", status_code=302)
    return RedirectResponse("/result", status_code=302)

@app.get("/result", response_class=HTMLResponse)
async def result(request: Request):
    form_data = request.session.get("answers", {})
    metric_scores = {metric: 0 for metric in METRIC_CONFIG}
    metric_counts = {metric: 0 for metric in METRIC_CONFIG}

    for metric, questions in METRIC_CONFIG.items():
        for q in questions:
            qid = str(q["question"])
            direction = q["direction"]
            answer = form_data.get(f"question_{qid}")
            if answer is None:
                continue
            score = (int(answer) - 1) / 6  # normalize 0-1
            if direction == "negative":
                score = 1 - score
            metric_scores[metric] += score
            metric_counts[metric] += 1

    percentages = {metric: round((metric_scores[metric] / metric_counts[metric]) * 100, 2) 
                   if metric_counts[metric] else 0
                   for metric in metric_scores}

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

    return templates.TemplateResponse("result.html", {
        "request": request,
        "percentages": percentages
    })


@app.get("/admin/count")
async def count_submissions():
    async with aiosqlite.connect("data.db") as db:
        async with db.execute("SELECT COUNT(*) FROM submissions") as cursor:
            row = await cursor.fetchone()
            return {"total_submissions": row[0]}