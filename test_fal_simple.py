#!/usr/bin/env python3
import asyncio
import aiohttp
import json
import os

FAL_AI_KEY = "aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b"
GPT_EDIT_API = "https://fal.run/openai/gpt-image-2/edit"

async def test_fal_api():
    # Read a test image and upload to fal storage first
    image_path = "/Users/goldenbot/.openclaw/workspace/agents/generator/bot_project/jewelry_input/test.jpg"
    
    # First, upload to fal.ai storage
    async with aiohttp.ClientSession() as session:
        # Upload to fal storage
        files = {"file": open(image_path, "rb")}
        print("Uploading to fal storage...")
        async with session.post("https://fal.run/storage/upload", headers={"Authorization": f"Key {FAL_AI_KEY}"}, data=files) as resp:
            print(f"Upload status: {resp.status}")
            upload_text = await resp.text()
            print(f"Upload response: {upload_text[:1000]}")

if __name__ == "__main__":
    asyncio.run(test_fal_api())
