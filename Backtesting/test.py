import requests
import json

# Configuration
base_url = "https://api.upstox.com/v2"
instrument_key = "NSE_EQ|INE075A01022" # "NSE_FO"
interval = "month"
from_date = "2023-11-18"  # Ensure this is earlier than `to_date`
to_date = "2024-11-17"

headers = {
    'Accept': 'application/json'
}

# Correctly construct the URL
url = f"{base_url}/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}"
print("URL:", url)

# Make the GET request
response = requests.get(url, headers=headers)

# Handle the response
if response.status_code == 200:
    output = response.json()  # Simplify JSON parsing
    print(json.dumps(output, indent=4))  # Pretty-print the JSON response
else:
    print(f"Error: {response.status_code}, {response.text}")
