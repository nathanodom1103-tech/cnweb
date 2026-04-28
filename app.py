from flask import Flask, render_template_string, request, jsonify
from openai import OpenAI
import os
import psycopg2

app = Flask(__name__)

# --- CONFIGURATION ---
client = OpenAI(api_key=os.environ.get("api_key"))
MODEL_PRICING = {"gpt-4o-mini": {"input": 0.00015, "output": 0.0006}}

# 1. Pull secret IDs from Render Environment
raw_ids = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS = [i.strip() for i in raw_ids.split(",") if i.strip()]

# 2. Map IDs to Names for the Dashboard
# You can update the names here; they stay private on the server.
USER_MAP = {
    "nathan": "Admin (Nathan)",
    "001": "Jazz",
    "002": "None",
    "003": "Private User",
    "004": "None",
    "005": "Samson",
    "006": "None",
    "007": "Michael",
    "008": "Lily-Grace",
    "009": "Quinn",
    "010": "Nael"
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
        
        # --- ADD THIS LINE TEMPORARILY TO RESET THE TABLE ---
        cursor.execute("DROP TABLE IF EXISTS users CASCADE;")
        
        # Now create the new structure
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id_code VARCHAR(50) PRIMARY KEY, 
                total_spent FLOAT DEFAULT 0.0
            );
        """)
        
        for id_code in ALLOWED_IDS:
            cursor.execute("""
                INSERT INTO users (id_code, total_spent) 
                VALUES (%s, 0.0) 
                ON CONFLICT (id_code) DO NOTHING;
            """, (id_code,))
            
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Database Init Error: {e}")

init_db()

# --- HTML TEMPLATES ---

CHAT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>N Tech AI</title>
    <style>
        body { font-family: sans-serif; max-width: 500px; margin: 50px auto; padding: 20px; border: 1px solid #eee; border-radius: 10px; }
        input, textarea { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ccc; border-radius: 5px; box-sizing: border-box; }
        button { width: 100%; padding: 12px; background: #222; color: white; border: none; border-radius: 5px; cursor: pointer; }
        #adminLink { display: none; text-align: center; margin-bottom: 20px; }
        #adminLink a { color: #007bff; text-decoration: none; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div id="adminLink"><a href="/dashboard">Access Admin Dashboard</a></div>
    <h2>N Tech AI Version 1.5</h2>
    <h3>Warning: Please check that your IDN is still correct as N Tech switched all IDN on 4/28/2026</h3>
    <input type="password" id="idInput" placeholder="Enter your IDN given by Nathan" oninput="checkAdmin()">
    <textarea id="promptInput" placeholder="What's on your mind?"></textarea>
    <button onclick="askAI()">Submit Request</button>
    <p id="response" style="margin-top:20px; white-space: pre-wrap;"></p>

    <script>
        function checkAdmin() {
            // Only show dashboard link if typed ID is 'nathan'
            document.getElementById('adminLink').style.display = 
                (document.getElementById('idInput').value === 'nathan') ? 'block' : 'none';
        }

        async function askAI() {
            const id = document.getElementById('idInput').value;
            const prompt = document.getElementById('promptInput').value;
            const res = document.getElementById('response');
            
            res.innerText = "Thinking...";
            const response = await fetch('/ask', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id_code: id, prompt: prompt})
            });
            const data = await response.json();
            res.innerText = data.error ? "Error: " + data.error : data.answer + "\\n\\nSpent: $" + data.spent.toFixed(6);
        }
    </script>
</body>
</html>
"""

DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>N Tech AI Spending Board</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; }
        table { width: 100%; border-collapse: collapse; }
        th, td { border: 1px solid #ddd; padding: 10px; text-align: left; }
        th { background: #f4f4f4; }
    </style>
</head>
<body>
    <a href="/">&larr; Back</a>
    <h2>Spending by User Name</h2>
    <table>
        <tr><th>Name</th><th>Total Spent</th></tr>
        {% for row in data %}
        <tr>
            <td>{{ user_map.get(row[0], "Unknown (" + row[0] + ")") }}</td>
            <td>${{ "%.6f"|format(row[1]) }}</td>
        </tr>
        {% endfor %}
    </table>
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
    data = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template_string(DASHBOARD_TEMPLATE, data=data, user_map=USER_MAP)

@app.route('/ask', methods=['POST'])
def ask():
    data = request.json
    id_code = data.get('id_code', '').strip()
    
    if id_code not in ALLOWED_IDS:
        return jsonify({"error": "Unauthorized ID"}), 403

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": data.get('prompt')}]
        )
        answer = res.choices[0].message.content
        cost = ((res.usage.prompt_tokens / 1000) * 0.00015) + ((res.usage.completion_tokens / 1000) * 0.0006)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET total_spent = total_spent + %s WHERE id_code = %s", (cost, id_code))
        conn.commit()
        cursor.execute("SELECT total_spent FROM users WHERE id_code = %s", (id_code,))
        new_total = cursor.fetchone()[0]
        cursor.close()
        conn.close()

        return jsonify({"answer": answer, "spent": new_total})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    app.run(debug=True)
