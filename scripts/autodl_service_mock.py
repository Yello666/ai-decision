from fastapi import FastAPI, Body
from pydantic import BaseModel
import uvicorn
import random

app = FastAPI()

class HotspotInput(BaseModel):
    keyword: str
    trend: str
    sentiment: str
    audience: str

class ShopInput(BaseModel):
    category: str
    brand_tone: str

class AssessmentRequest(BaseModel):
    hotspot: HotspotInput
    shop: ShopInput

@app.post("/predict")
def predict(payload: AssessmentRequest = Body(...)):
    print(f"Received prediction request: {payload}")
    
    # Simulate AI processing logic
    base_score = 70
    
    # Logic: if brand tone matches sentiment somewhat
    if payload.hotspot.sentiment == "positive" and "fun" in payload.shop.brand_tone.lower():
        base_score += 15
    elif payload.hotspot.sentiment == "negative" and "professional" in payload.shop.brand_tone.lower():
        base_score += 10
        
    final_score = min(98.5, base_score + random.uniform(-5, 5))
    
    return {
        "match_score": round(final_score, 1),
        "match_reason": f"The trend '{payload.hotspot.keyword}' ({payload.hotspot.trend}) resonates with your {payload.shop.brand_tone} brand tone.",
        "brand_fit": "High" if final_score > 80 else "Medium",
        "conversion_prediction": "High" if final_score > 85 else "Moderate",
        "content_suggestion": f"Create a {payload.shop.brand_tone} style post about {payload.hotspot.keyword}.",
        "best_timing": "Evenings (7-9 PM)",
        "products_to_promote": ["Product A", "Product B"]
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
