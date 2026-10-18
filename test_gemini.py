
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("Error: GEMINI_API_KEY was not found in .env")
else:
    try:
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents="Reply with exactly: Gemini connection successful."
        )

        print("Gemini response:")
        print(response.text)

    except Exception as error:
        print("Gemini test failed.")
        print("Error:", error)
