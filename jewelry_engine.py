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

# Available image editing models
AVAILABLE_MODELS = {
    "gpt": {
        "id": "openai/gpt-image-2/edit",
        "name": "GPT Image 2",
        "emoji": "🧠",
        "desc": "OpenAI — fine-grained edits, best fidelity",
    },
    "nano": {
        "id": "fal-ai/nano-banana-2/edit",
        "name": "Nano Banana 2",
        "emoji": "🍌",
        "desc": "Google — fast generation, creative edits",
    },
}
DEFAULT_MODEL = "gpt"

def get_model_api(model_key: str) -> str:
    """Get the fal.run API URL for a model key."""
    model_id = AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS[DEFAULT_MODEL])["id"]
    return f"https://fal.run/{model_id}"

GPT_EDIT_MODEL = AVAILABLE_MODELS["gpt"]["id"]
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
    # --- Precision Studio (4) ---
    "The jewelry piece centered on a hand-polished obsidian stone slab, surface veining visible beneath the piece, a single focused beam of fiber-optic light entering from camera-left at 12 degrees, casting a razor-thin shadow that traces the object's exact silhouette, photographed at f/11 with tilt-shift lens correcting all perspective, zero color or shape modification to the jewelry",
    "Jewelry suspended mid-air by a near-invisible 0.1mm monofilament above a flawless water surface, captured at the precise millisecond its reflection is perfectly symmetrical below, studio strobe synchronized at 1/8000s freezing every droplet of ambient mist, ultra-sharp 100mm macro, original jewelry hue and geometry unaltered",
    "The piece displayed inside an open luxury watch box lined with cream Alcantara, the box resting on a raw slab of Calacatta marble with gold veining, soft window light diffused through 1.5-stop silk, color temperature locked at 5500K, camera positioned at 35-degree downward tilt, jewelry color and silhouette completely faithful to original",
    "Jewelry placed inside a hand-blown glass dome on a thin mirrored plinth, light entering only through the top of the dome creating a precise cone of illumination that isolates the piece from its environment, surrounding area falls to pure darkness, 8x10 large-format rendering quality, jewelry color and dimensions unmodified",

    # --- Editorial / Narrative (3) ---
    "Deconstructed high-jewelry still life: the main piece anchoring the center, surrounded by its raw material counterparts — uncut gemstones, rough metal ingots, jeweler's tools — all arranged on a velvet tray, single overhead beauty dish light, editorial narrative composition, exact original color and form of the jewelry preserved",
    "Extreme close-up of the jewelry against the décolletage of a model in a silk noir gown, Hasselblad medium-format rendering, a single Broncolor Para 88 light source at 45 degrees feathered across skin and piece simultaneously, bokeh background of blurred crystal chandeliers, jewelry original color and shape retained with forensic accuracy",
    "Jewelry worn at the wrist of a model submerged to elbow depth in a shallow tray of still mineral water with a single white gardenia floating nearby, camera shooting through water surface at 15-degree angle, Profoto ring light on axis, the jewelry's original metal tone and stone colors refracted but reference-matched in post to original, shape perfectly unaltered",

    # --- Material Contrast (2) ---
    "Flat-lay composition: jewelry placed at the intersection of two contrasting luxury materials — one half raw aged bronze, one half polished white Thassos marble — the material boundary running diagonally beneath the piece, straight-down camera at 1:1 ratio, twin polarized lights eliminating all glare, original jewelry tones and structure intact",
    "Jewelry resting inside a shallow ceramic bowl finished in matte biscuit-white, the bowl elevated on a stone riser inside an anechoic-white cove studio, seamless negative-fill black card on camera-right creating soft gradated shadow under the piece, 4x5 technical camera, zero modification to jewelry hue or silhouette",

    # --- Optical / Atmospheric (2) ---
    "The jewelry piece photographed through a pane of etched optical glass that creates concentric circle refractions in the negative space while leaving the centered jewelry in perfect crystalline focus, background a single flat tone of warm ash gray, technical composite photograph, jewelry color and shape not manipulated",
    "The piece lit exclusively by the natural luminescence of backlit Japanese shoji paper panels, color temperature 4200K, a precise snooted accent light at 3% power defining the highest specular point on the piece, deep cream background, long-exposure integration, original jewelry color and form reproduced with scientific fidelity",

    # --- Hands & Gesture (1) ---
    "Jewelry featured as sole focus in a hands-in-frame gesture shot — model's hands cupped open presenting the piece, skin in perfect neutral light, background a gradient from cool off-white to pure white, Phase One IQ4 150MP simulation quality, no cropping or distortion of jewelry, color and shape pixel-accurate to original",

    # --- Synthetic Ice Encasement (1) ---
    "The jewelry piece encased in a block of optically clear synthetic ice on a backlit translucent white acrylic table, light source beneath diffusing upward through ice, creating a three-dimensional glow around but not touching the piece, the jewelry itself rendered in perfect clarity with original color and exact contour unaffected by surrounding ice",

    # --- Cashmere Drape (1) ---
    "Jewelry displayed atop a folded swatch of double-faced cashmere in deep ivory, the textile draped naturally creating soft topographic folds radiating from the piece, overhead Chimera 4x6 softbox, second kicker light at 120 degrees behind subject, all fabric texture captured at micro-fiber level, jewelry color and profile unchanged",

    # --- Crepuscular-Ray Lighting (1) ---
    "Crepuscular-ray lighting setup: jewelry placed at the convergence point of five parallel beams of theatrical haze-diffused light entering from a slatted overhead grid, each beam 4cm wide, producing a systematic pattern of light and shadow across the plinth while the piece itself sits in the brightest intersection, jewelry color and shape unmodified",

    # --- Miniature Gallery (1) ---
    "Architectural sectional view: jewelry photographed in a purpose-built white foam-core environment scaled to look like a modernist gallery room — miniature spotlights, a tiny pedestal, negative-space walls — the piece the size of a sculpture inside this constructed world, shot from eye-level with the miniature room, jewelry's real colors and geometry faithfully retained",

    # --- Motion Freeze Portrait (1) ---
    "Jewelry captured in a slow-motion portrait freeze: model mid-turn, hair lifted by motion, gown fabric trailing, the jewelry piece pinned in absolute tack-sharp focus while all surrounding elements render at 1/15s motion blur, high-power strobe freezing only the piece, original jewelry color and shape reference-locked, cinematic luxury campaign aesthetic",

    # --- Convex Mirror Reflection (1) ---
    "The piece positioned at the focal point of a convex antique mirror surface, the reflection of a candlelit interior visible in the curved background while the jewelry itself is front-lit by a controlled LED panel, creating a contrast between warm ambient environment and cool precision product light, jewelry color and exact form unmodified",

    # --- Tungsten Fresnel Half-Light (1) ---
    "Jewelry illuminated by a single portable tungsten Fresnel spot at 3200K bounced off a 24-inch round gold-white reflector positioned at camera height, producing a warm wrap-around half-light that models the three-dimensional form of the piece without obscuring detail, background pure photographic black, jewelry's original color temperature anchored in post, shape intact",

    # --- Double-Exposure Composite (1) ---
    "Double-exposure composite concept: the jewelry piece's silhouette filled with an interior macro world — microscopic crystal lattice structures, magnified stone inclusions, metal grain patterns — the outer shape boundary razor-defined, background studio white, the composite reveals the jewelry's inner material world while preserving its exact original outline and authentic surface color at full opacity",
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

async def edit_image(session, image_url, prompt, model_key="gpt", notify=None):
    """
    Submit an edit job to fal.ai and poll for the result.
    Uses async mode (no sync_mode) because image editing can take
    2-5 minutes and sync_mode=True will time out the HTTP connection.
    
    model_key: "gpt" (GPT Image 2) or "nano" (Nano Banana 2)
    """
    api_url = get_model_api(model_key)
    headers = {"Authorization": f"Key {FAL_AI_KEY}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "image_urls": [image_url]}

    try:
        # Step 1: Submit the job (returns immediately with request_id)
        async with session.post(api_url, headers=headers, json=payload) as resp:
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

async def process_jewelry_request(request_type, params, callback_msg=None, model_key="gpt"):
    """
    Main entry point for the bot logic.
    callback_msg: aiogram Message object for live progress updates
    model_key: "gpt" (GPT Image 2) or "nano" (Nano Banana 2)
    
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
                    res_url = await edit_image(session, img_url, prompt, model_key=model_key, notify=notify)
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
            res_url = await edit_image(session, img_url, prompt, model_key=model_key, notify=notify)
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
