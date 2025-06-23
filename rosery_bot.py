import os
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

chat_history = [
    {
        "role": "system",
        "content": (
            "תענה בעברית. אתה נציג שירות של Rosery. "
            "פשוטה, מקצועית, עזור ללקוחות להבין את תנאי התשלומים, אחריות, החזירות, מבצעים וחומרים של התכשיטים. "
            "מנה את הידע העסקי מה{business_knowledge}"
        ),
    }
]

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    message = data.get("message", "")

    if not message:
        return jsonify({"error": "No message provided"}), 400

    chat_history.append({"role": "user", "content": message})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=chat_history
        )
        answer = response.choices[0].message.content
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    chat_history.append({"role": "assistant", "content": answer})

    return jsonify({"response": answer})

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(debug=True, host="0.0.0.0", port=port)
