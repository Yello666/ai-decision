import sys
import os
import requests
import json

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

API_URL = "http://localhost:8000/api/v1"
EMAIL = "test_merchant@example.com"
PASSWORD = "password123"
STORE_ID = "test-store-123"

def print_step(msg):
    print(f"\n{'='*20} {msg} {'='*20}")

def run_test():
    # 1. Register (We can't easily test OAuth flow automatically without mocking callbacks, 
    # so we will use the registration initiation just to check it doesn't crash, 
    # but for actual login we might need to insert a user directly if registration is blocked by OAuth)
    
    # However, to make this script self-contained, let's try to hit the Login endpoint.
    # If the user doesn't exist, we can't login.
    # Let's insert a user directly into the DB using the app code? 
    # Or better, let's create a temporary "dev-only" endpoint or just use SQL to insert?
    # I'll rely on the fact that I can't easily register via API without browser interaction.
    # So I will assume the server is running and I will use a direct DB insertion for the test user.
    
    # Wait, I can import the app code and use the DB session directly to create a user!
    try:
        from app.db.mysql import SessionLocal
        from app.models import Merchant
        from app.core.security import get_password_hash
        
        db = SessionLocal()
        user = db.query(Merchant).filter(Merchant.email == EMAIL).first()
        if not user:
            print_step("Creating Test User in DB")
            user = Merchant(
                email=EMAIL,
                password_hash=get_password_hash(PASSWORD),
                name="Test Merchant",
                shopify_store_id=STORE_ID,
                shopify_domain="test-store.myshopify.com",
                brand_tone="Professional",
                is_active=True
            )
            db.add(user)
            db.commit()
            print("User created.")
        else:
            print("Test user already exists.")
        db.close()
    except Exception as e:
        print(f"Failed to setup DB user: {e}")
        return

    # 2. Login
    print_step("Testing Login")
    resp = requests.post(f"{API_URL}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        return
    
    data = resp.json()["data"]
    token = data["access_token"]
    print(f"Got token: {token[:20]}...")
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Get Merchant Info
    print_step("Get Merchant Info")
    resp = requests.get(f"{API_URL}/merchant/info", headers=headers)
    print(json.dumps(resp.json(), indent=2))

    # 4. Set Brand Tone
    print_step("Set Brand Tone to 'Fun & Friendly'")
    resp = requests.post(
        f"{API_URL}/merchant/set-brand-tone", 
        headers=headers,
        json={"brand_tone": "Fun & Friendly"}
    )
    print(json.dumps(resp.json(), indent=2))

    # 5. AI Assessment
    print_step("AI Assessment")
    payload = {
        "hotspot": {
            "keyword": "Summer Vibes",
            "trend": "rising",
            "sentiment": "positive",
            "audience": "teens"
        },
        # shop info is optional, will be filled from DB
    }
    resp = requests.post(f"{API_URL}/hotspot/assess", headers=headers, json=payload)
    print(json.dumps(resp.json(), indent=2))

if __name__ == "__main__":
    run_test()
