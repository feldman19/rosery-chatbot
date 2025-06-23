from flask import Flask, request, jsonify
import openai
import os

app = Flask(__name__)

# Read OpenAI API key from environment variable
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    message = data.get("message", "")

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "אתה שירות הלקוחות של Rosery. תענה בעברית בלבד."},
                {"role": "user", "content": message}
            ]
        )
        answer = response.choices[0].message.content
        return jsonify({"response": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
  port = int(os.environ.get("PORT", 10000))
 app.run(debug=True, host="0.0.0.0", port=port)

