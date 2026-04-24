from flask import Flask, render_template_string, request, jsonify
from openai import OpenAI
import os
import psycopg2

app = Flask(__name__)

# --- CONFIGURATION ---
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL_PRICING = {"gpt-4o-mini": {"input": 0.00015, "output": 0.0006}}

# Allowed user list
ALLOWED_NAMES = ["nathan", "001", "002", "003", "004", "005", "006", "007", "008", "009", "010"]

# --- SECURE DATABASE URL ---
DATABASE_URL = os.environ.get("DATABASE_URL")
# Render requires 'sslmode=require' for external/internal connections sometimes
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    if "?" in DATABASE_URL:
        DATABASE_URL += "&sslmode=require"
    else:
        DATABASE_URL += "?sslmode=require"

def get_db_connection():
    """Helper to establish database connections cleanly."""
    return psycopg2.connect(DATABASE_URL)

# --- DATABASE LOGIC ---
def init_db():
    """Creates the users table and seeds authorized users."""
    if not DATABASE_URL:
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create table mapping names to cumulative cost
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                name VARCHAR(50) PRIMARY KEY,
                total_spent FLOAT DEFAULT 0.0
            );
        """)
        
        # Populate default users if not already in the database
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
    """Retrieves the total spent for a specific user."""
    if not DATABASE_URL:
        return 0.0
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT total_spent FROM users WHERE name = %s;", (name,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result and result[0] is not None else 0.0
    except Exception as e:
        print(f"Get Spent Error: {e}")
        return 0.0

def add_user_cost(name, cost):
    """Adds a newly incurred cost to the user's running total."""
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

# Initialize the database table when the server starts
init_db()

# --- HTML TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>AI Flask Assistant</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; line-height: 1.6; }
        textarea { width: 100%; height: 100px; padding: 10px; border-radius: 5px; border: 1px solid #ccc; width: 100%; box-sizing: border-box; }
        input { width: 100%; padding: 10px; border-radius: 5px; border: 1px solid #ccc; box-sizing: border-box; }
        button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; }
        #response { background: #f4f4f4; padding: 15px; margin-top: 20px; border-radius: 5px; min-height: 50px; white-space: pre-wrap; }
        .stats { margin-top: 10px; font-weight: bold; color: #555; }
    </style>
</head>
<body>
    <h2>AI Assistant (Flask + Postgres Auth)</h2>
    
    <label for="userName"><strong>Enter Your Name / ID:</strong></label><br>
    <input type="text" id="userName" placeholder="e.g., nathan or 001"><br><br>
    
    <label for="userInput"><strong>Your Prompt:</strong></label><br>
    <textarea id="userInput" placeholder="What is on your mind?"></textarea><br><br>
    
    <button onclick="askAI()">Submit Request</button>

    <div id="response">Waiting for input...</div>
    <div class="stats">Your Total Spent: $<span id="totalDisplay">0.000000</span></div>

    <script>
        async function askAI() {
            const name = document.getElementById('userName').value.trim();
            const prompt = document.getElementById('userInput').value;
            const resDiv = document.getElementById('response');
            
            if (!name) {
                alert("Please enter your name/ID.");
                return;
            }
            if (!prompt) {
                alert("Please enter a prompt.");
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

# --- ROUTES ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/ask', methods=['POST'])
def ask():
    data = request.json
    name = data.get('name', '').strip()
    user_prompt = data.get('prompt')

    # Deny request if user is not in the whitelist
    if name not in ALLOWED_NAMES:
        return jsonify({"error": "Access denied. Name not recognized in the database."}), 403

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": user_prompt}]
        )
        
        answer = response.choices[0].message.content
        
        # Calculate Cost
        usage = response.usage
        cost = ((usage.prompt_tokens / 1000) * MODEL_PRICING["gpt-4o-mini"]["input"]) + \
               ((usage.completion_tokens / 1000) * MODEL_PRICING["gpt-4o-mini"]["output"])
        
        # Update user cost in Postgres
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
