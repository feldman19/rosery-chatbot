from flask import Flask, request, jsonify
import openai
import os

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_input = data.get("message", "")

    messages = [
        {
            "role": "system",
            "content": "אתה שירות לקוחות של Rosery. תענה בעברית. עזור ללקוחות להבין את תנאי המשלוחים, האחריות, ההחלפות, החזרות ומבצעים.",
        },
        {"role": "user", "content": user_input},
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=messages
        )
        answer = response.choices[0].message.content.strip()
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=10000)
