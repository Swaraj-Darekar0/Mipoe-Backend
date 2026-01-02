import os
import jwt  # pip install pyjwt
from dotenv import load_dotenv

# 1. Load your .env file
load_dotenv()

# 2. Get the secret you configured
secret = os.getenv('JWT_SECRET_KEY')
print(f"DEBUG: Your loaded secret starts with: '{secret[:5]}...'")
print(f"DEBUG: Your loaded secret ends with:   '...{secret[-5:]}'")

# 3. The exact token you received from login (I copied it from your message)
token = "eyJhbGciOiJIUzI1NiIsImtpZCI6IkRUK3NRQURhd0Uwdm9RbkwiLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJodHRwczovL2Frd2VtZWZld2ZtenhlZ2lkZXNsLnN1cGFiYXNlLmNvL2F1dGgvdjEiLCJzdWIiOiI5OGU1MTg0Ni05Y2NkLTQ1MzktYjM1NS05OWI3M2FjOWE2YTAiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoxNzY1MzI2ODQwLCJpYXQiOjE3NjUzMTk2NDAsImVtYWlsIjoiZGFyZWthcjEzOEBnbWFpbC5jb20iLCJwaG9uZSI6IiIsImFwcF9tZXRhZGF0YSI6eyJwcm92aWRlciI6ImVtYWlsIiwicHJvdmlkZXJzIjpbImVtYWlsIl19LCJ1c2VyX21ldGFkYXRhIjp7ImVtYWlsIjoiZGFyZWthcjEzOEBnbWFpbC5jb20iLCJlbWFpbF92ZXJpZmllZCI6dHJ1ZSwicGhvbmVfdmVyaWZpZWQiOmZhbHNlLCJyb2xlIjoiY3JlYXRvciIsInN1YiI6Ijk4ZTUxODQ2LTljY2QtNDUzOS1iMzU1LTk5YjczYWM5YTZhMCIsInVzZXJuYW1lIjoid2FyYWoifSwicm9sZSI6ImF1dGhlbnRpY2F0ZWQiLCJhYWwiOiJhYWwxIiwiYW1yIjpbeyJtZXRob2QiOiJwYXNzd29yZCIsInRpbWVzdGFtcCI6MTc2NTMxOTY0MH1dLCJzZXNzaW9uX2lkIjoiNjgwODZmNmUtYzM4NC00ZWUxLWE1YTAtY2RlNjY2OTI0NGNiIiwiaXNfYW5vbnltb3VzIjpmYWxzZX0.CHegMFFKl_sbB6WenGXu6kYkyrumoviAUCYsUxJB3uc"

try:
    # 4. Attempt to verify using YOUR secret
    decoded = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})
    print("\n✅ SUCCESS! The secret matches.")
    print("Decoded payload:", decoded)
except jwt.InvalidSignatureError:
    print("\n❌ FAILURE: Signature verification failed.")
    print("REASON: The 'JWT_SECRET_KEY' in your .env file is WRONG.")
    print("It does not match the secret Supabase used to sign this token.")
except Exception as e:
    print(f"\n❌ ERROR: {str(e)}")