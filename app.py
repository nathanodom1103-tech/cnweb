from flask import Flask, render_template_string, request, jsonify, redirect, url_for
from openai import OpenAI
import os
import psycopg2

app = Flask(__name__)

# --- CONFIGURATION ---
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

# Prices are USD per 1K tokens
MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-5.4-nano": {"input": 0.00005, "output": 0.0002},
    "gpt-5.4-mini": {"input": 0.0003, "output": 0.0012},
}
IMAGE_PRICING = {
    "low": 0.02  # flat USD cost per generated image
}

raw_ids = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS = [i.strip() for i in raw_ids.split(",") if i.strip()]

USER_MAP = {
    "nathan": "Admin (Nathan)",
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

# --- DATABASE LOGIC ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"


def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(DATABASE_URL)


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

# --- HTML TEMPLATES ---

CHAT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>N Tech AI 2.1</title>
    <style>
        :root {
            color-scheme: light;
            --bg: #f5f7fb;
            --card: #ffffff;
            --border: #d9e1ee;
            --primary: #2563eb;
            --user: #e9f2ff;
            --assistant: #f7f7f8;
            --muted: #667085;
        }
        body { font-family: Inter, system-ui, sans-serif; max-width: 920px; margin: 24px auto; padding: 16px; background: var(--bg); color: #111827; }
        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06);
            padding: 20px;
        }
        .topbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 10px;
        }
        .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }
        input, textarea, select {
            width: 100%;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid var(--border);
            box-sizing: border-box;
            background: #fff;
        }
        textarea { min-height: 70px; resize: vertical; }
        button {
            padding: 12px 16px;
            background: var(--primary);
            color: white;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 600;
            width: 100%;
        }
        .muted { color: var(--muted); font-size: 0.9rem; }
        .chat { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin-top: 12px; min-height: 360px; max-height: 520px; overflow-y: auto; }
        .msg { padding: 10px 12px; border-radius: 10px; margin-bottom: 10px; white-space: pre-wrap; }
        .msg.user { background: var(--user); }
        .msg.assistant { background: var(--assistant); }
        .row { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
        .toggle { display: flex; align-items: center; gap: 8px; }
        .toggle input { width: auto; }
        .nav a { color: var(--primary); text-decoration: none; font-weight: 600; display: none; }
        .nav a.secondary { display: inline; margin-right: 12px; }
        .composer { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 12px; }
        .prompt-row { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: end; }
        .intro {
            position: fixed;
            inset: 0;
            background: #000;
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
            animation: introFadeOut 1s ease 1.8s forwards;
        }
        .intro .logo {
            font-size: min(36vw, 300px);
            font-weight: 900;
            color: white;
            line-height: 1;
            opacity: 0;
            transform: scale(0.92);
            animation: nReveal 1.1s ease forwards;
        }
        @keyframes nReveal {
            from { opacity: 0; transform: scale(0.92); }
            to { opacity: 1; transform: scale(1); }
        }
        @keyframes introFadeOut {
            from { opacity: 1; visibility: visible; }
            to { opacity: 0; visibility: hidden; }
        }
    </style>
</head>
<body>
    <div class="intro" id="introSplash"><div class="logo">N</div></div>
    <div class="card">
        <div class="topbar">
            <div>
                <h2 style="margin:0;">N Tech AI 2.1</h2>
                <h5 style="margin:0;">By using N Tech AI 2.1, you agree to the terms and conditions.</h5>
                <div class="muted">2.1 new features: chat history, optional memory. Note: 1.9 Smart is the same as 1.8 Ultra, but not reccomended.</div>
            </div>
            <div class="nav">
                <a href="/image" class="secondary">Image Generator</a>
                <a href="/dashboard" id="adminLink">Admin Dashboard →</a>
            </div>
        </div>

        <div class="chat" id="chatHistory"></div>

        <div class="composer">
            <div class="controls">
                <input type="password" id="idCode" placeholder="Enter IDN" oninput="checkAdmin()">
                <select id="modelSelect">
                    <option value="gpt-4o-mini">N Tech 1.7 Basic</option>
                    <option value="gpt-4o">N Tech 1.7 Smart</option>
                    <option value="gpt-5.4-nano">N Tech AI 1.8 Smart</option>
                    <option value="gpt-5.4-mini">N Tech AI 1.9 Smart</option>
                    <option value="gpt-5.4-nano">N Tech AI 2.0 Basic (New!)</option>
                </select>
            </div>
            <div class="row">
                <label class="toggle">
                    <input type="checkbox" id="memoryToggle" checked>
                    Remember previous outputs for context
                </label>
                <button style="width:auto;background:#475467;" onclick="clearHistory()">Clear chat</button>
            </div>
            <div class="prompt-row">
                <textarea id="userInput" placeholder="Ask anything..."></textarea>
                <button style="width:auto;" onclick="askAI()">Send to AI</button>
            </div>
        </div>
        <div class="row" style="margin-top:10px;">
            <div class="muted">Session Spent: $<span id="totalDisplay">0.000000</span></div>
            <div class="muted" id="status">Ready</div>
        </div>
        <div class="muted" style="margin-top:8px;">Credits Used: <span id="creditsUsed">0.00</span><span id="creditLimitText"></span></div>
        <div style="margin-top:6px;background:#e5e7eb;border-radius:9999px;height:10px;overflow:hidden;">
            <div id="creditsBar" style="height:10px;width:0%;background:#2563eb;"></div>
        </div>
    </div>

    <script>
        let messages = [];

        function checkAdmin() {
            document.getElementById('adminLink').style.display = (document.getElementById('idCode').value.trim() === 'nathanthenathano') ? 'inline' : 'none';
        }

        function renderHistory() {
            const wrap = document.getElementById('chatHistory');
            if (!messages.length) {
                wrap.innerHTML = '<div class="muted">No messages yet. Start chatting.</div>';
                return;
            }
            wrap.innerHTML = messages.map(m => `<div class="msg ${m.role}"><strong>${m.role === 'user' ? 'You' : 'AI'}:</strong> ${escapeHtml(m.content)}</div>`).join('');
            wrap.scrollTop = wrap.scrollHeight;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.innerText = text || '';
            return div.innerHTML;
        }

        function clearHistory() {
            messages = [];
            renderHistory();
            document.getElementById('status').innerText = 'Chat cleared';
        }

        async function askAI() {
            const id = document.getElementById('idCode').value.trim();
            const prompt = document.getElementById('userInput').value.trim();
            const model = document.getElementById('modelSelect').value;
            const memory = document.getElementById('memoryToggle').checked;
            const status = document.getElementById('status');

            if (!id || !prompt) {
                alert('Please enter both IDN and message.');
                return;
            }

            messages.push({role: 'user', content: prompt});
            renderHistory();
            document.getElementById('userInput').value = '';
            status.innerText = 'Processing...';

            try {
                const response = await fetch('/ask', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        id_code: id,
                        prompt: prompt,
                        model: model,
                        memory: memory,
                        history: memory ? messages.slice(0, -1) : []
                    })
                });

                const data = await response.json();
                if (data.error) {
                    messages.push({role: 'assistant', content: 'Error: ' + data.error});
                    status.innerText = 'Error';
                } else {
                    messages.push({role: 'assistant', content: data.answer});
                    document.getElementById('totalDisplay').innerText = Number(data.spent || 0).toFixed(6);
                    const used = Number(data.credits_used || 0);
                    const limit = data.credit_limit;
                    document.getElementById('creditsUsed').innerText = used.toFixed(2);
                    const limitText = document.getElementById('creditLimitText');
                    const bar = document.getElementById('creditsBar');
                    if (limit !== null && limit !== undefined) {
                        limitText.innerText = ` / ${Number(limit).toFixed(2)}`;
                        bar.style.width = `${Math.min((used / Number(limit)) * 100, 100)}%`;
                    } else {
                        limitText.innerText = '';
                        bar.style.width = `${Math.min(used, 100)}%`;
                    }
                    status.innerText = memory ? 'Replied (memory on)' : 'Replied (memory off)';
                }
                renderHistory();
            } catch (e) {
                messages.push({role: 'assistant', content: 'Connection failed.'});
                renderHistory();
                status.innerText = 'Connection failed';
            }
        }

        renderHistory();
        setTimeout(() => {
            const intro = document.getElementById('introSplash');
            if (intro) intro.remove();
        }, 3200);
    </script>
