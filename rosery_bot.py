from flask import Flask, request, jsonify
import openai
import os

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json()
    user_message = data.get('message', '')

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "אתה שירות לקוחות של Rosery"},
            {"role": "user", "content": user_message}
        ]
    )

    answer = response.choices[0].message.content
    return jsonify({"answer": answer})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10000)
