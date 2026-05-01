from flask import Flask, render_template_string, request, jsonify, redirect, url_for
from openai import OpenAI
import os
import psycopg2

app = Flask(__name__)

# --- CONFIGURATION ---
client = OpenAI(api_key=os.environ.get("api_key"))

# Prices are USD per 1K tokens
MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-5.4-nano": {"input": 0.00005, "output": 0.0002},
    "gpt-5.4-mini": {"input": 0.0003, "output": 0.0012},
}

raw_ids = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS = [i.strip() for i in raw_ids.split(",") if i.strip()]

USER_MAP = {
    "nathan": "Admin (Nathan)",
    "1865": "Michael", "002": "User 002", "003": "User 003",
    "1793": "Quinn", "005": "User 005", "006": "User 006",
    "007": "User 007", "008": "User 008", "004": "User 004", "010": "User 010"
}

# --- DATABASE LOGIC ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    if not DATABASE_URL:
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS users (id_code VARCHAR(50) PRIMARY KEY, total_spent FLOAT DEFAULT 0.0);")
        for id_code in ALLOWED_IDS:
            cursor.execute("INSERT INTO users (id_code, total_spent) VALUES (%s, 0.0) ON CONFLICT (id_code) DO NOTHING;", (id_code,))
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
    <title>N Tech AI</title>
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
        body {
            font-family: Inter, system-ui, sans-serif;
            max-width: 860px;
            margin: 24px auto;
            padding: 16px;
            background: var(--bg);
            color: #111827;
        }
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
        .controls {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin: 12px 0;
        }
        input, textarea, select {
            width: 100%;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid var(--border);
            box-sizing: border-box;
            background: #fff;
        }
        textarea { min-height: 110px; resize: vertical; }
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
        .chat {
            background: #fff;
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 14px;
            margin-top: 12px;
            min-height: 200px;
            max-height: 440px;
            overflow-y: auto;
        }
        .msg { padding: 10px 12px; border-radius: 10px; margin-bottom: 10px; white-space: pre-wrap; }
        .msg.user { background: var(--user); }
        .msg.assistant { background: var(--assistant); }
        .row { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
        .toggle { display: flex; align-items: center; gap: 8px; }
        .toggle input { width: auto; }
        .nav a { color: var(--primary); text-decoration: none; font-weight: 600; display: none; }
    </style>
</head>
<body>
    <div class="card">
        <div class="topbar">
            <div>
                <h2 style="margin:0;">N Tech AI 1.9</h2>
                <div class="muted">1.9 new features: chat history, optional memory. Note: 1.9 Smart is the same as 1.8 Ultra</div>
            </div>
            <div class="nav"><a href="/dashboard" id="adminLink">Admin Dashboard →</a></div>
        </div>

        <div class="controls">
            <input type="password" id="idCode" placeholder="Enter IDN" oninput="checkAdmin()">
            <select id="modelSelect">
                <option value="gpt-4o-mini">N Tech 1.7 Basic </option>
                <option value="gpt-4o">N Tech 1.7 Smart</option>
                <option value="gpt-5.4-nano">N Tech AI 1.8 Smart</option>
                <option value="gpt-5.4-mini">N Tech AI 1.9 Smart</option>
            </select>
        </div>

        <div class="row">
            <label class="toggle">
                <input type="checkbox" id="memoryToggle" checked>
                Remember previous outputs for context
            </label>
            <button style="width:auto;background:#475467;" onclick="clearHistory()">Clear chat</button>
        </div>

        <textarea id="userInput" placeholder="Ask anything..."></textarea>
        <button onclick="askAI()">Send to AI</button>

        <div class="chat" id="chatHistory"></div>
        <div class="row" style="margin-top:10px;">
            <div class="muted">Session Spent: $<span id="totalDisplay">0.000000</span></div>
            <div class="muted" id="status">Ready</div>
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
    <table>
        <tr><th>Assigned Name</th><th>Total Spent ($)</th></tr>
        {% for id_code, amount in data %}
        <tr><td>{{ user_map.get(id_code, id_code) }}</td><td>${{ "%.6f"|format(amount) }}</td></tr>
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


@app.route('/')
def index():
    return render_template_string(CHAT_TEMPLATE)


@app.route('/dashboard')
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id_code, total_spent FROM users ORDER BY total_spent DESC;")
    db_data = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template_string(DASHBOARD_TEMPLATE, data=db_data, user_map=USER_MAP)


@app.route('/data')
def edit_data():
    return render_template_string(DATA_EDIT_TEMPLATE, allowed_ids=ALLOWED_IDS, user_map=USER_MAP)


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
        return f"Error updating database: {e}"


@app.route('/ask', methods=['POST'])
def ask():
    data = request.json or {}
    id_code = data.get('id_code', '').strip()
    selected_model = data.get('model', 'gpt-4o-mini')
    user_prompt = (data.get('prompt') or '').strip()
    memory_enabled = bool(data.get('memory', True))
    history = data.get('history', []) if memory_enabled else []

    if id_code not in ALLOWED_IDS:
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
        cursor.execute("UPDATE users SET total_spent = total_spent + %s WHERE id_code = %s", (cost, id_code))
        conn.commit()
        cursor.execute("SELECT total_spent FROM users WHERE id_code = %s", (id_code,))
        new_total = cursor.fetchone()
        cursor.close()
        conn.close()

        return jsonify({"answer": answer, "spent": new_total[0], "cost": cost})
    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == '__main__':
    app.run(debug=True)