</body>
</html>
"""

DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Admin Dashboard</title>
    <style>
        body { font-family: sans-serif; max-width: 700px; margin: 50px auto; padding: 20px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 15px; text-align: left; }
        th { background-color: #007bff; color: white; }
        .nav { display: flex; justify-content: space-between; margin-bottom: 20px; }
        .nav a { color: #007bff; text-decoration: none; font-weight: bold; }
        .edit-btn { background: #28a745; color: white; padding: 8px 15px; border-radius: 5px; text-decoration: none; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div class="nav">
        <a href="/">&larr; Back to Chat</a>
        <a href="/data" class="edit-btn">Manage Data (Edit Balances)</a>
    </div>
    <h2>User Spend Dashboard</h2>
    <form action="/add_account" method="POST" style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:8px;align-items:end;">
        <div><label>ID Code</label><input name="id_code" required></div>
        <div><label>Display Name</label><input name="display_name" required></div>
        <div><label>Credit Limit</label><input name="credit_limit" type="number" step="0.01" min="0" placeholder="e.g. 10"></div>
        <button type="submit" class="edit-btn" style="border:none;cursor:pointer;">Add Account</button>
    </form>
    <table>
        <tr><th>Assigned Name</th><th>ID Code</th><th>Total Spent ($)</th><th>Credits Used</th><th>Credit Limit</th></tr>
        {% for row in data %}
        <tr>
            <td>{{ row[2] or user_map.get(row[0], row[0]) }}</td>
            <td>{{ row[0] }}</td>
            <td>${{ "%.6f"|format(row[1]) }}</td>
            <td>{{ "%.2f"|format(row[1] * 1000) }}</td>
            <td>{{ "%.2f"|format(row[3]) if row[3] is not none else "—" }}</td>
        </tr>
        {% endfor %}
    </table>
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
    <title>N Tech AI 2.1</title>
    <style>
        body { font-family: Inter, system-ui, sans-serif; max-width: 860px; margin: 24px auto; padding: 16px; background: #f5f7fb; }
        .card { background: #fff; border: 1px solid #d9e1ee; border-radius: 16px; padding: 20px; }
        textarea, input, button { width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid #d9e1ee; box-sizing: border-box; }
        textarea { min-height: 120px; resize: vertical; margin-bottom: 10px; }
        button { background: #2563eb; color: white; border: none; font-weight: 600; cursor: pointer; }
        .row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .muted { color: #667085; font-size: 0.9rem; }
        img { max-width: 100%; border-radius: 12px; margin-top: 12px; border: 1px solid #d9e1ee; }
        a { color: #2563eb; text-decoration: none; font-weight: 600; }
    </style>
</head>
<body>
    <div class="card">
        <div class="row">
            <a href="/">&larr; Back to Chat</a>
            <div class="muted">Model: N Tech AI Images • Model: 2.1 Smart</div>
        </div>
        <h2 style="margin-top:0;">N Tech AI Image Generation (Not functional until 2.2)</h2>
        <input id="idCode" type="password" placeholder="Enter IDN">
        <textarea id="prompt" placeholder="Describe the image you want..."></textarea>
        <button onclick="generateImage()">Generate</button>
        <div id="status" class="muted" style="margin-top:10px;">Ready</div>
        <div class="muted" style="margin-top:6px;">Session Spent: $<span id="imageSpent">0.000000</span></div>
        <div class="muted">Credits Used: <span id="imageCredits">0.00</span><span id="imageCreditLimit"></span></div>
        <div id="result"></div>
    </div>

    <script>
        async function generateImage() {
            const idCode = document.getElementById('idCode').value.trim();
            const prompt = document.getElementById('prompt').value.trim();
            const status = document.getElementById('status');
            const result = document.getElementById('result');
            if (!idCode || !prompt) {
                alert('Please enter both IDN and prompt.');
                return;
            }
            status.innerText = 'Generating...';
            result.innerHTML = '';
            try {
                const response = await fetch('/generate_image', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id_code: idCode, prompt})
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


@app.route('/')
def index():
    return render_template_string(CHAT_TEMPLATE)

@app.route('/image')
def image_page():
    return render_template_string(IMAGE_TEMPLATE)


@app.route('/dashboard')
def dashboard():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id_code, total_spent, display_name, credit_limit FROM users ORDER BY total_spent DESC;")
        db_data = cursor.fetchall()
        cursor.close()
        conn.close()
        return render_template_string(DASHBOARD_TEMPLATE, data=db_data, user_map=USER_MAP)
    except Exception as e:
        return f"Dashboard unavailable: {e}", 500


@app.route('/add_account', methods=['POST'])
def add_account():
    id_code = normalize_id_code(request.form.get('id_code'))
    display_name = (request.form.get('display_name') or '').strip() or id_code
    credit_limit = parse_credit_limit(request.form.get('credit_limit'))

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
            (id_code, display_name, credit_limit)
        )
        conn.commit()
        cursor.close()
        conn.close()
        if id_code not in ALLOWED_IDS:
            ALLOWED_IDS.append(id_code)
        USER_MAP[id_code] = display_name
        return redirect(url_for('dashboard'))
    except Exception as e:
        return f"Error adding account: {e}", 500


@app.route('/data')
def edit_data():
    return render_template_string(DATA_EDIT_TEMPLATE, allowed_ids=get_allowed_ids(), user_map=USER_MAP)


@app.route('/update_data', methods=['POST'])
def update_data():
    id_code = request.form.get('id_code')
    new_amount = request.form.get('new_amount')
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET total_spent = %s WHERE id_code = %s", (new_amount, id_code))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('dashboard'))
    except Exception as e:
        return f"Error updating database: {e}", 500


@app.route('/ask', methods=['POST'])
def ask():
    data = request.json or {}
    id_code = normalize_id_code(data.get('id_code', ''))
    selected_model = data.get('model', 'gpt-4o-mini')
    user_prompt = (data.get('prompt') or '').strip()
    memory_enabled = bool(data.get('memory', True))
    history = data.get('history', []) if memory_enabled else []

    if id_code not in get_allowed_ids():
        return jsonify({"error": "Unauthorized Access ID"}), 403

    if selected_model not in MODEL_PRICING:
        return jsonify({"error": "Unsupported model selected."}), 400

    if not user_prompt:
        return jsonify({"error": "Prompt is empty."}), 400

    try:
        messages = []
        for message in history:
            role = message.get('role')
            content = message.get('content', '')
            if role in {'user', 'assistant'} and content:
                messages.append({'role': role, 'content': content})
        messages.append({'role': 'user', 'content': user_prompt})

        res = client.chat.completions.create(model=selected_model, messages=messages)
        answer = res.choices[0].message.content

        pricing = MODEL_PRICING[selected_model]
        cost = ((res.usage.prompt_tokens / 1000) * pricing['input']) + ((res.usage.completion_tokens / 1000) * pricing['output'])

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

        return jsonify({
            "answer": answer,
            "spent": new_total[0],
            "cost": cost,
            "credits_used": new_total[0] * 1000,
            "credit_limit": new_total[1]
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/generate_image', methods=['POST'])
def generate_image():
    data = request.json or {}
    id_code = normalize_id_code(data.get('id_code', ''))
    prompt = (data.get('prompt') or '').strip()

    if id_code not in get_allowed_ids():
        return jsonify({"error": "Unauthorized Access ID"}), 403
    if not prompt:
        return jsonify({"error": "Prompt is empty."}), 400

    try:
        image_cost = IMAGE_PRICING["low"]
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
            model="gpt-image-2",
            prompt=prompt,
            quality="low",
            size="1024x1024"
        )
        cursor.execute("UPDATE users SET total_spent = total_spent + %s WHERE id_code = %s", (image_cost, id_code))
        conn.commit()
        cursor.execute("SELECT total_spent, credit_limit FROM users WHERE id_code = %s", (id_code,))
        new_total = cursor.fetchone()
        cursor.close()
        conn.close()
        return jsonify({
            "image_b64": res.data[0].b64_json,
            "spent": new_total[0],
            "credits_used": new_total[0] * 1000,
            "credit_limit": new_total[1],
            "cost": image_cost
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
                "Image Generation is not working at the moment, go and try our new 2.0 Chat model!"
                #OpenAI organization configuration error. Ensure OPENAI_ORGANIZATION/OPENAI_ORG_ID matches the organization that owns your API key and project.
            )
        return jsonify({"error": msg}), 500


if __name__ == '__main__':
    app.run(debug=True)
