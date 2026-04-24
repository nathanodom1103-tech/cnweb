from flask import Flask, render_template_string, request, jsonify
from openai import OpenAI
import os
import psycopg2

app = Flask(__name__)

# --- CONFIGURATION ---
client = OpenAI(api_key=os.environ.get("api_key"))
MODEL_PRICING = {"gpt-4o-mini": {"input": 0.00015, "output": 0.0006}}

# Allowed user list
ALLOWED_NAMES = ["nathan", "001", "002", "003", "004", "005", "006", "007", "008", "009", "010"]

# --- SECURE DATABASE URL ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    if "?" in DATABASE_URL:
        DATABASE_URL += "&sslmode=require"
    else:
        DATABASE_URL += "?sslmode=require"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# --- DATABASE LOGIC ---
def init_db():
    if not DATABASE_URL:
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                name VARCHAR(50) PRIMARY KEY,
                total_spent FLOAT DEFAULT 0.0
            );
        """)
        for name in ALLOWED_NAMES:
            cursor.execute("""
                INSERT INTO users (name, total_spent) 
                VALUES (%s, 0.0) 
                ON CONFLICT (name) DO NOTHING;
            """, (name,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Database Init Error: {e}")

def get_user_spent(name):
    if not DATABASE_URL:
        return 0.0
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT total_spent FROM users WHERE name = %s;", (name,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result and result is not None else 0.0
    except Exception as e:
        print(f"Get Spent Error: {e}")
        return 0.0

def add_user_cost(name, cost):
    if not DATABASE_URL:
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users 
            SET total_spent = total_spent + %s 
            WHERE name = %s;
        """, (cost, name))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Add Cost Error: {e}")

def get_all_data():
    if not DATABASE_URL:
        return []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, total_spent FROM users ORDER BY total_spent DESC;")
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return results
    except Exception as e:
        print(f"Get All Data Error: {e}")
        return []

# Run DB seed
init_db()

# --- HTML TEMPLATES ---

CHAT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>N Tech AI</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; line-height: 1.6; }
        textarea { width: 100%; height: 100px; padding: 10px; border-radius: 5px; border: 1px solid #ccc; box-sizing: border-box; }
        input { width: 100%; padding: 10px; border-radius: 5px; border: 1px solid #ccc; box-sizing: border-box; }
        button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; }
        #response { background: #f4f4f4; padding: 15px; margin-top: 20px; border-radius: 5px; min-height: 50px; white-space: pre-wrap; }
        .stats { margin-top: 10px; font-weight: bold; color: #555; }
        .nav { text-align: right; margin-bottom: 20px; min-height: 24px; }
        .nav a { color: #007bff; text-decoration: none; font-weight: bold; display: none; }
    </style>
</head>
<body>
    <!-- Link is hidden by default using CSS display: none -->
    <div class="nav"><a href="/dashboard" id="adminLink">Admin Dashboard &rarr;</a></div>
    
    <h2>N Tech AI Version 1.4</h2>
    
    <label for="userName"><strong>Enter Your IDN:</strong></label><br>
    <!-- oninput triggers the checkName function on every keystroke -->
    <input type="text" id="userName" placeholder="IDN is given by nathan" oninput="checkName()"><br><br>
    
    <label for="userInput"><strong>Your Prompt:</strong></label><br>
    <textarea id="userInput" placeholder="What's on your mind?"></textarea><br><br>
    
    <button onclick="askAI()">Submit Request</button>

    <div id="response">Waiting for input...</div>
    <div class="stats">Usage: $<span id="totalDisplay">0.000000</span></div>

    <script>
        // Check if the user is typing "nathan"
        function checkName() {
            const nameInput = document.getElementById('userName').value.trim();
            const adminLink = document.getElementById('adminLink');
            
            if (nameInput === "nathan") {
                adminLink.style.display = "inline"; // Show the button
            } else {
                adminLink.style.display = "none";   // Hide the button
            }
        }

        async function askAI() {
            const name = document.getElementById('userName').value.trim();
            const prompt = document.getElementById('userInput').value;
            const resDiv = document.getElementById('response');
            
            if (!name || !prompt) {
                alert("Please fill in both fields.");
                return;
            }
            resDiv.innerText = "Thinking...";

            const response = await fetch('/ask', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, prompt: prompt})
            });

            const data = await response.json();
            if (data.error) {
                resDiv.innerText = "Error: " + data.error;
            } else {
                resDiv.innerText = data.answer;
                document.getElementById('totalDisplay').innerText = data.user_spent.toFixed(6);
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
    <title>Database Dashboard</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; line-height: 1.6; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
        th { background-color: #007bff; color: white; }
        tr:nth-child(even) { background-color: #f2f2f2; }
        .nav { margin-bottom: 20px; }
        .nav a { color: #007bff; text-decoration: none; font-weight: bold; }
    </style>
</head>
<body>
    <div class="nav"><a href="/">&larr; Back to Chat</a></div>
    <h2>User Spend Dashboard</h2>
    <p>Below is a live view of all tracked users sorted by the highest spenders.</p>
    
    <table>
        <thead>
            <tr>
                <th>User / ID</th>
                <th>Total Spent ($)</th>
            </tr>
        </thead>
        <tbody>
            {% for row in data %}
            <tr>
                <td>{{ row[0] }}</td>
                <td>${{ "%.6f"|format(row[1]) }}</td>
            </tr>
            {% endfor %}
        </tbody>
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
    db_data = get_all_data()
    return render_template_string(DASHBOARD_TEMPLATE, data=db_data)

@app.route('/ask', methods=['POST'])
def ask():
    data = request.json
    name = data.get('name', '').strip()
    user_prompt = data.get('prompt')

    if name not in ALLOWED_NAMES:
        return jsonify({"error": "Access denied. Name not recognized."}), 403

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        # FIX ADDED HERE: Added [0] after choices
        answer = response.choices[0].message.content
        
        usage = response.usage
        cost = ((usage.prompt_tokens / 1000) * MODEL_PRICING["gpt-4o-mini"]["input"]) + \
               ((usage.completion_tokens / 1000) * MODEL_PRICING["gpt-4o-mini"]["output"])
        
        add_user_cost(name, cost)
        user_spent = get_user_spent(name)

        return jsonify({
            "answer": answer,
            "user_spent": user_spent
        })
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
