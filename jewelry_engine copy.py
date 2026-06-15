
import asyncio
import aiohttp
import os
import json
import random
from datetime import datetime
from pathlib import Path

# --- CONFIG ---
FAL_AI_KEY = "aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b"
GPT_EDIT_MODEL = "openai/gpt-image-2/edit"
GPT_EDIT_API = f"https://fal.run/{GPT_EDIT_MODEL}"

BASE_DIR = Path("/Users/goldenbot/.openclaw/workspace/agents/generator/bot_project")
INPUT_DIR = BASE_DIR / "jewelry_input"
OUTPUT_DIR = BASE_DIR / "jewelry_output"
STATE_FILE = BASE_DIR / "jewelry_state.json"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
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

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"active_batches": {}, "history": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

async def upload_to_fal_storage(session, file_path):
    """
    Uploads file to fal.ai CDN or returns base64 for models that support it.
    Returns URL if upload succeeds, or None if we need to use base64 fallback.
    """
    import base64
    
    # Read file content
    with open(file_path, "rb") as f:
        file_content = f.read()
    
    # Get file extension
    ext = file_path.suffix.lower().replace(".", "")
    mime = f"image/{ext}" if ext in ("jpg", "jpeg", "png", "webp") else "image/png"
    
    # Return base64 data URI (works with GPT Image 2)
    b64 = base64.b64encode(file_content).decode()
    return f"data:{mime};base64,{b64}"

async def edit_image(session, image_url, prompt):
    headers = {"Authorization": f"Key {FAL_AI_KEY}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "image_urls": [image_url]}
    try:
        async with session.post(GPT_EDIT_API, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"EDIT SUBMIT ERROR {resp.status}: {text[:300]}")
                return None
            data = await resp.json()
            req_id = data.get("request_id")
            if not req_id:
                print(f"EDIT NO REQUEST ID: {data}")
                return None
        
        status_url = f"https://fal.run/{GPT_EDIT_MODEL}/requests/{req_id}/status"
        for _ in range(60):
            await asyncio.sleep(2)
            async with session.get(status_url, headers=headers) as s_resp:
                s_data = await s_resp.json()
                status = s_data.get("status")
                if status == "COMPLETED":
                    print(f"DEBUG: Completed result = {s_data}")
                    # GPT Image 2 returns images at top level
                    top_images = s_data.get("images", [])
                    if top_images and isinstance(top_images, list) and top_images[0].get("url"):
                        return top_images[0]["url"]
                    # Also check result.images path
                    result = s_data.get("result", {})
                    if isinstance(result, dict):
                        images = result.get("images", [])
                        if images and isinstance(images, list) and images[0].get("url"):
                            return images[0]["url"]
                    print(f"EDIT NO URL IN RESULT: keys = {s_data.keys()}")
                    print(f"DEBUG: Full result = {s_data}")
                    return None
                if status in ("FAILED", "CANCELLED"):
                    print(f"EDIT STATUS: {status} - {s_data}")
                    return None
    except Exception as e:
        print(f"EDIT EXCEPTION: {e}")
    return None

async def download_image(session, url, path):
    # Create parent directories if they don't exist
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                path.write_bytes(await resp.read())
                return True
            print(f"DOWNLOAD STATUS ERROR: {resp.status}")
    except Exception as e:
        print(f"DOWNLOAD ERROR: {e}")
    return False

async def process_jewelry_request(request_type, params, callback_msg=None):
    """
    Main entry point for the bot logic.
    callback_msg: aiogram Message object for live progress updates
    """
    async def notify(text):
        if callback_msg:
            try:
                await callback_msg.answer(text)
            except:
                pass

    print(f"ENGINE: Starting {request_type} request with params: {params}")
    
    state = load_state()
    async with aiohttp.ClientSession() as session:
        if request_type == 'BATCH':
            paths = params.get('image_paths', [])
            num_to_pick = params.get('num_images', 1)
            styles_count = params.get('styles_per_image', 1)
            
            if not paths:
                await notify("Error: No image paths provided")
                return None

            actual_pick_count = min(num_to_pick, len(paths))
            picked_files = random.sample(paths, actual_pick_count)
            
            results = []
            for idx, file_path in enumerate(picked_files):
                await notify(f"Processing image {idx+1}/{actual_pick_count}...")
                img_url = await upload_to_fal_storage(session, Path(file_path))
                if not img_url:
                    await notify(f"Upload failed for {Path(file_path).name}")
                    continue
                
                for i in range(styles_count):
                    prompt = random.choice(JEWELRY_PROMPTS)
                    await notify(f"Generating style {i+1}/{styles_count}...")
                    res_url = await edit_image(session, img_url, prompt)
                    if res_url:
                        out_name = f"{Path(file_path).stem}_style{i+1}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        out_path = OUTPUT_DIR / out_name
                        if await download_image(session, res_url, out_path):
                            results.append({"file": out_name, "prompt": prompt})
            
            return results

        elif request_type == 'SINGLE':
            img_path_raw = params.get('image_path')
            prompt = params.get('prompt')
            
            if not img_path_raw:
                await notify("Error: No image path provided")
                return None
            
            path = Path(img_path_raw)
            if not path.exists():
                await notify(f"Error: File not found: {path}")
                return None

            await notify("Uploading to fal.ai...")
            img_url = await upload_to_fal_storage(session, path)
            if not img_url:
                await notify("Upload failed - check API key and network")
                return None
            
            await notify("AI is generating your professional jewelry render... (this takes 30-60s)")
            res_url = await edit_image(session, img_url, prompt)
            if res_url:
                out_name = f"{path.stem}_single_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                out_path = OUTPUT_DIR / out_name
                await notify("Downloading result...")
                if await download_image(session, res_url, out_path):
                    return {"file": out_name, "prompt": prompt}
            else:
                await notify("AI generation failed - model may be busy, try again")
    return None
