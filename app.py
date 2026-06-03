
from flask import Flask, render_template_string, request, jsonify, redirect, url_for, session
from openai import OpenAI
import os
import psycopg2
import base64
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "ntechai-dev-secret")

# ----------------------------
# OpenAI client configuration
# ----------------------------
def build_openai_client():
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("api_key")
    organization = os.environ.get("OPENAI_ORG_ID") or os.environ.get("OPENAI_ORGANIZATION")
    project = os.environ.get("OPENAI_PROJECT_ID")
    if project and project.startswith("sk-"):
        project = None

    kwargs = {"api_key": api_key}
    if organization:
        kwargs["organization"] = organization
    if project:
        kwargs["project"] = project
    return OpenAI(**kwargs)


client = build_openai_client()

# ----------------------------
# Pricing configuration
# ----------------------------
MODEL_PRICING = {
    # --- Ultra-Low Cost / Massive Scale Automation ---
    "gpt-4.1-nano": {"input": 0.00010, "output": 0.00040},  # Accurate
    "gpt-5-nano": {"input": 0.00005, "output": 0.00040},    # Accurate
    "gpt-5.4-nano": {"input": 0.00010, "output": 0.000625}, # Accurate (Standard)

    # --- Lightweight Apps & Chatbot Defaults ---
    "gpt-4o-mini": {"input": 0.00015, "output": 0.00060},    # Accurate
    "gpt-4.1-mini": {"input": 0.00040, "output": 0.00160},   # Accurate
    "gpt-5-mini": {"input": 0.00025, "output": 0.00200},     # Accurate
    "gpt-5.4-mini": {"input": 0.00075, "output": 0.00450},  # Accurate

    # --- Codex & Engineering Tiers ---
    "gpt-5.1-codex-mini": {"input": 0.00025, "output": 0.00200},  # Accurate
    "gpt-5.3-codex-global": {"input": 0.00175, "output": 0.01400}, # Accurate

    # --- Flagship Production Workhorses ---
    "gpt-4o": {"input": 0.00250, "output": 0.01000},         # Accurate
    "gpt-5.4": {"input": 0.00250, "output": 0.01500},        # Accurate
    "gpt-5.5": {"input": 0.00500, "output": 0.03000},        # Accurate

    # --- Elite Reasoning & STEM Models ---
    "o4-mini": {"input": 0.00110, "output": 0.00440},        # FIXED: Updated to Standard API rate
}



IMAGE_PRICING = {
    "low": 0.009,
    "high": 0.035,
}

IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4.1-mini")
ADMIN_IDN = os.environ.get("ADMIN_IDN", "nathanodom11032013151507072014198319816789")

raw_ids = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS = [i.strip() for i in raw_ids.split(",") if i.strip()]

USER_MAP = {
    "nathanodom11032013151507072014198319816789": "Admin (Nathan)",
    "1865": "Michael", "002": "User 002", "003": "User 003",
    "1793": "Quinn", "005": "User 005", "006": "User 006", "010": "User 010",
    "9823": "Market day 1", "4265": "Market day 2", "5892": "Market day 3",
    "1285": "Market day 4", "6723": "Market day 5", "7531": "Market day 6",
    "1596": "Market day 7", "4652": "Market day 8", "9187": "Market day 9",
}


def normalize_id_code(id_code):
    return (id_code or "").strip()


def get_allowed_ids():
    ids = set(ALLOWED_IDS)
    ids.update(USER_MAP.keys())
    return sorted(ids)


def parse_credit_limit(raw_value):
    cleaned = (raw_value or "").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except (TypeError, ValueError):
        return "invalid"
    return value if value >= 0 else "invalid"


DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"


def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(DATABASE_URL)


def user_exists(id_code):
    normalized = normalize_id_code(id_code)
    if not normalized:
        return False
    if not DATABASE_URL:
        return normalized in get_allowed_ids()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE id_code = %s LIMIT 1;", (normalized,))
        found = cursor.fetchone() is not None
        cursor.close()
        conn.close()
        return found
    except Exception:
        return normalized in get_allowed_ids()


def is_admin_session():
    return normalize_id_code(session.get("idn")) == ADMIN_IDN


def init_db():
    if not DATABASE_URL:
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id_code VARCHAR(50) PRIMARY KEY,
                total_spent FLOAT DEFAULT 0.0,
                display_name VARCHAR(255),
                credit_limit FLOAT
            );
        """)
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS credit_limit FLOAT;")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interaction_logs (
                id SERIAL PRIMARY KEY,
                id_code VARCHAR(50) NOT NULL,
                log_type VARCHAR(32) NOT NULL,
                prompt TEXT,
                response TEXT,
                model TEXT,
                cost FLOAT DEFAULT 0.0,
                media_name TEXT,
                media_mime TEXT,
                media_b64 TEXT,
                analysis TEXT,
                quality TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interaction_logs_created_at ON interaction_logs (created_at DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interaction_logs_id_code ON interaction_logs (id_code);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_interaction_logs_type ON interaction_logs (log_type);")

        for id_code in get_allowed_ids():
            cursor.execute(
                """
                INSERT INTO users (id_code, total_spent, display_name)
                VALUES (%s, 0.0, %s)
                ON CONFLICT (id_code) DO UPDATE SET display_name = EXCLUDED.display_name;
                """,
                (id_code, USER_MAP.get(id_code, id_code))
            )

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"DB Error: {e}")


init_db()

# ----------------------------
# DB helpers
# ----------------------------
def get_user_account(id_code):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT total_spent, credit_limit, display_name FROM users WHERE id_code = %s", (id_code,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def update_user_spent(id_code, additional_cost):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET total_spent = total_spent + %s WHERE id_code = %s", (additional_cost, id_code))
    conn.commit()
    cursor.execute("SELECT total_spent, credit_limit FROM users WHERE id_code = %s", (id_code,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def insert_log(
    id_code,
    log_type,
    prompt="",
    response="",
    model="",
    cost=0.0,
    media_name=None,
    media_mime=None,
    media_b64=None,
    analysis=None,
    quality=None,
):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO interaction_logs
            (id_code, log_type, prompt, response, model, cost, media_name, media_mime, media_b64, analysis, quality)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (id_code, log_type, prompt, response, model, cost, media_name, media_mime, media_b64, analysis, quality),
    )
    new_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    return new_id


def delete_log(log_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM interaction_logs WHERE id = %s", (log_id,))
    conn.commit()
    cursor.close()
    conn.close()


def fetch_logs(limit=120):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, id_code, log_type, prompt, response, model, cost, media_name, media_mime, media_b64, analysis, quality, created_at
        FROM interaction_logs
        ORDER BY created_at DESC
        LIMIT %s;
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def approx_tokens_from_text(text):
    return max(1, int(len(text or "") / 4))


def cost_from_usage(model_name, prompt_tokens=0, completion_tokens=0):
    pricing = MODEL_PRICING.get(model_name)
    if not pricing:
        return 0.0
    return ((prompt_tokens / 1000) * pricing["input"]) + ((completion_tokens / 1000) * pricing["output"])


def analyze_image_with_vision(data_url, prompt="Describe this image in detail.", model=None):
    vision_model = model or VISION_MODEL
    res = client.chat.completions.create(
        model=vision_model,
        messages=[
            {
                "role": "system",
                "content": "You are a precise image analysis assistant. Describe the image clearly and briefly, then mention notable objects, text, actions, and safety issues if present.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    )
    text = res.choices[0].message.content or ""
    usage = getattr(res, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    cost = cost_from_usage(vision_model, prompt_tokens, completion_tokens)
    return text, cost, vision_model


def is_allowed_image_mime(mime_type):
    return mime_type in {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}


# ----------------------------
# HTML templates
# ----------------------------
BASE_STYLE = """
:root {
    color-scheme: light;
    --bg: #eef2ff;
    --bg-2: #f8fafc;
    --card: rgba(255,255,255,.82);
    --card-solid: #ffffff;
    --panel: #ffffff;
    --panel-2: #f8fafc;
    --border: #d7e0ee;
    --border-strong: #c5d1e3;
    --primary: #2563eb;
    --primary-2: #1d4ed8;
    --primary-soft: rgba(37, 99, 235, .12);
    --muted: #64748b;
    --text: #0f172a;
    --subtle-text: #475569;
    --shadow: 0 18px 45px rgba(15, 23, 42, .10);
    --shadow-soft: 0 10px 26px rgba(15, 23, 42, .08);
    --user: #e9f2ff;
    --assistant: #f8fafc;
    --user-border: #cfe0ff;
    --assistant-border: #e2e8f0;
    --radius-xl: 24px;
    --radius-lg: 18px;
    --radius-md: 14px;
    --radius-sm: 10px;
    --app-pad: 18px;
    --chat-pad: 16px;
    --control-gap: 12px;
    --panel-width: 360px;
    --composer-pad: 14px;
    --base-font: 16px;
    --line-height: 1.5;
}

html[data-theme="light"] {
    color-scheme: light;
}

