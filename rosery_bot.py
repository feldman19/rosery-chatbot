import os
import openai
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

openai.api_key = os.getenv("OPENAI_API_KEY")

# Set initial system role
chat_history = [
    {
        "role": "system",
        "content": "אתה עוזר שירות של Rosery. תענה בעברית מקצועית ותעזור ללקוחות להבין את תנאי המשלוחים, אחריות, החזרות, מבצעים וחומרים של התכשיטים.",
    }
]

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    message = data.get("message", "")

    if not message:
        return jsonify({"error": "Missing message"}), 400

    chat_history.append({"role": "user", "content": message})

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=chat_history
        )
        reply = response.choices[0].message.content
        chat_history.append({"role": "assistant", "content": reply})
        return jsonify({"response": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(debug=True, host="0.0.0.0", port=port)
