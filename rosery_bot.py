from flask import Flask, request, jsonify
import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)

# This route handles chatbot messages
@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    message = data.get("message", "")

    if not message:
        return jsonify({"response": "לא הבנתי, תוכל לשאול שוב?"})

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "אתה שירות לקוחות של Rosery"},
            {"role": "user", "content": message}
        ]
    )

    answer = response.choices[0].message.content
    return jsonify({"response": answer})


# This runs the Flask app and uses the correct port for Render
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