html[data-theme="dark"] {
    color-scheme: dark;
    --bg: #0b1220;
    --bg-2: #111827;
    --card: rgba(15, 23, 42, .82);
    --card-solid: #0f172a;
    --panel: #0f172a;
    --panel-2: #111827;
    --border: #223049;
    --border-strong: #31415f;
    --primary: #60a5fa;
    --primary-2: #3b82f6;
    --primary-soft: rgba(96, 165, 250, .14);
    --muted: #94a3b8;
    --text: #e5e7eb;
    --subtle-text: #cbd5e1;
    --shadow: 0 20px 50px rgba(0, 0, 0, .35);
    --shadow-soft: 0 12px 28px rgba(0, 0, 0, .24);
    --user: rgba(96, 165, 250, .16);
    --assistant: rgba(148, 163, 184, .12);
    --user-border: rgba(96, 165, 250, .28);
    --assistant-border: rgba(148, 163, 184, .18);
}

html[data-theme="midnight"] {
    color-scheme: dark;
    --bg: #090b16;
    --bg-2: #121528;
    --card: rgba(11, 15, 27, .86);
    --card-solid: #0b0f1b;
    --panel: #0b0f1b;
    --panel-2: #11172a;
    --border: #20263b;
    --border-strong: #2d3550;
    --primary: #a78bfa;
    --primary-2: #8b5cf6;
    --primary-soft: rgba(167, 139, 250, .16);
    --muted: #9aa3b2;
    --text: #eef2ff;
    --subtle-text: #c9d2e3;
    --shadow: 0 24px 55px rgba(0, 0, 0, .45);
    --shadow-soft: 0 14px 32px rgba(0, 0, 0, .28);
    --user: rgba(167, 139, 250, .16);
    --assistant: rgba(148, 163, 184, .10);
    --user-border: rgba(167, 139, 250, .24);
    --assistant-border: rgba(148, 163, 184, .18);
}

html[data-theme="emerald"] {
    color-scheme: light;
    --primary: #10b981;
    --primary-2: #059669;
    --primary-soft: rgba(16, 185, 129, .13);
}

html[data-theme="rose"] {
    color-scheme: light;
    --primary: #f43f5e;
    --primary-2: #e11d48;
    --primary-soft: rgba(244, 63, 94, .13);
}

html[data-theme="slate"] {
    color-scheme: dark;
    --bg: #0f172a;
    --bg-2: #111827;
    --card: rgba(15, 23, 42, .84);
    --card-solid: #0f172a;
    --panel: #0f172a;
    --panel-2: #111827;
    --border: #243246;
    --border-strong: #334155;
    --primary: #38bdf8;
    --primary-2: #0ea5e9;
    --primary-soft: rgba(56, 189, 248, .14);
    --muted: #94a3b8;
    --text: #e5e7eb;
    --subtle-text: #cbd5e1;
    --shadow: 0 20px 50px rgba(0, 0, 0, .28);
    --shadow-soft: 0 12px 28px rgba(0, 0, 0, .22);
    --user: rgba(56, 189, 248, .13);
    --assistant: rgba(148, 163, 184, .10);
    --user-border: rgba(56, 189, 248, .24);
    --assistant-border: rgba(148, 163, 184, .18);
}

html[data-density="compact"] {
    --app-pad: 12px;
    --chat-pad: 12px;
    --control-gap: 8px;
    --panel-width: 332px;
    --composer-pad: 10px;
    --base-font: 15px;
}

html[data-density="comfortable"] {
    --app-pad: 18px;
    --chat-pad: 16px;
    --control-gap: 12px;
    --panel-width: 360px;
    --composer-pad: 14px;
    --base-font: 16px;
}

html[data-density="spacious"] {
    --app-pad: 22px;
    --chat-pad: 18px;
    --control-gap: 14px;
    --panel-width: 392px;
    --composer-pad: 16px;
    --base-font: 17px;
}

html[data-radius="soft"] {
    --radius-xl: 24px;
    --radius-lg: 18px;
    --radius-md: 14px;
    --radius-sm: 10px;
}

html[data-radius="rounded"] {
    --radius-xl: 30px;
    --radius-lg: 22px;
    --radius-md: 16px;
    --radius-sm: 12px;
}

html[data-radius="square"] {
    --radius-xl: 14px;
    --radius-lg: 12px;
    --radius-md: 10px;
    --radius-sm: 8px;
}

html[data-text="small"] { --base-font: 15px; }
html[data-text="medium"] { --base-font: 16px; }
html[data-text="large"] { --base-font: 17px; }

html[data-motion="reduced"] *,
html[data-motion="reduced"] *::before,
html[data-motion="reduced"] *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
    scroll-behavior: auto !important;
}

* { box-sizing: border-box; }

html, body {
    margin: 0;
    min-height: 100%;
}

body {
    min-height: 100vh;
    overflow-x: hidden;
    overflow-y: auto;

    font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: var(--base-font);
    line-height: var(--line-height);
    color: var(--text);
    background:
        radial-gradient(1100px 650px at 12% -8%, rgba(37, 99, 235, .18) 0%, transparent 45%),
        radial-gradient(900px 600px at 100% 0%, rgba(99, 102, 241, .10) 0%, transparent 40%),
        linear-gradient(135deg, var(--bg), var(--bg-2));
}

body::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    background-image:
        linear-gradient(rgba(255,255,255,.02) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.02) 1px, transparent 1px);
    background-size: 24px 24px;
    opacity: .24;
    mask-image: radial-gradient(circle at center, black 25%, transparent 100%);
}

a {
    color: var(--primary);
    text-decoration: none;
}

a:hover { text-decoration: underline; }

small { color: var(--muted); }

.glass {
    background: var(--card);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid var(--border);
    border-radius: var(--radius-xl);
    box-shadow: var(--shadow);
}

.btn {
    padding: 11px 15px;
    border: 0;
    border-radius: 12px;
    cursor: pointer;
    font-weight: 700;
    letter-spacing: .01em;
    background: linear-gradient(180deg, var(--primary), var(--primary-2));
    color: white;
    box-shadow: 0 8px 18px var(--primary-soft);
    transition: transform .15s ease, box-shadow .15s ease, filter .15s ease;
}

.btn:hover { transform: translateY(-1px); filter: brightness(1.03); }
.btn:active { transform: translateY(0); filter: brightness(.98); }

.btn.secondary {
    background: var(--panel-2);
    color: var(--text);
    border: 1px solid var(--border);
    box-shadow: none;
}

