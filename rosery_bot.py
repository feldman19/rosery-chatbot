from flask import Flask, request, jsonify
from flask_cors import CORS
import openai
import os

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Allows cross-origin requests (needed for Shopify)

# Set OpenAI API key from environment variable
openai.api_key = os.getenv("OPENAI_API_KEY")

# Define the chat route
@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    message = data.get("message", "")

    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": message}],
        )
        reply = response.choices[0].message.content.strip()
        return jsonify({"reply": reply})
    except Exception as e:
        print("Error:", e)  # Show error in Render logs
        return jsonify({"reply": "הייתה בעיה. נסה שוב מאוחר יותר."})

# Run the app locally (not used in Render)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
