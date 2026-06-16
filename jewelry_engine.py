import asyncio
import aiohttp
import os
import json
import random
import mimetypes
from datetime import datetime
from pathlib import Path

# --- CONFIG ---
FAL_AI_KEY = os.environ["FAL_AI_KEY"]
GPT_EDIT_MODEL = "openai/gpt-image-2/edit"
GPT_EDIT_API = f"https://fal.run/{GPT_EDIT_MODEL}"

FAL_REST_BASE = "https://rest.alpha.fal.ai"

# How long (seconds) to wait for a single fal.ai job before giving up.
# gpt-image-2/edit at "high" quality can comfortably take 1-3 minutes.
EDIT_TIMEOUT_SECONDS = 300
EDIT_POLL_INTERVAL = 2

BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "jewelry_input"
OUTPUT_DIR = BASE_DIR / "jewelry_output"
STATE_FILE = BASE_DIR / "jewelry_state.json"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('jewelry_engine')
file_handler = logging.FileHandler(BASE_DIR / 'jewelry_engine.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

def log_debug(msg):
    """Log to both console and file."""
    print(msg, flush=True)
    logger.debug(msg)

JEWELRY_PROMPTS = [
    # --- Studio Product Photography (4) ---
    "The jewelry piece placed on a smooth white marble surface, professional studio lighting with two soft-box lights from 45-degree angles, pure white background, ultra-sharp macro focus capturing all surface details, no color alteration, product photography style",
    "Jewelry displayed on a minimalist frosted glass pedestal, diffused overhead light with a subtle fill reflector, neutral gray gradient background, tack-sharp lens, colors and form exactly as original, commercial luxury brand style",
    "Flat-lay top-down view of the jewelry item on a brushed light-gray concrete surface, ring flash lighting for even illumination, high-resolution macro capture preserving every facet and texture, exact original color palette retained",
    "Jewelry suspended on a fine transparent acrylic stand against a deep charcoal background, dramatic rim lighting highlighting contours without altering metal tone or stone color, fine-art product photography",

    # --- Lifestyle / Worn (4) ---
    "A woman's elegant hand wearing the jewelry against a soft bokeh outdoor garden background, natural golden-hour sunlight, lifestyle fashion aesthetic, original jewelry color and form fully preserved, shallow depth of field",
    "Close-up of the jewelry worn at a refined wrist or neckline, muted linen fabric in the background, natural window light casting soft shadows, upscale lifestyle editorial mood, no color or shape modification to the piece",
    "Jewelry resting on a real marble vanity tray beside a single white orchid and soft linen cloth, warm ambient room light, luxury lifestyle still-life composition, all original material finishes maintained",
    "Jewelry being held gently in a model's palm, soft natural daylight from a side window, minimal depth of field isolating the piece, authentic skin tone and original jewelry color unmodified, aspirational lifestyle mood",

    # --- Lifestyle / Scene (3) ---
    "The jewelry displayed beside a cup of espresso and an open book on a neutral linen tablecloth, warm indoor ambient light, relaxed morning lifestyle vibe, original metal and stone colors preserved accurately",
    "Jewelry placed on top of a folded cashmere fabric in dusty rose, soft diffused studio-lifestyle hybrid lighting, high-end fashion mood, exact original shape and hue of the piece faithfully reproduced",
    "Jewelry as the hero element in a high-fashion editorial spread, the piece photographed on a model in dramatic side-lighting with deep shadows, Vogue-level production quality, original design and color not modified",

    # --- Editorial / Artistic (4) ---
    "Overhead editorial flat-lay with the jewelry at center, surrounded by luxury props — pressed botanicals, gold foil paper, silk ribbon — neutral palette background so the piece remains dominant, color and form unaltered",
    "The jewelry photographed against a textured aged-gold patterned backdrop, professional editorial lighting, cinematic color grade applied only to the environment, original jewelry material and tone left unchanged",
    "Jewelry placed on a raw stone slab with scattered rose petals, high-fashion editorial composition, dramatic top-lighting, moody atmosphere, colors and silhouette of the piece exactly as designed",
    "Close-up editorial portrait where the jewelry is featured near the collarbone or earlobe, soft rim lighting on skin, negative space in background for text, original jewelry geometry and color perfectly intact",

    # --- Macro / Detail (3) ---
    "Extreme macro shot of the jewelry showing micro-detail — every prong, grain, and facet — under a ring-flash and fiber optic side light combination, clinical precision, original color and shape not modified, white seamless background",
    "45-degree macro angle capturing depth of the jewelry construction — layers, settings, and textures — with razor-sharp focus throughout, studio light eliminating all shadows, faithful representation of exact original color",
    "Reflective detail shot with the jewelry tilted to catch a specular highlight across the surface, neutral dark slate background providing contrast, original tone and surface treatment unchanged, ultra-high resolution",
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
            json={},  # Send empty JSON body as expected by fal.ai API
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"UPLOAD AUTH ERROR {resp.status}: {text[:300]}", flush=True)
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
                                print(f"UPLOAD OK (CDN): {access_url}", flush=True)
                                # Check if URL ends with .bin but file is actually an image
                                # If so, fallback to base64 since GPT Image 2 can't process .bin URLs
                                if access_url.endswith(".bin") and content_type and content_type.startswith("image/"):
                                    print(f"WARNING: CDN URL ends with .bin for image, falling back to base64", flush=True)
                                    ext = file_path.suffix.lower().replace(".", "")
                                    import base64
                                    b64 = base64.b64encode(file_content).decode()
                                    return f"data:image/{ext};base64,{b64}"
                                return access_url
                            print(f"UPLOAD MISSING access_url: {up_data}", flush=True)
                        else:
                            text = await up_resp.text()
                            print(f"UPLOAD FILE ERROR {up_resp.status}: {text[:300]}", flush=True)
    except Exception as e:
        print(f"UPLOAD EXCEPTION: {e}", flush=True)

    # --- Fallback: base64 data URI ---
    print("UPLOAD: falling back to base64 data URI", flush=True)
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
    """
    Submit an edit job to fal.ai and poll for the result.
    Uses async mode (no sync_mode) because gpt-image-2/edit can take
    2-5 minutes and sync_mode=True will time out the HTTP connection.
    """
    headers = {"Authorization": f"Key {FAL_AI_KEY}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "image_urls": [image_url]}

    try:
        # Step 1: Submit the job (returns immediately with request_id)
        async with session.post(GPT_EDIT_API, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"EDIT SUBMIT ERROR {resp.status}: {text[:300]}")
                print(f"EDIT SUBMIT ERROR {resp.status}: {text[:300]}", flush=True)
                return None
            submit_data = await resp.json()

        request_id = submit_data.get("request_id")
        if not request_id:
            # If no request_id, fal.ai might have returned the result directly
            # (some models do this even without sync_mode=True)
            url = _extract_image_url(submit_data)
            if url:
                logger.info(f"EDIT COMPLETED (direct) - URL: {url[:80]}")
                print(f"EDIT COMPLETED (direct) - URL: {url[:80]}...", flush=True)
                return url
            logger.error(f"EDIT: No request_id in response: {json.dumps(submit_data)[:300]}")
            print(f"EDIT: No request_id in response: {json.dumps(submit_data)[:300]}", flush=True)
            return None

        logger.info(f"EDIT submitted: request_id={request_id}")
        print(f"EDIT submitted: request_id={request_id}", flush=True)

        # Step 2: Poll for completion
        # gpt-image-2/edit can take 2-5 minutes. Poll every 5s for up to 6 minutes.
        status_url = f"https://fal.run/{GPT_EDIT_MODEL}/requests/{request_id}/status"
        result_url = f"https://fal.run/{GPT_EDIT_MODEL}/requests/{request_id}"
        max_polls = 72  # 6 minutes at 5 second intervals
        poll_interval = 5

        for attempt in range(max_polls):
            await asyncio.sleep(poll_interval)

            async with session.get(status_url, headers=headers) as s_resp:
                if s_resp.status != 200:
                    text = await s_resp.text()
                    logger.warning(f"EDIT STATUS HTTP {s_resp.status} (attempt {attempt+1}): {text[:200]}")
                    print(f"EDIT STATUS HTTP {s_resp.status} (attempt {attempt+1}): {text[:200]}", flush=True)
                    continue
                try:
                    s_data = await s_resp.json()
                except Exception as e:
                    logger.warning(f"EDIT STATUS BAD JSON (attempt {attempt+1}): {e}")
                    print(f"EDIT STATUS BAD JSON (attempt {attempt+1}): {e}", flush=True)
                    continue

            status = s_data.get("status")
            elapsed = (attempt + 1) * poll_interval

            if notify and attempt > 0 and attempt % 6 == 0:
                await notify(f"⏳ Still generating... ({elapsed}s)")

            if status == "COMPLETED":
                logger.info(f"EDIT COMPLETED after {elapsed}s")
                print(f"EDIT COMPLETED after {elapsed}s", flush=True)

                # Try extracting URL from status response first
                url = _extract_image_url(s_data)
                if url:
                    logger.info(f"EDIT URL from status: {url[:80]}")
                    print(f"EDIT URL from status: {url[:80]}...", flush=True)
                    return url

                # Fallback: fetch result endpoint
                async with session.get(result_url, headers=headers) as r_resp:
                    if r_resp.status != 200:
                        text = await r_resp.text()
                        logger.error(f"EDIT RESULT FETCH ERROR {r_resp.status}: {text[:300]}")
                        print(f"EDIT RESULT FETCH ERROR {r_resp.status}: {text[:300]}", flush=True)
                        return None
                    try:
                        r_data = await r_resp.json()
                    except Exception as e:
                        logger.error(f"EDIT RESULT BAD JSON: {e}")
                        print(f"EDIT RESULT BAD JSON: {e}", flush=True)
                        return None

                url = _extract_image_url(r_data)
                if url:
                    logger.info(f"EDIT URL from result: {url[:80]}")
                    print(f"EDIT URL from result: {url[:80]}...", flush=True)
                    return url

                logger.error(f"EDIT: No URL found in COMPLETED response. "
                            f"status keys: {list(s_data.keys())}, "
                            f"result keys: {list(r_data.keys())}")
                print(f"EDIT: No URL found. status keys={list(s_data.keys())}, "
                      f"result keys={list(r_data.keys())}", flush=True)
                return None

            if status in ("FAILED", "CANCELLED", "ERROR"):
                logger.error(f"EDIT {status}: {json.dumps(s_data, indent=2)[:500]}")
                print(f"EDIT {status}: {json.dumps(s_data, indent=2)[:500]}", flush=True)
                if notify:
                    await notify(f"❌ Generation failed: {status}")
                return None

        # Poll loop exhausted
        logger.error(f"EDIT TIMEOUT after {max_polls * poll_interval}s for request {request_id}")
        print(f"EDIT TIMEOUT after {max_polls * poll_interval}s for request {request_id}", flush=True)
        if notify:
            await notify(f"⏰ Timed out after {max_polls * poll_interval}s — fal.ai may still finish. Try again.")
        return None

    except asyncio.TimeoutError:
        logger.error("EDIT: Connection timed out")
        print("EDIT: Connection timed out", flush=True)
        return None
    except Exception as e:
        logger.error(f"EDIT EXCEPTION: {e}", exc_info=True)
        print(f"EDIT EXCEPTION: {e}", flush=True)
        import traceback
        traceback.print_exc()
    return None

async def download_image(session, url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Most result URLs from fal.ai/GPT-Image 2 are public CDN URLs.
        # Adding the Authorization header to a public CDN request can sometimes cause 403/400 errors
        # if the CDN doesn't expect it or doesn't recognize the key.
        async with session.get(url) as resp:
            if resp.status == 200:
                path.write_bytes(await resp.read())
                print(f"DOWNLOAD OK: {url}", flush=True)
                return True
            
            print(f"DOWNLOAD PUBLIC STATUS ERROR: {resp.status}. Trying with auth...", flush=True)
            headers = {"Authorization": f"Key {FAL_AI_KEY}"}
            async with session.get(url, headers=headers) as resp_auth:
                if resp_auth.status == 200:
                    path.write_bytes(await resp_auth.read())
                    print(f"DOWNLOAD OK (with auth): {url}", flush=True)
                    return True
                print(f"DOWNLOAD AUTH STATUS ERROR: {resp_auth.status}", flush=True)

    except Exception as e:
        print(f"DOWNLOAD ERROR: {e}", flush=True)
    
    print(f"DOWNLOAD FAILED: {url}", flush=True)
    return False

async def process_jewelry_request(request_type, params, callback_msg=None):
    """
    Main entry point for the bot logic.
    callback_msg: aiogram Message object for live progress updates
    
    Returns dict with 'file' and 'prompt' for SINGLE, or list of dicts for BATCH.
    Also cleans up input files after successful generation to save space
    and prevent duplicate credit waste.
    """
    async def notify(text):
        if callback_msg:
            try:
                await callback_msg.answer(text)
            except:
                pass

    print(f"ENGINE: Starting {request_type} request with params: {params}", flush=True)

    state = load_state()
    async with aiohttp.ClientSession() as session:
        if request_type == 'BATCH':
            paths = params.get('image_paths', [])
            num_to_pick = params.get('num_images', 1)
            styles_count = params.get('styles_per_image', 1)
            user_prompt = params.get('prompt')  # Optional: user-specified prompt
            prompt_mode = params.get('prompt_mode', 'same')  # 'same' or 'diff'
            user_prompts = params.get('user_prompts', [])  # List of per-image prompts

            if not paths:
                await notify("Error: No image paths provided")
                return None

            actual_pick_count = min(num_to_pick, len(paths))
            picked_files = random.sample(paths, actual_pick_count)

            results = []
            processed_inputs = []  # Track which inputs were successfully processed
            
            for idx, file_path in enumerate(picked_files):
                path_obj = Path(file_path)
                if not path_obj.exists():
                    await notify(f"⚠️ Skipping missing file: {path_obj.name}")
                    continue
                await notify(f"Processing image {idx+1}/{actual_pick_count}...")
                img_url = await upload_to_fal_storage(session, path_obj)
                if not img_url:
                    await notify(f"Upload failed for {Path(file_path).name}")
                    continue

                file_success = False
                for i in range(styles_count):
                    # Determine prompt based on mode
                    if prompt_mode == 'diff':
                        if user_prompts and idx < len(user_prompts):
                            prompt = user_prompts[idx]  # User-provided per-image prompt
                        else:
                            prompt = random.choice(JEWELRY_PROMPTS)  # AI random per image
                    else:
                        # 'same' mode: use the single prompt for all
                        prompt = user_prompt if user_prompt else random.choice(JEWELRY_PROMPTS)
                    
                    await notify(f"Generating style {i+1}/{styles_count}...")
                    res_url = await edit_image(session, img_url, prompt, notify=notify)
                    if res_url:
                        out_name = f"{Path(file_path).stem}_style{i+1}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        out_path = OUTPUT_DIR / out_name
                        if await download_image(session, res_url, out_path):
                            results.append({"file": out_name, "prompt": prompt})
                            file_success = True
                
                # Delete input file after all styles processed successfully
                if file_success:
                    processed_inputs.append(file_path)
                    try:
                        Path(file_path).unlink(missing_ok=True)
                        print(f"CLEANUP: Deleted input {file_path}", flush=True)
                    except Exception as e:
                        print(f"CLEANUP: Failed to delete {file_path}: {e}", flush=True)

            # Clean up old output files (keep last 100)
            _cleanup_old_outputs(keep=100)
            
            if processed_inputs:
                await notify(f"✅ Done! {len(results)} images generated from {len(processed_inputs)} inputs. Input files cleaned up.")
            
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
                    # Delete input file after successful generation
                    try:
                        path.unlink(missing_ok=True)
                        print(f"CLEANUP: Deleted input {path}", flush=True)
                    except Exception as e:
                        print(f"CLEANUP: Failed to delete {path}: {e}", flush=True)
                    
                    # Clean up old output files
                    _cleanup_old_outputs(keep=100)
                    
                    await notify("✅ Done! Input file cleaned up.")
                    return {"file": out_name, "prompt": prompt}
            else:
                await notify("AI generation failed - check the bot logs for details")
    return None


def _cleanup_old_outputs(keep=100):
    """Remove oldest output files, keeping the most recent `keep` files."""
    try:
        files = sorted(OUTPUT_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
        if len(files) > keep:
            print(f"CLEANUP: Removed {len(files) - keep} old output files", flush=True)
    except Exception as e:
        print(f"CLEANUP: Output cleanup error: {e}", flush=True)
