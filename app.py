from flask import Flask, render_template_string, request, jsonify, redirect, url_for
from openai import OpenAI
import os
import psycopg2

app = Flask(__name__)

# --- CONFIGURATION ---
client = OpenAI(api_key=os.environ.get("api_key"))

# Updated pricing to include GPT-4o
MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-5.4-mini": {"input": 0.0075, "output": 0.00450}, # Placeholder for 5.4 mini
    "gpt-5.4": {"input": 0.025, "output": 0.015}          # Placeholder for 5.4
}


raw_ids = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS = [i.strip() for i in raw_ids.split(",") if i.strip()]

USER_MAP = {
    "nathan": "Admin (Nathan)",
    "001": "User 001", "002": "User 002", "003": "User 003",
    "004": "User 004", "005": "User 005", "006": "User 006",
    "007": "User 007", "008": "User 008", "009": "User 009", "010": "User 010"
}

# --- DATABASE LOGIC ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if not DATABASE_URL: return
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
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; position: relative; }
        textarea { width: 100%; height: 120px; padding: 12px; border-radius: 8px; border: 1px solid #ccc; box-sizing: border-box; }
        input { width: 100%; padding: 12px; border-radius: 8px; border: 1px solid #ccc; box-sizing: border-box; margin-bottom: 15px; }
        button { padding: 12px 24px; background: #007bff; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%; }
        #response { background: #f8f9fa; padding: 20px; margin-top: 25px; border-radius: 8px; border: 1px solid #ddd; min-height: 50px; white-space: pre-wrap; }
        .stats { margin-top: 15px; font-weight: bold; color: #555; text-align: center; }
        .nav { text-align: right; margin-bottom: 20px; min-height: 24px; }
        .nav a { color: #007bff; text-decoration: none; font-weight: bold; display: none; }
        .model-selector { position: absolute; top: 10px; left: 20px; }
        select { padding: 8px; border-radius: 5px; border: 1px solid #ccc; background: white; cursor: pointer; }
    </style>
</head>
<body>
    <div class="model-selector">
        <label style="font-size: 12px; color: #666; display: block;">AI Model:</label>
        <select id="modelSelect">
            <option value="gpt-4o-mini">N Tech 1.7 Basic</option>
            <option value="gpt-4o">N Tech 1.7 Smart</option>
            <option value="gpt-5.4-mini">N Tech 1.8 Smart</option>
            <option value="gpt-5.4">N Tech 1.8 Ultra</option>
        </select>

    </div>

    <div class="nav"><a href="/dashboard" id="adminLink">Admin Dashboard &rarr;</a></div>
    
    <h2 style="text-align: center;">N Tech AI 1.8</h2>
    <h5 style="text-align: center;">N Tech AI now allows you to switch between Smart and Basic and even Ultra models! </h5>
    <input type="password" id="idCode" placeholder="Enter IDN" oninput="checkAdmin()">
    <textarea id="userInput" placeholder="Ask anything..."></textarea>
    
    <button onclick="askAI()">Send to AI</button>

    <div id="response">Waiting for request...</div>
    <div class="stats">Session Spent: $<span id="totalDisplay">0.000000</span></div>

    <script>
        function checkAdmin() {
            document.getElementById('adminLink').style.display = (document.getElementById('idCode').value.trim() === "nathanthenathano") ? "inline" : "none";
        }

        async function askAI() {
            const id = document.getElementById('idCode').value.trim();
            const prompt = document.getElementById('userInput').value;
            const model = document.getElementById('modelSelect').value;
            const resDiv = document.getElementById('response');
            
            if (!id || !prompt) {
                alert("Please enter both IDN and message.");
                return;
            }

            // Warning for any expensive model (anything not gpt-4o-mini)
            if (model !== "gpt-4o-mini") {
                const proceed = confirm(`WARNING: ${model} is a high-cost model. Your Spent will increase significantly faster than using 1.7 Basic. Continue?`);
                if (!proceed) return;
            }

            resDiv.innerText = "Processing...";

            try {
                const response = await fetch('/ask', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id_code: id, prompt: prompt, model: model})
                });

                const data = await response.json();
                if (data.error) {
                    resDiv.innerText = "Error: " + data.error;
                } else {
                    resDiv.innerText = data.answer;
                    document.getElementById('totalDisplay').innerText = data.spent.toFixed(6);
                }
            } catch (e) {
                resDiv.innerText = "Connection failed.";
            }
        }
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

# --- ROUTES ---

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
    data = request.json
    id_code = data.get('id_code', '').strip()
    selected_model = data.get('model', 'gpt-4o-mini')
    user_prompt = data.get('prompt')

    if id_code not in ALLOWED_IDS: 
        return jsonify({"error": "Unauthorized Access ID"}), 403

    try:
        res = client.chat.completions.create(
            model=selected_model, 
            messages=[{"role": "user", "content": user_prompt}]
        )
        answer = res.choices[0].message.content
        
        # Determine pricing based on actual model used
        pricing = MODEL_PRICING.get(selected_model, MODEL_PRICING["gpt-4o-mini"])
        cost = ((res.usage.prompt_tokens / 1000) * pricing["input"]) + \
               ((res.usage.completion_tokens / 1000) * pricing["output"])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET total_spent = total_spent + %s WHERE id_code = %s", (cost, id_code))
        conn.commit()
        cursor.execute("SELECT total_spent FROM users WHERE id_code = %s", (id_code,))
        new_total = cursor.fetchone()
        cursor.close()
        conn.close()

        return jsonify({"answer": answer, "spent": new_total[0]})
    except Exception as e: 
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    app.run(debug=True)
