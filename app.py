from flask import Flask, render_template_string, request, jsonify
from openai import OpenAI
import os

app = Flask(__name__)

# --- CONFIGURATION ---
# Replace with your actual key
client = OpenAI(api_key="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")

MODEL_PRICING = {"gpt-4o-mini": {"input": 0.00015, "output": 0.0006}}

# Global variable to track total spent (resets when server restarts)
total_spent = 0.0

# --- HTML TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>AI Flask Assistant</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; line-height: 1.6; }
        textarea { width: 100%; height: 100px; padding: 10px; border-radius: 5px; border: 1px solid #ccc; }
        button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; }
        #response { background: #f4f4f4; padding: 15px; margin-top: 20px; border-radius: 5px; min-height: 50px; }
        .stats { margin-top: 10px; font-weight: bold; color: #555; }
    </style>
</head>
<body>
    <h2>Ask the AI (Flask)</h2>
    <textarea id="userInput" placeholder="What is on your mind?"></textarea><br><br>
    <button onclick="askAI()">Submit Request</button>

    <div id="response">Waiting for input...</div>
    <div class="stats">Total Spent: $<span id="totalDisplay">0.000000</span></div>

    <script>
        async function askAI() {
            const prompt = document.getElementById('userInput').value;
            const resDiv = document.getElementById('response');
            if (!prompt) return;

            resDiv.innerText = "Thinking...";

            const response = await fetch('/ask', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({prompt: prompt})
            });

            const data = await response.json();
            if (data.error) {
                resDiv.innerText = "Error: " + data.error;
            } else {
                resDiv.innerText = data.answer;
                document.getElementById('totalDisplay').innerText = data.total_spent.toFixed(6);
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
    global total_spent
    data = request.json
    user_prompt = data.get('prompt')

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
        
        total_spent += cost

        return jsonify({
            "answer": answer,
            "total_spent": total_spent
        })
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
