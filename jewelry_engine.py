import asyncio
import aiohttp
import os
import json
import random
import mimetypes
from datetime import datetime
from pathlib import Path

# --- CONFIG ---
FAL_AI_KEY = os.environ.get("FAL_AI_KEY", "aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b")
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
    # --- Earring-specific (6) ---
    "Single earring, clean white background, soft studio lighting, macro detail shot showing craftsmanship, professional jewelry product photography, 8K realistic",
    "Pair of earrings elegantly suspended on invisible thread, pure white background, crisp shadows, luxury brand catalog style, hyper-realistic detail",
    "Earring displayed on a minimalist white bust, natural daylight, clean and simple composition, high-end jewelry editorial, photorealistic",
    "Single earring lying flat on white silk, soft overhead lighting, extreme close-up on gemstone facets and metal polish, luxury product shot, 8K",
    "Pair of earrings arranged symmetrically on white marble, rim lighting creating subtle glow on edges, premium jewelry catalog, photorealistic",
    "Earring captured mid-air as if gently falling onto white surface, frozen motion, crisp focus, creative luxury editorial, hyper-realistic",

    # --- Ring-specific (6) ---
    "Ring standing upright on white surface, soft directional light creating gentle shadow, macro focus on metal texture and stone facets, clean product shot, 8K realistic",
    "Ring on pristine white marble, natural window light, elegant minimal composition, luxury jewelry catalog, photorealistic detail",
    "Ring floating slightly above a mirror surface, soft reflection below, clean studio lighting, high-end jewelry advertisement style, hyper-realistic",
    "Ring worn on a hand with fingers elegantly posed, white background, soft natural light, lifestyle luxury shot, photorealistic skin texture",
    "Ring placed inside a small white jewelry box with lid open, soft spotlight, unboxing luxury experience, 8K realistic detail",
    "Ring photographed from directly above on white surface, perfect symmetry, crisp shadow directly below, minimalist product photography, hyper-realistic",

    # --- Bracelet-specific (6) ---
    "Bracelet laid in a gentle curve on white silk, soft diffused lighting, clean elegant composition, luxury product photography, 8K realistic detail",
    "Bracelet clasped around a minimalist white display cylinder, studio lighting, crisp focus on links and texture, professional jewelry catalog, photorealistic",
    "Bracelet draped naturally on white surface, soft shadows, clean and simple presentation, high-end jewelry editorial, hyper-realistic",
    "Bracelet worn on a wrist with elegant pose, white background, natural daylight, lifestyle luxury photography, photorealistic skin and metal detail",
    "Bracelet arranged in a perfect circle on white marble, overhead shot, symmetrical composition, premium catalog style, 8K realistic",
    "Bracelet cascading in an S-curve on white velvet, soft side lighting highlighting each link, luxury product showcase, hyper-realistic",

    # --- Necklace-specific (6) ---
    "Necklace laid in an elegant arc on white velvet, soft studio lighting, macro detail on pendant and chain, luxury product photography, 8K realistic",
    "Necklace displayed on a minimalist white bust form, natural daylight, clean composition, high-end jewelry catalog, photorealistic detail",
    "Necklace flowing gracefully on pure white background, soft directional light, crisp focus on gemstone details, professional jewelry advertisement, hyper-realistic",
    "Necklace worn on a neck with elegant collarbone visible, white background, soft natural light, lifestyle luxury editorial, photorealistic",
    "Necklace arranged in a circular coil on white surface, pendant centered, overhead macro shot, premium jewelry catalog, 8K realistic",
    "Necklace draped over the edge of a white jewelry display stand, soft shadows, creative luxury composition, hyper-realistic detail",

    # --- General luxury presentation (6) ---
    "Jewelry piece on clean white background, professional studio lighting, sharp focus on metal and stone details, luxury product photography, 8K realistic",
    "Jewelry displayed on white marble surface, natural soft light, elegant minimal composition, high-end catalog style, photorealistic",
    "Jewelry floating on pure white with subtle reflection, clean commercial lighting, luxury brand advertisement look, hyper-realistic detail",
    "Jewelry piece with a single rose petal nearby on white surface, soft romantic lighting, Valentine's luxury collection style, photorealistic",
    "Jewelry photographed with a soft beam of light creating a subtle halo effect, white background, premium brand campaign, 8K realistic",
    "Jewelry on white surface with a delicate shadow pattern from window blinds, natural morning light, artistic luxury editorial, hyper-realistic",
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
