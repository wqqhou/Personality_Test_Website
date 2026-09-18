# Questionnaire Scoring Web Application

A FastAPI web application for administering a 100-item multidimensional questionnaire, computing normalized profile scores, and presenting shareable results.

## What it does

- Randomizes question order, paginates the questionnaire, and stores responses in user sessions.
- Maps answers from a seven-point response scale into 25 configured metrics, including reverse-direction scoring and 0-100 normalization.
- Stores aggregate submissions and event analytics asynchronously in SQLite.
- Generates ranked result pages and dynamic shareable images with metric badges.
- Implements signed CAPTCHA tokens, session middleware, gzip compression, and basic event tracking.
- Supports Simplified and Traditional Chinese output through an OpenCC conversion middleware.

## Technical focus

**Python, FastAPI, Jinja2, aiosqlite, Pillow, OpenCC, HTML/CSS, session-based state, asynchronous web development**

The project demonstrates a complete survey workflow from data collection and scoring to persistence, localization, analytics, and result presentation.
