import openai
import sys
import io

# תומך בעברית במסך
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 🔑 מפתח ה-API שלך
import os
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# 📄 טוען את המידע העסקי מקובץ
with open("business_info.txt", "r", encoding="utf-8") as f:
    business_knowledge = f.read()

# 🧠 התחלת השיחה עם מידע על העסק
chat_history = [
    {
        "role": "system",
        "content": f"אתה נציג שירות של Rosery. תענה בעברית פשוטה, מקצועית, עזור ללקוחות להבין את תנאי המשלוחים, אחריות, החזרות, מבצעים וחומרים של התכשיטים. הנה המידע של העסק:\n\n{business_knowledge}"
    }
]

# 💬 צ׳אט רץ
while True:
    user_input = input("אתה: ")
    if user_input.lower() in ["exit", "quit", "יציאה"]:
        print("ביי 👋")
        break

    chat_history.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=chat_history
    )

    answer = response.choices[0].message.content

    # ✅ שמירת התשובה לקובץ
    with open("last_response.txt", "w", encoding="utf-8") as f:
        f.write(answer)
    print("Rosery Bot: ✅ התשובה נשמרה בקובץ last_response.txt")

    chat_history.append({"role": "assistant", "content": answer})
