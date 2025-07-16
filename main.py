from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from questions import QUESTION_LOOKUP
import aiosqlite
from datetime import datetime
from starlette.middleware.sessions import SessionMiddleware
import re

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecretkey")
templates = Jinja2Templates(directory="templates")
templates.env.auto_reload = True  # Enable auto reload
templates.env.cache = {}
QUESTIONS = [QUESTION_LOOKUP[i] for i in range(1, 46)]

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

@app.route("/result", methods=["GET", "POST"])
async def result(request: Request):
    form_data = request.session.get("answers", {})
    result = {"Dom": 0, "Sub": 0, "Sadist": 0, "Masochist": 0,
              "DomQC": 0, "SubQC": 0, "SadistQC": 0, "MasochistQC": 0}

    for q in QUESTIONS:
        raw_score = form_data.get(f"question_{q['id']}")
        if raw_score is None:
            continue
        score = int(raw_score) - 1
        cat = q['cat']
        if cat == 1:
            result["Dom"] += score
            result["DomQC"] += 1
        elif cat == 2:
            result["Sub"] += score
            result["SubQC"] += 1
        elif cat == 3:
            result["Sadist"] += score
            result["SadistQC"] += 1
        elif cat == 4:
            result["Masochist"] += score
            result["MasochistQC"] += 1

    domresult = result["Dom"] / (result["DomQC"]*6) if result["DomQC"] else 0
    subresult = result["Sub"] / (result["SubQC"]*6) if result["SubQC"] else 0
    sresult = result["Sadist"] / (result["SadistQC"]*6) if result["SadistQC"] else 0
    mresult = result["Masochist"] / (result["MasochistQC"]*6) if result["MasochistQC"] else 0

    percentages = {
        "Dom": round(domresult * 100, 2),
        "Sub": round(subresult * 100, 2),
        "Sadist": round(sresult * 100, 2),
        "Masochist": round(mresult * 100, 2),
    }
    email = request.session.get("email", "unknown@example.com")
    # Save to SQLite
    async with aiosqlite.connect("data.db") as db:
        await db.execute("""
            INSERT INTO submissions (timestamp, email, dom, sub, sadist, masochist)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
        datetime.utcnow().isoformat(),
        email,
        percentages["Dom"],
        percentages["Sub"],
        percentages["Sadist"],
        percentages["Masochist"]
    ))
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