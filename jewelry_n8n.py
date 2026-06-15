"""
Jewelry Bot - n8n Integration Mode
Downloads images via n8n webhook and sends back to Telegram
"""

import asyncio
import aiohttp
import json
import os
import random
import base64
from pathlib import Path
from datetime import datetime

# --- CONFIG ---
N8N_WEBHOOK_URL = "http://localhost:5678/webhook/jewelry-generation"
TELEGRAM_TOKEN = "8971263763:AAG-SCWjZHC7AKLDrgHRMr8nmyic26LnnEw"
FAL_AI_KEY = "aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b"

BASE_DIR = Path("/Users/goldenbot/.openclaw/workspace/agents/generator/bot_project")
INPUT_DIR = BASE_DIR / "jewelry_input"
OUTPUT_DIR = BASE_DIR / "jewelry_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

JEWELRY_PROMPTS = [
    "Transform this jewelry into solid 18K yellow gold with brilliant diamond accents, professional product photography on white marble, luxury brand style",
    "Recast this piece in polished platinum with sapphire inlays, studio lighting, high-end jewelry catalog shot on dark velvet",
    "Render this as rose gold with morganite gemstones, soft romantic lighting, floating on a silk background, luxury editorial",
    "Convert to sterling silver with emerald details, crisp clean lighting, displayed on a minimalist white pedestal, modern luxury",
    "Transform into white gold with ruby accents, dramatic spotlight lighting, on black reflective surface, high-fashion jewelry ad",
    "Recast as brushed titanium with black diamond, industrial chic style, concrete background, contemporary designer jewelry",
    "Render in antique bronze with turquoise stones, warm vintage lighting, on weathered wood, artisanal handcrafted look",
    "Convert to palladium with aquamarine gems, cool ocean-toned lighting, on frosted glass, ethereal luxury aesthetic",
    "Keep the jewelry design but place it on a royal blue velvet display with golden rim lighting, museum-quality presentation",
    "Same jewelry, now floating elegantly above a mirror surface with soft reflections, luxury perfume-ad style lighting",
    "Display this piece on natural rough geode crystal, dramatic side lighting casting long shadows, high-end editorial",
    "Place on pristine white sand with gentle ocean blur in background, golden hour sunlight, resort luxury catalog",
    "Set against a dark moody background with a single beam of light hitting the jewelry, cinematic jewelry commercial",
    "Arrange on a bed of fresh rose petals, soft diffused lighting, romantic Valentine's luxury collection",
    "Display on a minimalist geometric concrete stand, architectural lighting, modern art gallery aesthetic",
    "Place on aged leather with vintage books nearby, warm library lighting, heritage luxury brand campaign",
]

# --- Local fal.ai upload (simpler, just for n8n) ---
async def upload_image_to_fal(file_path: Path) -> str:
    """Upload image to fal.ai using REST API and return URL for n8n to use."""
    import aiohttp
    import mimetypes
    
    headers = {"Authorization": f"Key {FAL_AI_KEY}"}
    FAL_REST_BASE = "https://rest.alpha.fal.ai"
    
    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: get upload token
            async with session.post(
                f"{FAL_REST_BASE}/storage/auth/token",
                headers=headers,
                params={"storage_type": "fal-cdn-v3"},
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"UPLOAD AUTH ERROR {resp.status}: {text[:300]}")
                    return None
                
                auth_data = await resp.json()
                token = auth_data.get("token")
                base_url = auth_data.get("base_url")
                if not token or not base_url:
                    print(f"UPLOAD MISSING AUTH DATA: {auth_data}")
                    return None
                
                # Step 2: upload the file
                content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
                file_content = file_path.read_bytes()

                form = aiohttp.FormData()
                form.add_field("file", file_content, filename=file_path.name, content_type=content_type)

                async with session.post(
                    f"{base_url}/files/upload",
                    headers={"Authorization": f"Bearer {token}"},
                    data=form,
                ) as up_resp:
                    if up_resp.status == 200:
                        up_data = await up_resp.json()
                        access_url = up_data.get("access_url")
                        if access_url:
                            print(f"UPLOAD OK (CDN): {access_url}")
                            return access_url
                        print(f"UPLOAD MISSING access_url: {up_data}")
                    else:
                        text = await up_resp.text()
                        print(f"UPLOAD FILE ERROR {up_resp.status}: {text[:300]}")
                        return None
    except Exception as e:
        print(f"UPLOAD EXCEPTION: {e}")
    
    return None

# --- Process via n8n ---
async def process_jewelry_request(request_type, params, callback_msg=None):
    """
    Process jewelry request via n8n webhook.
    Returns result dict with 'file' and 'prompt' or None on failure.
    """
    async def notify(text):
        if callback_msg:
            try:
                await callback_msg.answer(text)
            except:
                pass

    print(f"ENGINE: Starting {request_type} request with params: {params}")
    
    if request_type == 'SINGLE':
        img_path_raw = params.get('image_path')
        prompt = params.get('prompt', random.choice(JEWELRY_PROMPTS))
        
        if not img_path_raw:
            await notify("Error: No image path provided")
            return None
        
        path = Path(img_path_raw)
        if not path.exists():
            await notify(f"Error: File not found: {path}")
            return None

        await notify("🔄 Uploading image to fal.ai for processing...")
        fal_url = await upload_image_to_fal(path)
        if not fal_url:
            await notify("❌ Failed to upload to fal.ai")
            return None
        
        await notify(f"✅ Image uploaded. Sending to n8n for professional render...")
        
        # Build request to n8n
        n8n_payload = {
            "image_url": fal_url,
            "prompt": prompt,
            "input_filename": path.name,
            "output_dir": str(OUTPUT_DIR)
        }
        
        print(f"n8n payload: {n8n_payload}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(N8N_WEBHOOK_URL, json=n8n_payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        print(f"n8n response: {result}")
                        output_filename = result.get("output_filename")
                        if output_filename:
                            out_path = OUTPUT_DIR / output_filename
                            if out_path.exists():
                                await notify("✅ Download complete. Sending back to you...")
                                return {"file": output_filename, "prompt": prompt}
                    else:
                        text = await resp.text()
                        print(f"n8n error: {resp.status} - {text}")
                        await notify(f"❌ n8n error: {resp.status}")
        except asyncio.TimeoutError:
            print("n8n timeout")
            await notify("❌ n8n processing timeout")
        except Exception as e:
            print(f"n8n request error: {e}")
            await notify(f"❌ Error: {e}")
    
    return None

# --- Simple download helper for when n8n returns a URL ---
async def download_image(session, url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                path.write_bytes(await resp.read())
                return True
    except Exception as e:
        print(f"DOWNLOAD ERROR: {e}")
    return False