.btn.danger {
    background: linear-gradient(180deg, #ef4444, #dc2626);
}

.btn.ghost {
    background: transparent;
    color: var(--text);
    border: 1px solid var(--border);
    box-shadow: none;
}

input, textarea, select {
    width: 100%;
    padding: 11px 12px;
    border-radius: 12px;
    border: 1px solid var(--border);
    background: var(--card-solid);
    color: var(--text);
    outline: none;
    transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}

input:focus, textarea:focus, select:focus {
    border-color: var(--primary);
    box-shadow: 0 0 0 4px var(--primary-soft);
}

label {
    font-size: .92rem;
    color: var(--subtle-text);
    font-weight: 600;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th, td {
    border-bottom: 1px solid var(--border);
    padding: 10px;
    vertical-align: top;
    text-align: left;
}

th {
    background: var(--panel-2);
    color: var(--text);
}

.scrollbar::-webkit-scrollbar,
.chat::-webkit-scrollbar,
.panel::-webkit-scrollbar {
    width: 12px;
}

.scrollbar::-webkit-scrollbar-thumb,
.chat::-webkit-scrollbar-thumb,
.panel::-webkit-scrollbar-thumb {
    background: rgba(100, 116, 139, .35);
    border-radius: 999px;
    border: 3px solid transparent;
    background-clip: padding-box;
}

.scrollbar::-webkit-scrollbar-track,
.chat::-webkit-scrollbar-track,
.panel::-webkit-scrollbar-track {
    background: transparent;
}

.pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    background: var(--primary-soft);
    color: var(--primary);
    border: 1px solid rgba(127, 141, 170, .16);
}

.kbd {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 22px;
    padding: 1px 7px;
    border: 1px solid var(--border);
    border-bottom-color: var(--border-strong);
    border-radius: 8px;
    background: var(--panel-2);
    color: var(--subtle-text);
    font-size: 12px;
    font-weight: 700;
    box-shadow: 0 1px 0 rgba(255,255,255,.25) inset;
}
"""

CHAT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" data-theme="system" data-density="comfortable" data-radius="soft" data-text="medium" data-motion="on">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>N Tech AI 2.4</title>
    <style>
        """ + BASE_STYLE + """
        body {
            min-height: 100vh;
            overflow-x: hidden;
            overflow-y: auto;
        }

        .app {
            min-height: 100vh;
            display: grid;
            grid-template-columns: 1fr;
            gap: 0;
            padding: var(--app-pad);
        }

        .card {
            height: 100%;
            display: flex;
            flex-direction: column;
            gap: var(--control-gap);
            padding: var(--app-pad);
            overflow: hidden;
        }

        .topbar {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 16px;
            padding-bottom: 8px;
        }

        .topbar h2 {
            margin: 0;
            font-size: clamp(1.3rem, 2vw, 1.7rem);
            letter-spacing: -.02em;
        }

        .subtitle {
            margin-top: 6px;
            color: var(--muted);
            max-width: 860px;
            font-size: .95rem;
        }

        .chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }

        .chat-shell {
            display: grid;
            grid-template-rows: 1fr auto;
            gap: var(--control-gap);
            min-height: 0;
            flex: 1;
        }

        .chat {
            background:
                radial-gradient(700px 220px at 10% 0%, rgba(255,255,255,.55), transparent 45%),
                linear-gradient(180deg, var(--panel), var(--panel-2));
            border: 1px solid var(--border);
            border-radius: var(--radius-xl);
            padding: var(--chat-pad);
            overflow-y: auto;
            min-height: 0;
            box-shadow: var(--shadow-soft);
        }

        .empty-state {
            display: grid;
            place-items: center;
            text-align: center;
            height: 100%;
            color: var(--muted);
            padding: 24px;
        }

        .empty-card {
            max-width: 520px;
            padding: 28px;
            border-radius: var(--radius-xl);
            border: 1px solid var(--border);
            background: rgba(255,255,255,.22);
        }

        .empty-card h3 {
            margin: 0 0 8px 0;
            color: var(--text);
            font-size: 1.15rem;
        }

        .messages {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .message {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            max-width: min(90%, 920px);
        }

        .message.user {
            align-self: flex-end;
            flex-direction: row-reverse;
        }

        .avatar {
            width: 34px;
            height: 34px;
            border-radius: 50%;
            flex: 0 0 auto;
            display: grid;
            place-items: center;
            font-size: 15px;
            font-weight: 800;
            color: white;
            background: linear-gradient(180deg, var(--primary), var(--primary-2));
            box-shadow: 0 8px 18px var(--primary-soft);
        }

        .avatar.assistant {
            background: linear-gradient(180deg, #64748b, #475569);
            box-shadow: none;
        }

        .bubble-wrap {
            display: flex;
            flex-direction: column;
            gap: 6px;
            min-width: 0;
        }

        .bubble {
            padding: 12px 14px;
            border-radius: var(--radius-lg);
            white-space: pre-wrap;
            word-break: break-word;
            border: 1px solid var(--assistant-border);
            background: var(--assistant);
            box-shadow: 0 8px 16px rgba(15, 23, 42, .05);
        }

        .message.user .bubble {
            background: var(--user);
            border-color: var(--user-border);
        }

        .meta {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 12px;
            color: var(--muted);
            padding: 0 2px;
        }

        .message.user .meta {
            justify-content: flex-end;
        }

        .attachments {
            display: grid;
            gap: 8px;
            margin-top: 8px;
        }

        .preview {
            max-width: 180px;
            max-height: 180px;
            border-radius: 14px;
            border: 1px solid var(--border);
            display: block;
            object-fit: cover;
            box-shadow: var(--shadow-soft);
        }

        .composer {
            border: 1px solid var(--border);
            background: var(--card);
            border-radius: var(--radius-xl);
            padding: var(--composer-pad);
            box-shadow: var(--shadow-soft);
        }

        .composer-grid {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 10px;
            align-items: end;
        }

        .textarea-wrap {
            display: grid;
            gap: 8px;
        }

        textarea {
            min-height: 92px;
            resize: vertical;
            line-height: 1.45;
        }

        .composer-tools {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
            margin-top: 10px;
        }

        .row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }

        .stats {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            margin-top: 4px;
        }

        .stat {
            padding: 12px;
            border-radius: 16px;
            border: 1px solid var(--border);
            background: var(--panel-2);
        }

        .stat .label {
            color: var(--muted);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .03em;
            text-transform: uppercase;
        }

        .stat .value {
            margin-top: 6px;
            font-size: 1rem;
            font-weight: 800;
            color: var(--text);
        }

        .panel {
            position: fixed;
            right: calc(-1 * var(--panel-width));
            top: 0;
            width: var(--panel-width);
            height: 100vh;
            background: var(--panel);
            border-left: 1px solid var(--border);
            padding: 16px;
            transition: right .22s ease;
            z-index: 10001;
            overflow: auto;
            box-shadow: -18px 0 34px rgba(2, 6, 23, .18);
        }

        .panel.open {
            right: 0;
        }

        .panel-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 12px;
        }

        .panel h3 {
            margin: 0;
            font-size: 1.05rem;
        }

        .panel-section {
            padding: 12px;
            border-radius: 16px;
            border: 1px solid var(--border);
            background: var(--panel-2);
            margin-top: 12px;
        }

        .panel-section h4 {
            margin: 0 0 10px 0;
            font-size: .93rem;
            color: var(--text);
        }

        .settings-grid {
            display: grid;
            gap: 10px;
        }

        .settings-grid.two {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .toggle-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 10px 0;
        }

        .toggle-row input[type="checkbox"] {
            width: auto;
            transform: scale(1.05);
        }

        .intro {
            position: fixed;
            inset: 0;
            background:
                radial-gradient(circle at center, rgba(255,255,255,.12), transparent 34%),
                #050816;
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
            animation: introFadeOut 1s ease 1.9s forwards;
        }

        .intro .logo {
            font-size: min(34vw, 300px);
            font-weight: 900;
            color: white;
            line-height: 1;
            opacity: 0;
            transform: scale(.92);
            letter-spacing: -.06em;
            animation: nReveal 1.05s ease forwards;
            text-shadow: 0 18px 60px rgba(0,0,0,.45);
        }

        @keyframes nReveal {
            from { opacity: 0; transform: scale(.92); filter: blur(8px); }
            to { opacity: 1; transform: scale(1); filter: blur(0); }
        }

        @keyframes introFadeOut {
            from { opacity: 1; visibility: visible; }
            to { opacity: 0; visibility: hidden; }
        }

        .muted { color: var(--muted); }
        .small-note { font-size: .88rem; color: var(--muted); }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            background: var(--primary);
            box-shadow: 0 0 0 4px var(--primary-soft);
        }

        .top-actions {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: flex-end;
        }

        .link-list {
            display: grid;
            gap: 8px;
        }

        .link-card {
            padding: 12px;
            border-radius: 14px;
            border: 1px solid var(--border);
            background: var(--card-solid);
        }

        .link-card:hover {
            border-color: var(--primary);
            text-decoration: none;
            box-shadow: 0 0 0 4px var(--primary-soft);
        }

        .credits {
            display: grid;
            gap: 8px;
            margin-top: 10px;
        }

        .bar {
            width: 100%;
            height: 10px;
            border-radius: 999px;
            overflow: hidden;
            background: rgba(148, 163, 184, .18);
            border: 1px solid var(--border);
        }

        .bar > div {
            height: 100%;
            width: 0%;
            border-radius: 999px;
            background: linear-gradient(90deg, var(--primary), var(--primary-2));
            transition: width .2s ease;
        }

        @media (max-width: 980px) {
            body { overflow: auto; }
            .app { padding: 10px; }
            .stats { grid-template-columns: 1fr; }
            .message { max-width: 96%; }
            .panel {
                width: min(100vw, 400px);
                right: calc(-1 * min(100vw, 400px));
            }
        }

        @media (max-width: 760px) {
            .topbar { flex-direction: column; align-items: stretch; }
            .top-actions { justify-content: flex-start; }
            .composer-grid { grid-template-columns: 1fr; }
            .message { max-width: 100%; }
            .panel { width: 100vw; right: -100vw; }
        }
    </style>
</head>
<body>
    <div class="intro" id="introSplash">
        <div class="logo">N Tech AI</div>
    </div>

    <div class="app">
        <div class="card glass">
            <div class="topbar">
                <div>
                    <h2>N Tech AI 2.5</h2>
                    <div class="subtitle" style="font-size: 5px;">
                        2.1 new features: chat history, optional memory. Note: 1.9 Smart is the same as 1.8 Ultra, but being remade. N Tech AI Art competition submissions is almost closed! Images used to cost 20 credits (almost 2 cents!) per image (On basic), which we fixed, now only costing 9 credits (On basic) and 35 (on smart). N Code is fixed! 2.2 Ultra is out, and it is probably our best model yet! try it by clicking the settings to change models. N TECH AI FOUND ILLEGAL BEHAVIOUR AT 11:39 AM 5/19/26 and 5/21/26 around noon. If you know something, please tell the N Tech Staff. N Tech AI 2026. 2.4 is out!!! New features include: better photo scanning, enhanced image generation, and more! 2.4 new update: A cleaner workspace for chat, files, and vision. Style controls live in Settings, and the interface can follow your preferred theme, density, and motion settings. 2.5 is out, and what does it bring? NEW MODELS. N Tech AI 2.3 Smart is extremly smart and cost efficient, and 2.4 Smart is N Tech AI's first "Reasoning" Model, meaning it has insane accuracy, and is extremely good to use. The 2.5 models are the best models yet, but they are extremly expensive. use them only if you have a lot of credits. By Using N Tech AI, you agree to the updated terms and conditions. 
                    </div>
                    <div class="chip-row">
                        <span class="pill"><span class="status-dot"></span> Live Chat</span>
                        <span class="pill">Memory: <span id="memoryChip">On</span></span>
                        <span class="pill">Model: <span id="modelChip">N Tech AI 1.7 Basic</span></span>
                        <span class="pill">Theme: <span id="themeChip">System</span></span>
                    </div>
                </div>
                <div class="top-actions">
                    <button class="btn ghost" onclick="toggleSettings()">Settings</button>
                    <button class="btn secondary" onclick="clearHistory()">Clear chat</button>
                </div>
            </div>

            <div class="stats">
                <div class="stat">
                    <div class="label">Session spent</div>
                    <div class="value">$<span id="totalDisplay">0.000000</span></div>
                </div>
                <div class="credits">
                    <div class="bar">
                        <div id="creditsBar"></div>
                    </div>
                </div>
                <div class="stat">
                    <div class="label">Credits used</div>
                    <div class="value"><span id="creditsUsed">0.00</span><span id="creditLimitText"></span></div>
                </div>
                <div class="stat">
                    <div class="label">Status</div>
                    <div class="value" id="status">Ready</div>
                </div>
            </div>

            <div class="chat-shell">
                <div class="chat scrollbar" id="chatHistory"></div>

                <div class="composer">
                    <div class="row">
                        <label style="display:flex;align-items:center;gap:8px;">
                            <input type="checkbox" id="memoryToggle" checked style="width:auto;">
                            Remember previous outputs for context
                        </label>
                        <div class="small-note">
                            Send: <span class="kbd">Enter</span> • New line: <span class="kbd">Shift</span> + <span class="kbd">Enter</span>
                        </div>
                    </div>

                    <div class="composer-tools">
                        <input id="fileInput" type="file" multiple />
                    </div>

                    <div class="composer-grid">
                        <div class="textarea-wrap">
                            <textarea id="userInput" placeholder="Ask anything..."></textarea>
                        </div>
                        <button class="btn" style="width:auto;" onclick="askAI()">Send to AI</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <aside class="panel scrollbar" id="settingsPanel">
        <div class="panel-head">
            <h3>Settings</h3>
            <button class="btn secondary" style="width:auto;padding:6px 10px;" onclick="toggleSettings()">Close</button>
        </div>

        <div class="small-note">Signed in as: {{ idn }}</div>

        <div class="panel-section">
            <h4>Model</h4>
            <select id="panelModel">
                <!-- Standard & Mini Tiers (Your Existing Consumer Lineup) -->
                <option value="gpt-4o-mini">N Tech 1.7 Basic</option>
                <option value="gpt-4.1-nano">N Tech 1.7 Smart</option>
                <option value="gpt-4.1-mini">N Tech 2.0 Basic (experimental)</option>
                <option value="gpt-5.4-nano">N Tech AI 1.8 Smart</option>
                <option value="gpt-5.4-mini">N Tech AI 1.9 Smart (Being remade)</option>
                <option value="gpt-4.1-nano">N Tech AI 2.0 Basic</option>
                <option value="gpt-5.4-nano">N Tech AI 2.1 Smart</option>
                <option value="gpt-5.4-nano">N Tech AI 2.2 Basic</option>
                <option value="gpt-5-mini">N Tech AI 2.2 Ultra</option>
                <option value="gpt-5-nano">N Tech AI 2.3 Smart (Super cheap)</option>
                <option value="gpt-5.4-mini">N Tech AI 2.3 Basic (Costly)</option>
                <option value="gpt-5.4-nano">N Tech AI 2.4 Basic</option>
                <option value="o4-mini">N Tech AI 2.4 Smart</option>

                <!--2.5 MODELS-->
                
                <option value="gpt-5.4">N Tech AI 2.5 Smart</option>
                <option value="gpt-5.5">N Tech AI 2.5 Ultra</option>
                <option value="gpt-5.3-codex-global">N Tech AI 2.5 Code</option>
            </select>

        </div>

        <div class="panel-section">
            <h4>Appearance</h4>
            <div class="settings-grid">
                <div>
                    <label>Theme</label>
                    <select id="themeSelect">
                        <option value="system">System</option>
                        <option value="light">Light</option>
                        <option value="dark">Dark</option>
                        <option value="midnight">Midnight</option>
                        <option value="slate">Slate</option>
                        <option value="emerald">Emerald</option>
                        <option value="rose">Rose</option>
                    </select>
                </div>

                <div>
                    <label>Accent</label>
                    <select id="accentSelect">
                        <option value="blue">Blue</option>
                        <option value="violet">Violet</option>
                        <option value="emerald">Emerald</option>
                        <option value="rose">Rose</option>
                        <option value="amber">Amber</option>
                        <option value="cyan">Cyan</option>
                    </select>
                </div>

                <div>
                    <label>Density</label>
                    <select id="densitySelect">
                        <option value="compact">Compact</option>
                        <option value="comfortable">Comfortable</option>
                        <option value="spacious">Spacious</option>
                    </select>
                </div>

                <div>
                    <label>Corner style</label>
                    <select id="radiusSelect">
                        <option value="soft">Soft</option>
                        <option value="rounded">Rounded</option>
                        <option value="square">Square</option>
                    </select>
                </div>

                <div>
                    <label>Text size</label>
                    <select id="textSelect">
                        <option value="small">Small</option>
                        <option value="medium">Medium</option>
                        <option value="large">Large</option>
                    </select>
                </div>

                <div>
                    <label>Motion</label>
                    <select id="motionSelect">
                        <option value="on">On</option>
                        <option value="reduced">Reduced</option>
                    </select>
                </div>
            </div>

            <div class="toggle-row">
                <div>
                    <div style="font-weight:700;">Show message timestamps</div>
                    <div class="small-note">Display a time label for each message bubble.</div>
                </div>
                <input type="checkbox" id="timestampToggle" checked />
            </div>
        </div>

        <div class="panel-section">
            <h4>Memory & tools</h4>
            <div class="toggle-row">
                <div>
                    <div style="font-weight:700;">Remember previous outputs</div>
                    <div class="small-note">Keeps chat history in the request context.</div>
                </div>
                <input type="checkbox" id="panelMemory" checked />
            </div>
        </div>

        <div class="panel-section">
            <h4>Shortcuts</h4>
            <div class="link-list">
                <a class="link-card" href="/image">Open Image Generator</a>
                <a class="link-card" href="/code">Open N-Code</a>
                {% if is_admin %}<a class="link-card" href="/dashboard">Open Admin Dashboard</a>{% endif %}
                <a class="link-card" href="/logout">Sign out</a>
            </div>
        </div>
    </aside>

    <script>
        let messages = [];
        const UI_KEY = "ntai-ui-v2";

        const ACCENTS = {
            blue:   { primary: "#2563eb", primary2: "#1d4ed8" },
            violet: { primary: "#7c3aed", primary2: "#6d28d9" },
            emerald:{ primary: "#10b981", primary2: "#059669" },
            rose:   { primary: "#f43f5e", primary2: "#e11d48" },
            amber:  { primary: "#f59e0b", primary2: "#d97706" },
            cyan:   { primary: "#06b6d4", primary2: "#0891b2" }
        };

        const systemThemeQuery = window.matchMedia("(prefers-color-scheme: dark)");

        function defaultPrefs() {
            return {
                theme: "system",
                accent: "blue",
                density: "comfortable",
                radius: "soft",
                text: "medium",
                motion: "on",
                timestamps: true
            };
        }

        function loadPrefs() {
            try {
                return { ...defaultPrefs(), ...(JSON.parse(localStorage.getItem(UI_KEY)) || {}) };
            } catch {
                return defaultPrefs();
            }
        }

        function savePrefs(prefs) {
            localStorage.setItem(UI_KEY, JSON.stringify(prefs));
        }

        function getCurrentThemeValue(themeChoice) {
            if (themeChoice !== "system") return themeChoice;
            return systemThemeQuery.matches ? "dark" : "light";
        }

        function applyPrefs(prefs) {
            const themeValue = getCurrentThemeValue(prefs.theme);
            const root = document.documentElement;

            root.dataset.theme = themeValue;
            root.dataset.density = prefs.density || "comfortable";
            root.dataset.radius = prefs.radius || "soft";
            root.dataset.text = prefs.text || "medium";
            root.dataset.motion = prefs.motion || "on";

            const accent = ACCENTS[prefs.accent] || ACCENTS.blue;
            root.style.setProperty("--primary", accent.primary);
            root.style.setProperty("--primary-2", accent.primary2);
            root.style.setProperty("--primary-soft", `${accent.primary}1f`);

            document.getElementById("themeChip").innerText = prefs.theme === "system"
                ? `System (${themeValue})`
                : prefs.theme.charAt(0).toUpperCase() + prefs.theme.slice(1);

            document.getElementById("memoryChip").innerText = prefs.memory ? "On" : "Off";
            const MODEL_LABELS = {
                "gpt-4o-mini": "N Tech AI 1.7 Basic",
                "gpt-4.1-nano": "N Tech AI 1.7 Smart",
                "gpt-4.1-mini": "N Tech AI 2.0 Basic (experimental)",
                "gpt-5.4-nano": "N Tech AI 2.3 Basic",
                "gpt-5.4-mini": "N Tech AI 1.9 Smart (Being remade)",
                "gpt-5-mini": "N Tech AI 2.2 Ultra"
            };

            function updateUiChips() {
                document.getElementById("memoryChip").innerText =
                    document.getElementById("memoryToggle").checked ? "On" : "Off";

                const modelSelect = document.getElementById("panelModel");
                document.getElementById("modelChip").innerText =
                    modelSelect.options[modelSelect.selectedIndex].text;

                const prefs = loadPrefs();
                document.getElementById("themeChip").innerText =
                    prefs.theme === "system"
                        ? `System (${getCurrentThemeValue("system")})`
                        : prefs.theme.charAt(0).toUpperCase() + prefs.theme.slice(1);
            }
        }

        function setSelectValue(id, value) {
            const el = document.getElementById(id);
            if (el) el.value = value;
        }

        function syncUiFromPrefs(prefs) {
            setSelectValue("themeSelect", prefs.theme);
            setSelectValue("accentSelect", prefs.accent);
            setSelectValue("densitySelect", prefs.density);
            setSelectValue("radiusSelect", prefs.radius);
            setSelectValue("textSelect", prefs.text);
            setSelectValue("motionSelect", prefs.motion);
            document.getElementById("timestampToggle").checked = !!prefs.timestamps;
            document.getElementById("memoryToggle").checked = !!prefs.memory;
            document.getElementById("panelMemory").checked = !!prefs.memory;
        }

        function readPrefsFromUi() {
            return {
                theme: document.getElementById("themeSelect").value,
                accent: document.getElementById("accentSelect").value,
                density: document.getElementById("densitySelect").value,
                radius: document.getElementById("radiusSelect").value,
                text: document.getElementById("textSelect").value,
                motion: document.getElementById("motionSelect").value,
                timestamps: document.getElementById("timestampToggle").checked,
                memory: document.getElementById("memoryToggle").checked
            };
        }

        function persistAndApply() {
            const prefs = readPrefsFromUi();
            savePrefs(prefs);
            applyPrefs(prefs);
        }

        function toggleSettings() {
            document.getElementById("settingsPanel").classList.toggle("open");
        }

        function escapeHtml(text) {
            const div = document.createElement("div");
            div.innerText = text || "";
            return div.innerHTML;
        }

        function timeLabel(value) {
            if (!value) return "";
            return value;
        }

        function renderHistory() {
            const wrap = document.getElementById("chatHistory");

            if (!messages.length) {
                wrap.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-card">
                            <h3>Start a conversation</h3>
                            <div>Ask a question, upload a file, or switch styles in Settings.</div>
                        </div>
                    </div>`;
                return;
            }

            const showTimestamps = document.getElementById("timestampToggle").checked;

            wrap.innerHTML = `
                <div class="messages">
                    ${messages.map(m => {
                        const roleIcon = m.role === "user" ? "You" : "AI";
                        const avatarClass = m.role === "user" ? "avatar" : "avatar assistant";
                        const previewHtml = Array.isArray(m.previews) && m.previews.length
                            ? `<div class="attachments">${m.previews.map(p => `
                                <div>
                                    <small>${escapeHtml(p.label || "")}</small>
                                    <img class="preview" src="${p.src}" alt="attachment preview">
                                </div>
                            `).join("")}</div>`
                            : "";
                        const metaHtml = showTimestamps
                            ? `<div class="meta"><span>${roleIcon}</span><span>•</span><span>${escapeHtml(timeLabel(m.time || ""))}</span></div>`
                            : `<div class="meta"><span>${roleIcon}</span></div>`;

                        return `
                            <div class="message ${m.role}">
                                <div class="${avatarClass}">${m.role === "user" ? "Y" : "A"}</div>
                                <div class="bubble-wrap">
                                    <div class="bubble">${escapeHtml(m.content)}${previewHtml}</div>
                                    ${metaHtml}
                                </div>
                            </div>
                        `;
                    }).join("")}
                </div>
            `;
            wrap.scrollTop = wrap.scrollHeight;
        }

        function clearHistory() {
            if (!confirm("Clear the current chat?")) return;
            messages = [];
            renderHistory();
            document.getElementById("status").innerText = "Chat cleared";
        }

        async function fileToDataUrl(file) {
            return await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
        }

        function nowLabel() {
            return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        }

        function updateUiChips() {
            document.getElementById("memoryChip").innerText =
                document.getElementById("memoryToggle").checked ? "On" : "Off";

            const modelSelect = document.getElementById("panelModel");
            document.getElementById("modelChip").innerText =
                modelSelect.options[modelSelect.selectedIndex].text;

            const prefs = loadPrefs();
            document.getElementById("themeChip").innerText =
                prefs.theme === "system"
                    ? `System (${getCurrentThemeValue("system")})`
                    : prefs.theme.charAt(0).toUpperCase() + prefs.theme.slice(1);
        }

        async function askAI() {
            const id = {{ idn|tojson }};
            const prompt = document.getElementById("userInput").value.trim();
            const model = document.getElementById("panelModel").value;
            const memory = document.getElementById("memoryToggle").checked;
            const files = document.getElementById("fileInput").files;
            const status = document.getElementById("status");

            if (!prompt) {
                alert("Please enter a message.");
                return;
            }

            const attachments = [];
            if (files && files.length) {
                for (const f of files) {
                    if ((f.type || "").startsWith("image/")) {
                        const dataUrl = await fileToDataUrl(f);
                        attachments.push({ type: "image", name: f.name, mime: f.type, data_url: dataUrl });
                    } else {
                        const text = await f.text();
                        attachments.push({ type: "text", name: f.name, text: text.slice(0, 12000) });
                    }
                }
            }

            const previews = attachments
                .filter(a => a.type === "image")
                .map(a => ({ label: a.name, src: a.data_url }));

            messages.push({
                role: "user",
                content: prompt,
                previews: previews,
                time: nowLabel()
            });
            renderHistory();

            document.getElementById("userInput").value = "";
            document.getElementById("fileInput").value = "";
            status.innerText = "Processing...";

            try {
                const response = await fetch("/ask", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        id_code: id,
                        prompt: prompt,
                        model: model,
                        memory: memory,
                        history: memory ? messages.slice(0, -1).map(m => ({ role: m.role, content: m.content })) : [],
                        attachments: attachments
                    })
                });

                const data = await response.json();

                if (data.error) {
                    messages.push({ role: "assistant", content: "Error: " + data.error, time: nowLabel() });
                    status.innerText = "Error";
                } else {
                    messages.push({ role: "assistant", content: data.answer || "", time: nowLabel() });

                    document.getElementById("totalDisplay").innerText = Number(data.spent || 0).toFixed(6);
                    const used = Number(data.credits_used || 0);
                    const limit = data.credit_limit;
                    document.getElementById("creditsUsed").innerText = used.toFixed(2);

                    const limitText = document.getElementById("creditLimitText");
                    const bar = document.getElementById('creditsBar');

                    if (bar) {
                        if (limit !== null && limit !== undefined) {
                            const pct = Math.min((used / Number(limit)) * 100, 100);
                            bar.style.width = `${pct}%`;
                        } else {
                            bar.style.width = `${Math.min(used, 100)}%`;
                        }
                    }

                    if (limit !== null && limit !== undefined) {
                        limitText.innerText = ` / ${Number(limit).toFixed(2)}`;
                        const pct = Math.min((used / Number(limit)) * 100, 100);
                        bar.style.width = `${pct}%`;
                    } else {
                        limitText.innerText = "";
                        bar.style.width = `${Math.min(used, 100)}%`;
                    }

                    status.innerText = data.has_image ? "Replied (vision scan on)" : "Replied";
                }

                renderHistory();
                updateUiChips();
            } catch (e) {
                messages.push({ role: "assistant", content: "Connection failed.", time: nowLabel() });
                renderHistory();
                status.innerText = "Connection failed";
            }
        }

        function initSettings() {
            const saved = loadPrefs();
            syncUiFromPrefs(saved);
            applyPrefs(saved);
            updateUiChips();

            document.getElementById("panelModel").addEventListener("change", updateUiChips);
            document.getElementById("memoryToggle").addEventListener("change", () => {
                document.getElementById("panelMemory").checked = document.getElementById("memoryToggle").checked;
                updateUiChips();
                persistAndApply();
            });

            document.getElementById("panelMemory").addEventListener("change", () => {
                document.getElementById("memoryToggle").checked = document.getElementById("panelMemory").checked;
                updateUiChips();
                persistAndApply();
            });

            [
                "themeSelect",
                "accentSelect",
                "densitySelect",
                "radiusSelect",
                "textSelect",
                "motionSelect",
                "timestampToggle"
            ].forEach(id => {
                document.getElementById(id).addEventListener("change", () => {
                    persistAndApply();
                    updateUiChips();
                    renderHistory();
                });
            });

            if (systemThemeQuery.addEventListener) {
                systemThemeQuery.addEventListener("change", () => {
                    const prefs = loadPrefs();
                    if (prefs.theme === "system") applyPrefs(prefs);
                    updateUiChips();
                });
            }
        }

        document.getElementById("userInput").addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                askAI();
            }
        });

        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") document.getElementById("settingsPanel").classList.remove("open");
        });

        initSettings();
        renderHistory();

        setTimeout(() => {
            const intro = document.getElementById("introSplash");
            if (intro) intro.remove();
        }, 3200);
    </script>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Sign In - N Tech AI</title>
    <style>
        body{font-family:Inter,system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;background:#f5f7fb;margin:0}
        .card{background:#fff;border:1px solid #d9e1ee;border-radius:16px;padding:28px;min-width:340px}
        input,button{width:100%;padding:11px 12px;border-radius:10px;border:1px solid #d9e1ee;box-sizing:border-box}
        button{margin-top:10px;background:#2563eb;color:#fff;border:none;font-weight:700}
        .err{color:#b42318;margin-top:10px}
    </style>
</head>
<body>
    <div class="card">
        <h2 style="margin-top:0;">N Tech AI Sign In</h2>
        <form method="POST" action="/login">
            <input name="idn" type="password" placeholder="Enter IDN" required />
            <button type="submit">Continue</button>
            {% if error %}<div class="err">{{ error }}</div>{% endif %}
        </form>
    </div>
</body>
</html>
"""


DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Admin Dashboard</title>
    <style>
        """ + BASE_STYLE + """
        body { max-width: 1200px; margin: 34px auto; padding: 16px; }
        .topnav { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:18px; }
        .card { background:#fff; border:1px solid #d9e1ee; border-radius:16px; padding:18px; margin-bottom:18px; }
        .grid { display:grid; grid-template-columns: 1fr 1fr 1fr auto; gap:8px; align-items:end; }
        .log-grid { display:grid; grid-template-columns: 1fr; gap:12px; }
        .log-card { border:1px solid #e5e7eb; border-radius:14px; padding:14px; background:#fff; }
        .log-meta { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
        .thumb { max-width:180px; max-height:180px; border-radius:12px; border:1px solid #d9e1ee; display:block; margin-top:10px; }
        .actions { margin-top:10px; display:flex; gap:8px; flex-wrap:wrap; }
        .muted { color:#667085; }
        a { color:#2563eb; text-decoration:none; font-weight:600; }
    </style>
</head>
<body>
    <div class="topnav">
        <a href="/">&larr; Back to Chat</a>
        <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <a href="/data">Manage Data</a>
            <a href="/dashboard">Refresh</a>
        </div>
    </div>

    <div class="card">
        <h2 style="margin-top:0;">User Spend Dashboard</h2>
        <form action="/add_account" method="POST" class="grid">
            <div><label>ID Code</label><input name="id_code" required></div>
            <div><label>Display Name</label><input name="display_name" required></div>
            <div><label>Credit Limit</label><input name="credit_limit" type="number" step="0.01" min="0" placeholder="e.g. 10"></div>
            <button type="submit" class="btn" style="border:none;cursor:pointer;">Add Account</button>
        </form>

        <div style="overflow-x:auto;margin-top:14px;">
            <table>
                <tr>
                    <th>Assigned Name</th><th>ID Code</th><th>Total Spent ($)</th><th>Credits Used</th><th>Credit Limit</th><th>Actions</th>
                </tr>
                {% for row in data %}
                <tr>
                    <td>{{ row[2] or user_map.get(row[0], row[0]) }}</td>
                    <td>{{ row[0] }}</td>
                    <td>${{ "%.6f"|format(row[1]) }}</td>
                    <td>{{ "%.2f"|format(row[1] * 1000) }}</td>
                    <td>{{ "%.2f"|format(row[3]) if row[3] is not none else "—" }}</td>
                    <td>
                        <form action="/delete_account" method="POST" onsubmit="return confirm('Delete this account?');">
                            <input type="hidden" name="id_code" value="{{ row[0] }}">
                            <button type="submit" class="btn danger" style="border:none;">Delete</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>

    <div class="card">
        <div class="log-meta">
            <h2 style="margin:0;">Recent Logs</h2>
            <div class="muted">Dismissed logs are removed from the database</div>
        </div>
        <div class="log-grid">
            {% for log in logs %}
            <div class="log-card">
                <div class="log-meta">
                    <div>
                        <strong>{{ user_map.get(log[1], log[1]) }}</strong>
                        <div class="muted">#{{ log[0] }} • {{ log[2] }} • {{ log[12] }}</div>
                    </div>
                    <div class="muted">{{ log[5] or 'no model' }}{% if log[6] %} • ${{ "%.6f"|format(log[6]) }}{% endif %}</div>
                </div>

                {% if log[3] %}
                <div><strong>Prompt:</strong> {{ log[3] }}</div>
                {% endif %}

                {% if log[2] == 'chat' %}
                    <div style="margin-top:8px;"><strong>Response:</strong> {{ log[4] }}</div>
                {% elif log[2] == 'vision_scan' %}
                    <div style="margin-top:8px;"><strong>Scan:</strong> {{ log[10] or log[4] }}</div>
                {% elif log[2] == 'image_generation' %}
                    <div style="margin-top:8px;"><strong>Generated image</strong></div>
                {% endif %}

                {% if log[8] %}
                    <img class="thumb" src="{{ log[8] }}" alt="image preview">
                {% endif %}

                {% if log[7] %}
                    <div class="muted" style="margin-top:6px;">File: {{ log[7] }}</div>
                {% endif %}

                {% if log[9] %}
                    <div class="muted" style="margin-top:6px;">Analysis: {{ log[9] }}</div>
                {% endif %}

                <div class="actions">
                    <form action="/dismiss_log" method="POST" onsubmit="return confirm('Dismiss this log entry?');">
                        <input type="hidden" name="log_id" value="{{ log[0] }}">
                        <button type="submit" class="btn danger" style="border:none;">Dismiss</button>
                    </form>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""


DATA_EDIT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Edit Database</title>
    <style>
        body { font-family: sans-serif; max-width: 500px; margin: 50px auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px; }
        select, input { width: 100%; padding: 12px; margin: 10px 0; border-radius: 5px; border: 1px solid #ccc; }
        button { width: 100%; padding: 12px; background: #28a745; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; }
        .back { margin-bottom: 20px; display: block; color: #007bff; text-decoration: none; }
    </style>
</head>
<body>
    <a href="/dashboard" class="back">&larr; Back to Dashboard</a>
    <h2>Manual Data Override</h2>
    <form action="/update_data" method="POST">
        <label>Select User:</label>
        <select name="id_code">
            {% for id_code in allowed_ids %}
            <option value="{{ id_code }}">{{ user_map.get(id_code, id_code) }}</option>
            {% endfor %}
        </select>
        <label>New Total Spent ($):</label>
        <input type="number" step="0.000001" name="new_amount" placeholder="0.000000" required>
        <button type="submit">Update Database</button>
    </form>
</body>
</html>
"""


IMAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>N Tech AI Images</title>
    <style>
        """ + BASE_STYLE + """
        body { max-width: 940px; margin: 24px auto; padding: 16px; }
        .card { padding: 20px; }
        textarea { min-height: 120px; resize: vertical; margin-bottom: 10px; }
        button { font-weight: 700; }
        .row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; gap: 10px; }
        .muted { color: #667085; font-size: 0.9rem; }
        img { max-width: 100%; border-radius: 12px; margin-top: 12px; border: 1px solid #d9e1ee; }
        a { color: #2563eb; text-decoration: none; font-weight: 600; }
    </style>
</head>
<body>
    <div class="card glass">
        <div class="row">
            <a href="/">&larr; Back to Chat</a>
            <div class="muted">N Tech AI Images • 1024x1024</div>
        </div>
        <h2 style="margin-top:0;">N Tech AI Image Generation</h2>
        <div class="muted">Signed in as: {{ idn }}</div>
        <label>Quality</label>
        <select id="quality" onchange="handleQualityChange()">
            <option value="low">Low (~$0.009/image)</option>
            <option value="high">High (~$0.035/image)</option>
        </select>
        <textarea id="prompt" placeholder="Describe the image you want..."></textarea>
        <button class="btn" onclick="generateImage()">Generate</button>
        <div id="status" class="muted" style="margin-top:10px;">Ready</div>
        <div class="muted" style="margin-top:6px;">Session Spent: $<span id="imageSpent">0.000000</span></div>
        <div class="muted">Credits Used: <span id="imageCredits">0.00</span><span id="imageCreditLimit"></span></div>
        <div id="result"></div>
    </div>

    <script>
        function handleQualityChange() {
            const quality = document.getElementById('quality').value;
            if (quality === 'high') {
                alert('High quality images cost approximately $0.035 per 1024×1024 image.');
            }
        }

        async function generateImage() {
            const idCode = {{ idn|tojson }};
            const prompt = document.getElementById('prompt').value.trim();
            const quality = document.getElementById('quality').value;
            const status = document.getElementById('status');
            const result = document.getElementById('result');
            if (!prompt) {
                alert('Please enter a prompt.');
                return;
            }
            status.innerText = 'Generating...';
            result.innerHTML = '';
            try {
                const response = await fetch('/generate_image', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id_code: idCode, prompt, quality})
                });
                const data = await response.json();
                if (data.error) {
                    status.innerText = 'Error';
                    result.innerHTML = `<div class="muted">Error: ${data.error}</div>`;
                    return;
                }
                document.getElementById('imageSpent').innerText = Number(data.spent || 0).toFixed(6);
                document.getElementById('imageCredits').innerText = Number(data.credits_used || 0).toFixed(2);
                document.getElementById('imageCreditLimit').innerText = (data.credit_limit !== null && data.credit_limit !== undefined)
                    ? ` / ${Number(data.credit_limit).toFixed(2)}`
                    : '';
                status.innerText = 'Done';
                result.innerHTML = `<img alt="Generated image" src="data:image/png;base64,${data.image_b64}">`;
            } catch (e) {
                status.innerText = 'Connection failed';
            }
        }
    </script>
</body>
</html>
"""


CODE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>N Code</title>
    <style>
        """ + BASE_STYLE + """
        body { max-width: 1200px; margin: 24px auto; padding: 16px; color:#111827; }
        .card { padding:20px; }
        .top { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }
        .grid { display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
        textarea { min-height:140px; resize:vertical; }
        pre { background:#0b1020; color:#d7e3ff; border-radius:12px; padding:14px; overflow:auto; min-height:120px; margin-top:12px; white-space:pre-wrap; }
        .blocks { margin-top: 12px; display: grid; gap: 12px; }
        .code-card { border: 1px solid #1f2a44; border-radius: 12px; overflow: hidden; background: #0b1020; }
        .code-head { padding: 8px 12px; font-size: 12px; color: #cbd5e1; background: #111a2f; border-bottom: 1px solid #1f2a44; display:flex; justify-content:space-between; align-items:center; }
        .copy { border:0; background:#1d4ed8; color:#fff; border-radius:8px; padding:5px 9px; cursor:pointer; font-size:12px; }
        .row { display:flex; gap:10px; align-items:center; }
        .pill { background:#eef2ff; color:#1e3a8a; border-radius:999px; padding:6px 10px; font-size:12px; font-weight:600; }
        a { color:#2563eb; text-decoration:none; font-weight:600; }
    </style>
</head>
<body>
    <div class="card glass">
        <div class="top">
            <a href="/">&larr; Back to Chat</a>
            <div class="row">
                <span class="pill">N Code</span>
                <span class="muted">Code Studio</span>
            </div>
        </div>
        <h2 style="margin:0 0 6px;">N-Code</h2>
        <div class="muted" style="margin-bottom:12px;">Describe what you want to build, then get clean generated code with N Code</div>
        <div class="grid">
            <div class="muted">Signed in as {{ idn }}</div>
            <div><input id="language" placeholder="Language / framework (e.g. Python, HTML, Javascript)"></div>
        </div>
        <textarea id="prompt" placeholder="Describe the code to generate, requirements, and edge cases..."></textarea>
        <button class="btn" onclick="generateCode()">Generate Code</button>
        <div class="row" style="margin-top:10px; justify-content:space-between;">
            <div class="muted">Session Spent: $<span id="spent">0.000000</span></div>
            <div class="muted">Credits: <span id="credits">0.00</span><span id="limit"></span></div>
            <div class="muted" id="status">Ready</div>
        </div>
        <pre id="output">// Full response appears here...</pre>
        <div id="blocks" class="blocks"></div>
    </div>
    <script>
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.innerText = text || '';
            return div.innerHTML;
        }

        function parseCodeBlocks(text) {
            const blocks = [];
            const regex = /```([a-zA-Z0-9_+-]*)\\n([\\s\\S]*?)```/g;
            let m;
            while ((m = regex.exec(text)) !== null) {
                blocks.push({lang: m[1] || 'code', code: m[2] || ''});
            }
            return blocks;
        }

        function renderCodeBlocks(text) {
            const blocksWrap = document.getElementById('blocks');
            const blocks = parseCodeBlocks(text);
            if (!blocks.length) {
                blocksWrap.innerHTML = '<div class="muted">No fenced code blocks found; showing full output above.</div>';
                return;
            }
            blocksWrap.innerHTML = blocks.map((b, i) => `
                <div class="code-card">
                    <div class="code-head">
                        <span>${escapeHtml(b.lang)} block #${i + 1}</span>
                        <button class="copy" onclick="copyBlock(${i})">Copy</button>
                    </div>
                    <pre id="block-${i}">${escapeHtml(b.code)}</pre>
                </div>
            `).join('');
            window.__codeBlocks = blocks;
        }

        function copyBlock(idx) {
            const block = (window.__codeBlocks || [])[idx];
            if (!block) return;
            navigator.clipboard.writeText(block.code);
        }

        async function generateCode() {
            const id_code = {{ idn|tojson }};
            const language = document.getElementById('language').value.trim();
            const prompt = document.getElementById('prompt').value.trim();
            const status = document.getElementById('status');
            if (!prompt) { alert('Please enter a prompt.'); return; }
            status.innerText = 'Generating...';
            try {
                const response = await fetch('/generate_code', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({id_code, prompt, language})
                });
                const data = await response.json();
                if (data.error) {
                    status.innerText = 'Error';
                    document.getElementById('output').innerText = `Error: ${data.error}`;
                    return;
                }
                const output = data.code || '';
                document.getElementById('output').innerText = output;
                renderCodeBlocks(output);
                document.getElementById('spent').innerText = Number(data.spent || 0).toFixed(6);
                document.getElementById('credits').innerText = Number(data.credits_used || 0).toFixed(2);
                document.getElementById('limit').innerText = data.credit_limit !== null && data.credit_limit !== undefined ? ` / ${Number(data.credit_limit).toFixed(2)}` : '';
                status.innerText = 'Done';
            } catch (e) {
                status.innerText = 'Connection failed';
                document.getElementById('output').innerText = 'Connection failed.';
            }
        }
    </script>
</body>
</html>
"""

# ----------------------------
# Routes
# ----------------------------
@app.route("/")
def index():
    if not session.get("idn") or not user_exists(session.get("idn")):
        session.pop("idn", None)
        return redirect(url_for("login_page"))
    return render_template_string(CHAT_TEMPLATE, idn=session.get("idn"), is_admin=is_admin_session())


@app.route("/login", methods=["GET"])
def login_page():
    return render_template_string(LOGIN_TEMPLATE, error=None)


@app.route("/login", methods=["POST"])
def login_action():
    idn = normalize_id_code(request.form.get("idn"))
    if not user_exists(idn):
        return render_template_string(LOGIN_TEMPLATE, error="Invalid IDN"), 403
    session["idn"] = idn
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.pop("idn", None)
    return redirect(url_for("login_page"))


@app.route("/image")
def image_page():
    if not session.get("idn") or not user_exists(session.get("idn")):
        session.pop("idn", None)
        return redirect(url_for("login_page"))
    return render_template_string(IMAGE_TEMPLATE, idn=session.get("idn"))


@app.route("/code")
def code_page():
    if not session.get("idn") or not user_exists(session.get("idn")):
        session.pop("idn", None)
        return redirect(url_for("login_page"))
    return render_template_string(CODE_TEMPLATE, idn=session.get("idn"))


@app.route("/dashboard")
def dashboard():
    if not is_admin_session():
        return redirect(url_for("index"))
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id_code, total_spent, display_name, credit_limit FROM users ORDER BY total_spent DESC;")
        db_data = cursor.fetchall()
        cursor.close()
        conn.close()

        logs = fetch_logs(limit=100)
        return render_template_string(DASHBOARD_TEMPLATE, data=db_data, user_map=USER_MAP, logs=logs)
    except Exception as e:
        return f"Dashboard unavailable: {e}", 500


@app.route("/dismiss_log", methods=["POST"])
def dismiss_log():
    if not is_admin_session():
        return redirect(url_for("index"))
    log_id = request.form.get("log_id")
    try:
        if log_id:
            delete_log(int(log_id))
    except Exception as e:
        return f"Error dismissing log: {e}", 500
    return redirect(url_for("dashboard"))


@app.route("/add_account", methods=["POST"])
def add_account():
    if not is_admin_session():
        return redirect(url_for("index"))
    id_code = normalize_id_code(request.form.get("id_code"))
    display_name = (request.form.get("display_name") or "").strip() or id_code
    credit_limit = parse_credit_limit(request.form.get("credit_limit"))

    if not id_code:
        return "ID code is required", 400
    if credit_limit == "invalid":
        return "Invalid credit amount", 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (id_code, total_spent, display_name, credit_limit)
            VALUES (%s, 0.0, %s, %s)
            ON CONFLICT (id_code) DO UPDATE
            SET display_name = EXCLUDED.display_name, credit_limit = EXCLUDED.credit_limit;
            """,
            (id_code, display_name, credit_limit),
        )
        conn.commit()
        cursor.close()
        conn.close()
        if id_code not in ALLOWED_IDS:
            ALLOWED_IDS.append(id_code)
        USER_MAP[id_code] = display_name
        return redirect(url_for("dashboard"))
    except Exception as e:
        return f"Error adding account: {e}", 500


@app.route("/delete_account", methods=["POST"])
def delete_account():
    if not is_admin_session():
        return redirect(url_for("index"))
    id_code = normalize_id_code(request.form.get("id_code"))
    if not id_code:
        return redirect(url_for("dashboard"))
    if id_code == ADMIN_IDN:
        return "Cannot delete admin account", 400
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id_code = %s", (id_code,))
        conn.commit()
        cursor.close()
        conn.close()
        USER_MAP.pop(id_code, None)
        if id_code in ALLOWED_IDS:
            ALLOWED_IDS.remove(id_code)
        return redirect(url_for("dashboard"))
    except Exception as e:
        return f"Error deleting account: {e}", 500


@app.route("/data")
def edit_data():
    if not is_admin_session():
        return redirect(url_for("index"))
    return render_template_string(DATA_EDIT_TEMPLATE, allowed_ids=get_allowed_ids(), user_map=USER_MAP)


@app.route("/update_data", methods=["POST"])
def update_data():
    if not is_admin_session():
        return redirect(url_for("index"))
    id_code = request.form.get("id_code")
    new_amount = request.form.get("new_amount")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET total_spent = %s WHERE id_code = %s", (new_amount, id_code))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("dashboard"))
    except Exception as e:
        return f"Error updating database: {e}", 500


@app.route("/ask", methods=["POST"])
def ask():
    data = request.json or {}
    id_code = normalize_id_code(data.get("id_code", ""))
    selected_model = data.get("model", "gpt-4o-mini")
    user_prompt = (data.get("prompt") or "").strip()
    memory_enabled = bool(data.get("memory", True))
    history = data.get("history", []) if memory_enabled else []
    attachments = data.get("attachments", [])

    if not user_exists(id_code):
        return jsonify({"error": "Unauthorized Access ID"}), 403

    if selected_model not in MODEL_PRICING:
        return jsonify({"error": "Unsupported model selected."}), 400

    if not user_prompt:
        return jsonify({"error": "Prompt is empty."}), 400

    try:
        messages = [{"role": "system", "content": f"Authenticated user IDN: {id_code}"}]
        for message in history:
            role = message.get("role")
            content = message.get("content", "")
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})

        user_content = [{"type": "text", "text": user_prompt}]
        image_entries = []
        text_entries = []

        for item in attachments:
            item_type = (item or {}).get("type")
            if item_type == "image":
                data_url = (item or {}).get("data_url", "")
                name = (item or {}).get("name", "image.png")
                mime = (item or {}).get("mime", "image/png")
                if data_url.startswith("data:image/"):
                    user_content.append({"type": "image_url", "image_url": {"url": data_url}})
                    image_entries.append({"name": name, "mime": mime, "data_url": data_url})
            elif item_type == "text":
                name = (item or {}).get("name", "attachment.txt")
                text = (item or {}).get("text", "")
                if text:
                    wrapped = f"[Attached file: {name}]\n{text}"
                    user_content.append({"type": "text", "text": wrapped})
                    text_entries.append({"name": name, "text": text})

        messages.append({"role": "user", "content": user_content})

        model_for_request = VISION_MODEL if image_entries else selected_model
        if model_for_request not in MODEL_PRICING:
            return jsonify({"error": f"Model pricing is not configured for {model_for_request}."}), 400

        res = client.chat.completions.create(model=model_for_request, messages=messages)
        answer = res.choices[0].message.content or ""
        usage = getattr(res, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cost = cost_from_usage(model_for_request, prompt_tokens, completion_tokens)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT total_spent, credit_limit FROM users WHERE id_code = %s", (id_code,))
        current = cursor.fetchone()
        current_spent = current[0] if current else 0.0
        credit_limit = current[1] if current else None
        next_credits = (current_spent + cost) * 1000
        if credit_limit is not None and next_credits > credit_limit:
            cursor.close()
            conn.close()
            return jsonify({"error": "Invalid credit amount"}), 400

        cursor.execute("UPDATE users SET total_spent = total_spent + %s WHERE id_code = %s", (cost, id_code))
        conn.commit()
        cursor.execute("SELECT total_spent, credit_limit FROM users WHERE id_code = %s", (id_code,))
        new_total = cursor.fetchone()
        cursor.close()
        conn.close()

        insert_log(
            id_code=id_code,
            log_type="chat",
            prompt=user_prompt,
            response=answer,
            model=model_for_request,
            cost=cost,
        )

        # Store uploaded images and their vision analysis separately for admin review.
        if image_entries:
            for img in image_entries:
                analysis_prompt = f"Analyze this uploaded image in context of the user's message: {user_prompt}"
                analysis_text = answer
                # Re-run on each uploaded image so each file gets its own admin-visible scan.
                try:
                    analysis_text, analysis_cost, analysis_model = analyze_image_with_vision(img["data_url"], analysis_prompt)
                except Exception:
                    analysis_cost = 0.0
                    analysis_model = model_for_request
                    analysis_text = answer

                insert_log(
                    id_code=id_code,
                    log_type="vision_scan",
                    prompt=user_prompt,
                    response="",
                    model=analysis_model,
                    cost=analysis_cost,
                    media_name=img["name"],
                    media_mime=img["mime"],
                    media_b64=img["data_url"],
                    analysis=analysis_text,
                )

        return jsonify({
            "answer": answer,
            "spent": new_total[0],
            "cost": cost,
            "credits_used": new_total[0] * 1000,
            "credit_limit": new_total[1],
            "model_used": model_for_request,
            "has_image": bool(image_entries),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/generate_image", methods=["POST"])
def generate_image():
    data = request.json or {}
    id_code = normalize_id_code(data.get("id_code", ""))
    prompt = (data.get("prompt") or "").strip()
    quality = (data.get("quality") or "low").strip().lower()

    if not user_exists(id_code):
        return jsonify({"error": "Unauthorized Access ID"}), 403
    if not prompt:
        return jsonify({"error": "Prompt is empty."}), 400
    if quality not in IMAGE_PRICING:
        return jsonify({"error": "Unsupported quality selected."}), 400

    try:
        image_cost = IMAGE_PRICING[quality]
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT total_spent, credit_limit FROM users WHERE id_code = %s", (id_code,))
        current = cursor.fetchone()
        current_spent = current[0] if current else 0.0
        credit_limit = current[1] if current else None
        next_credits = (current_spent + image_cost) * 1000
        if credit_limit is not None and next_credits > credit_limit:
            cursor.close()
            conn.close()
            return jsonify({"error": "Invalid credit amount"}), 400

        res = client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            quality=quality,
            size="1024x1024",
        )

        image_b64 = res.data[0].b64_json

        cursor.execute("UPDATE users SET total_spent = total_spent + %s WHERE id_code = %s", (image_cost, id_code))
        conn.commit()
        cursor.execute("SELECT total_spent, credit_limit FROM users WHERE id_code = %s", (id_code,))
        new_total = cursor.fetchone()
        cursor.close()
        conn.close()

        insert_log(
            id_code=id_code,
            log_type="image_generation",
            prompt=prompt,
            response="Image generated successfully.",
            model=IMAGE_MODEL,
            cost=image_cost,
            media_name="generated-image.png",
            media_mime="image/png",
            media_b64=f"data:image/png;base64,{image_b64}",
            analysis=f"Generated image at quality: {quality}",
            quality=quality,
        )

        return jsonify({
            "image_b64": image_b64,
            "spent": new_total[0],
            "credits_used": new_total[0] * 1000,
            "credit_limit": new_total[1],
            "cost": image_cost,
        })
    except Exception as e:
        msg = str(e)
        lowered = msg.lower()
        if "invalid_project" in lowered or "invalid project id" in lowered:
            msg = (
                "Invalid project ID. OPENAI_PROJECT_ID must be your project id (usually starts with 'proj_'), "
                "not your API key (starts with 'sk-')."
            )
        elif "organization" in lowered:
            msg = (
                "OpenAI organization configuration error. Ensure OPENAI_ORGANIZATION/OPENAI_ORG_ID matches the "
                "organization that owns your API key and project."
            )
        return jsonify({"error": msg}), 500


@app.route("/generate_code", methods=["POST"])
def generate_code():
    data = request.json or {}
    id_code = normalize_id_code(data.get("id_code", ""))
    prompt = (data.get("prompt") or "").strip()
    language = (data.get("language") or "").strip()
    model_name = "gpt-5.1-codex-mini"

    if not user_exists(id_code):
        return jsonify({"error": "Unauthorized Access ID"}), 403
    if not prompt:
        return jsonify({"error": "Prompt is empty."}), 400

    full_prompt = f"Language/Stack: {language or 'Not specified'}\n\nTask:\n{prompt}\n\nReturn code first, then a short explanation."
    try:
        res = client.responses.create(
            model=model_name,
            input=f"Authenticated user IDN: {id_code}\n\n{full_prompt}"
        )
        code_answer = res.output_text
        input_tokens = getattr(getattr(res, "usage", None), "input_tokens", 0) or 0
        output_tokens = getattr(getattr(res, "usage", None), "output_tokens", 0) or 0
        cost = cost_from_usage(model_name, input_tokens, output_tokens)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT total_spent, credit_limit FROM users WHERE id_code = %s", (id_code,))
        current = cursor.fetchone()
        current_spent = current[0] if current else 0.0
        credit_limit = current[1] if current else None
        next_credits = (current_spent + cost) * 1000
        if credit_limit is not None and next_credits > credit_limit:
            cursor.close()
            conn.close()
            return jsonify({"error": "Invalid credit amount"}), 400

        cursor.execute("UPDATE users SET total_spent = total_spent + %s WHERE id_code = %s", (cost, id_code))
        conn.commit()
        cursor.execute("SELECT total_spent, credit_limit FROM users WHERE id_code = %s", (id_code,))
        new_total = cursor.fetchone()
        cursor.close()
        conn.close()

        insert_log(
            id_code=id_code,
            log_type="code",
            prompt=prompt,
            response=code_answer,
            model=model_name,
            cost=cost,
            analysis=f"Language: {language or 'Not specified'}",
        )

        return jsonify({
            "code": code_answer,
            "spent": new_total[0],
            "cost": cost,
            "credits_used": new_total[0] * 1000,
            "credit_limit": new_total[1],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
