import asyncio
import aiohttp
import os
import json
import random
import mimetypes
from datetime import datetime
from pathlib import Path

# --- CONFIG ---
FAL_AI_KEY = "aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b"
GPT_EDIT_MODEL = "openai/gpt-image-2/edit"
GPT_EDIT_API = f"https://fal.run/{GPT_EDIT_MODEL}"

FAL_REST_BASE = "https://rest.alpha.fal.ai"

# How long (seconds) to wait for a single fal.ai job before giving up.
# gpt-image-2/edit at "high" quality can comfortably take 1-3 minutes.
EDIT_TIMEOUT_SECONDS = 300
EDIT_POLL_INTERVAL = 2

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
    Upload a local file to fal.ai's CDN storage (two-step flow) and return a
    public URL. Falls back to a base64 data URI only if the CDN upload fails
    for some reason -- base64 works but is much slower / can push jobs past
    the polling timeout for larger images.
    """
    headers = {"Authorization": f"Key {FAL_AI_KEY}"}

    try:
        # Step 1: get a short-lived upload token + CDN base url
        async with session.post(
            f"{FAL_REST_BASE}/storage/auth/token",
            headers=headers,
            params={"storage_type": "fal-cdn-v3"},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"UPLOAD AUTH ERROR {resp.status}: {text[:300]}")
            else:
                auth_data = await resp.json()
                token = auth_data.get("token")
                base_url = auth_data.get("base_url")
                if token and base_url:
                    # Step 2: upload the file using the token
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
    except Exception as e:
        print(f"UPLOAD EXCEPTION: {e}")

    # --- Fallback: base64 data URI ---
    print("UPLOAD: falling back to base64 data URI")
    import base64
    file_content = file_path.read_bytes()
    ext = file_path.suffix.lower().replace(".", "")
    mime = f"image/{ext}" if ext in ("jpg", "jpeg", "png", "webp") else "image/png"
    b64 = base64.b64encode(file_content).decode()
    return f"data:{mime};base64,{b64}"

def _extract_image_url(data):
    """Try every shape fal.ai might use for the result payload."""
    if not isinstance(data, dict):
        return None
    # Top-level images / image
    top_images = data.get("images")
    if isinstance(top_images, list) and top_images and top_images[0].get("url"):
        return top_images[0]["url"]
    top_image = data.get("image")
    if isinstance(top_image, dict) and top_image.get("url"):
        return top_image["url"]
    # Nested under "result"
    result = data.get("result")
    if isinstance(result, dict):
        return _extract_image_url(result)
    return None

async def edit_image(session, image_url, prompt, notify=None):
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
        result_url = f"https://fal.run/{GPT_EDIT_MODEL}/requests/{req_id}"

        max_polls = EDIT_TIMEOUT_SECONDS // EDIT_POLL_INTERVAL
        for attempt in range(max_polls):
            await asyncio.sleep(EDIT_POLL_INTERVAL)

            async with session.get(status_url, headers=headers) as s_resp:
                if s_resp.status != 200:
                    text = await s_resp.text()
                    print(f"EDIT STATUS HTTP {s_resp.status} (attempt {attempt+1}): {text[:200]}")
                    continue
                try:
                    s_data = await s_resp.json()
                except Exception as e:
                    print(f"EDIT STATUS BAD JSON (attempt {attempt+1}): {e}")
                    continue

            status = s_data.get("status")

            # Periodic progress update so the user knows it's still working
            if notify and attempt > 0 and attempt % 10 == 0:
                await notify(f"Still generating... ({(attempt+1) * EDIT_POLL_INTERVAL}s elapsed)")

            if status == "COMPLETED":
                # Try to read the URL straight off the status response first
                url = _extract_image_url(s_data)
                if url:
                    return url

                # Otherwise fetch the result explicitly
                async with session.get(result_url, headers=headers) as r_resp:
                    if r_resp.status != 200:
                        text = await r_resp.text()
                        print(f"EDIT RESULT FETCH ERROR {r_resp.status}: {text[:300]}")
                        return None
                    try:
                        r_data = await r_resp.json()
                    except Exception as e:
                        print(f"EDIT RESULT BAD JSON: {e}")
                        return None

                url = _extract_image_url(r_data)
                if url:
                    return url

                print(f"EDIT NO URL IN RESULT. status keys={list(s_data.keys())}, result keys={list(r_data.keys())}")
                print(f"DEBUG status payload: {s_data}")
                print(f"DEBUG result payload: {r_data}")
                return None

            if status in ("FAILED", "CANCELLED", "ERROR"):
                print(f"EDIT STATUS: {status} - {s_data}")
                return None

        # Loop exhausted without COMPLETED/FAILED -- this is the silent
        # timeout case that previously just fell through to `return None`.
        print(f"EDIT TIMEOUT after {EDIT_TIMEOUT_SECONDS}s for request {req_id}")
        if notify:
            await notify(f"Still waiting after {EDIT_TIMEOUT_SECONDS}s -- fal.ai may finish shortly, but giving up for now.")
        return None

    except Exception as e:
        print(f"EDIT EXCEPTION: {e}")
    return None

async def download_image(session, url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"DOWNLOAD: Attempting to download from {url}")
    print(f"DOWNLOAD: Target path = {path}")
    try:
        async with session.get(url) as resp:
            print(f"DOWNLOAD: Response status = {resp.status}")
            if resp.status == 200:
                content = await resp.read()
                print(f"DOWNLOAD: Got {len(content)} bytes")
                path.write_bytes(content)
                print(f"DOWNLOAD: Saved to {path}")
                return True
            print(f"DOWNLOAD STATUS ERROR: {resp.status}")
            print(f"DOWNLOAD RESPONSE HEADERS: {resp.headers}")
    except Exception as e:
        print(f"DOWNLOAD ERROR: {e}")
        import traceback
        print(f"DOWNLOAD TRACEBACK: {traceback.format_exc()}")
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
                    res_url = await edit_image(session, img_url, prompt, notify=notify)
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

            await notify("AI is generating your professional jewelry render... (this can take a couple of minutes)")
            res_url = await edit_image(session, img_url, prompt, notify=notify)
            if res_url:
                out_name = f"{path.stem}_single_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                out_path = OUTPUT_DIR / out_name
                await notify("Downloading result...")
                if await download_image(session, res_url, out_path):
                    return {"file": out_name, "prompt": prompt}
            else:
                await notify("AI generation failed - check the bot logs for details")
    return None
