from flask import Flask, request, jsonify
import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_input = data.get("message", "")

    chat_history = [
        {
            "role": "system",
            "content": "תענה בעברית. אתה נציג שירות של Rosery. תענה ללקוחות בצורה פשוטה, מקצועית, ותעזור להם להבין את תנאי המשלוחים, אחריות, תכשיטים, מבצעים וחומרי התכשיטים. הנה הידע העסקי:\n\n{{business_knowledge}}"
        },
        {
            "role": "user",
            "content": user_input
        }
    ]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=chat_history
        )
        answer = response.choices[0].message.content
        return jsonify({"response": answer})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
