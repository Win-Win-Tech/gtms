import requests
import pandas as pd

# Define the API endpoint
url = "http://147.93.27.224:8001/auth/login/"

# Define different payloads
payloads = [
    {"email": "user9@example.com",
     "password": "Password999"
    }
]

# Store results
results = []

# Loop through each payload and send POST request
for i, payload in enumerate(payloads, start=1):
    try:
        response = requests.post(url, json=payload)
        result = {
            "Payload #": i,
            "Request": str(payload),
            "Status Code": response.status_code,
            "Response": response.text
        }
    except Exception as e:
        result = {
            "Payload #": i,
            "Request": str(payload),
            "Status Code": "Error",
            "Response": str(e)
        }
    results.append(result)

# Convert results to DataFrame
df = pd.DataFrame(results)

# Write to Excel
excel_file = "api_responses.xlsx"
df.to_excel(excel_file, index=False)

print(f"Results written to {excel_file}")

