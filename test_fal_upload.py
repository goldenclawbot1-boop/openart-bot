#!/usr/bin/env python3
import asyncio
import base64
import json
import aiohttp
import os

FAL_AI_KEY = "aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b"
GPT_EDIT_API = "https://fal.run/openai/gpt-image-2/edit"

async def test_fal_api():
    # Read a test image and upload to fal storage first
    image_path = "/Users/goldenbot/.openclaw/media/inbound/file_0---f9865ca5-decf-439e-968f-0117cff6154e.jpg"
    
    with open(image_path, "rb") as f:
        img_data = f.read()
    
    # First, upload to fal.ai storage
    async with aiohttp.ClientSession() as session:
        # Upload to fal storage
        files = {"file": ("image.jpg", img_data, "image/jpeg")}
        print("Uploading to fal storage...")
        async with session.post("https://fal.run/storage/upload", headers={"Authorization": f"Key {FAL_AI_KEY}"}, data=files) as resp:
            print(f"Upload status: {resp.status}")
            upload_text = await resp.text()
            print(f"Upload response: {upload_text[:1000]}")
            if resp.status == 200:
                upload_data = await resp.json()
                print(f"Upload data: {json.dumps(upload_data, indent=2)}")
                
                # If upload succeeded, get the URL
                if isinstance(upload_data, dict):
                    url = upload_data.get("url") or upload_data.get("file_url") or upload_data.get("id")
                    print(f"Uploaded URL: {url}")

if __name__ == "__main__":
    asyncio.run(test_fal_api())
