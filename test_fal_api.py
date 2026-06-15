#!/usr/bin/env python3
import asyncio
import base64
import json
import aiohttp
import os

FAL_AI_KEY = "aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b"
GPT_EDIT_API = "https://fal.run/openai/gpt-image-2/edit"

async def test_fal_api():
    # Read a test image and convert to base64
    image_path = "/Users/goldenbot/.openclaw/media/inbound/file_0---f9865ca5-decf-439e-968f-0117cff6154e.jpg"
    
    with open(image_path, "rb") as f:
        img_data = f.read()
        img_base64 = base64.b64encode(img_data).decode('utf-8')
    
    print(f"Image size: {len(img_data)} bytes")
    print(f"Base64 size: {len(img_base64)} chars")
    
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Key {FAL_AI_KEY}", "Content-Type": "application/json"}
        
        # Try with base64 data URI
        payload = {
            "prompt": "Display this piece on natural rough geode crystal, dramatic side lighting casting long shadows, high-end editorial",
            "image_data": f"data:image/jpeg;base64,{img_base64}"
        }
        
        print(f"Sending request...")
        async with session.post(GPT_EDIT_API, headers=headers, json=payload) as resp:
            print(f"Status: {resp.status}")
            text = await resp.text()
            print(f"Response: {text[:2000]}")
            
            if resp.status == 200:
                data = await resp.json()
                print(f"\nResponse keys: {list(data.keys())}")
                print(f"Full response:\n{json.dumps(data, indent=2)}")

if __name__ == "__main__":
    asyncio.run(test_fal_api())
