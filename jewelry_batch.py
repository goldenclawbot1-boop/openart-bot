"""
Jewelry Batch Editor — Processes jewelry photos with GPT Image 2 Edit.
Keeps original shape, applies professional style variations.
Spreads work across days to avoid API overload.
"""

import asyncio
import aiohttp
import os
import json
import random
import time
from datetime import datetime
from pathlib import Path

# --- CONFIG ---
FAL_AI_KEY = "aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b"
GPT_EDIT_MODEL = "openai/gpt-image-2/edit"
GPT_EDIT_API = f"https://fal.run/{GPT_EDIT_MODEL}"

INPUT_DIR = Path("/Users/goldenbot/.openclaw/workspace/agents/generator/bot_project/jewelry_input")
OUTPUT_DIR = Path("/Users/goldenbot/.openclaw/workspace/agents/generator/bot_project/jewelry_output")
STATE_FILE = Path("/Users/goldenbot/.openclaw/workspace/agents/generator/bot_project/jewelry_state.json")

# --- PROFESSIONAL JEWELRY PROMPTS ---
# These are curated to keep the jewelry looking professional and on-brand.
# Each prompt preserves the original shape but changes style/materials/background.

JEWELRY_PROMPTS = [
    # Material variations
    "Transform this jewelry into solid 18K yellow gold with brilliant diamond accents, professional product photography on white marble, luxury brand style",
    "Recast this piece in polished platinum with sapphire inlays, studio lighting, high-end jewelry catalog shot on dark velvet",
    "Render this as rose gold with morganite gemstones, soft romantic lighting, floating on a silk background, luxury editorial",
    "Convert to sterling silver with emerald details, crisp clean lighting, displayed on a minimalist white pedestal, modern luxury",
    "Transform into white gold with ruby accents, dramatic spotlight lighting, on black reflective surface, high-fashion jewelry ad",
    "Recast as brushed titanium with black diamond, industrial chic style, concrete background, contemporary designer jewelry",
    "Render in antique bronze with turquoise stones, warm vintage lighting, on weathered wood, artisanal handcrafted look",
    "Convert to palladium with aquamarine gems, cool ocean-toned lighting, on frosted glass, ethereal luxury aesthetic",
    
    # Background & setting variations
    "Keep the jewelry design but place it on a royal blue velvet display with golden rim lighting, museum-quality presentation",
    "Same jewelry, now floating elegantly above a mirror surface with soft reflections, luxury perfume-ad style lighting",
    "Display this piece on natural rough geode crystal, dramatic side lighting casting long shadows, high-end editorial",
    "Place on pristine white sand with gentle ocean blur in background, golden hour sunlight, resort luxury catalog",
    "Set against a dark moody background with a single beam of light hitting the jewelry, cinematic jewelry commercial",
    "Arrange on a bed of fresh rose petals, soft diffused lighting, romantic Valentine's luxury collection",
    "Display on a minimalist geometric concrete stand, architectural lighting, modern art gallery aesthetic",
    "Place on aged leather with vintage books nearby, warm library lighting, heritage luxury brand campaign",
    
    # Style & mood variations
    "Transform into an Art Deco inspired version, geometric patterns, Great Gatsby era elegance, champagne lighting",
    "Reimagine as Victorian-era royal jewelry, ornate filigree details, candlelit ambiance, crown jewels aesthetic",
    "Convert to minimalist Scandinavian design, clean lines, natural daylight, IKEA-meets-luxury aesthetic",
    "Render as bohemian festival jewelry, layered textures, sun-drenched outdoor setting, Coachella luxury",
    "Transform into futuristic cyberpunk jewelry, neon reflections, dark tech aesthetic, Blade Runner inspired",
    "Reimagine as ancient Egyptian royal treasure, hieroglyphic details, golden desert light, museum artifact quality",
    "Convert to Japanese wabi-sabi aesthetic, imperfect beauty, natural materials, zen garden setting",
    "Render as Hollywood red carpet glamour, dazzling spotlight, paparazzi flash effect, celebrity jewelry moment",
    
    # Seasonal & occasion
    "Transform for a winter wonderland collection, frost details, icy blue lighting, snow-dusted display, holiday luxury",
    "Reimagine for a spring garden party, floral accents, soft pastel lighting, cherry blossom background, bridal luxury",
    "Convert to autumn harvest theme, warm amber tones, fallen leaves background, Thanksgiving luxury collection",
    "Render for a summer yacht party, nautical accents, bright Mediterranean sunlight, ocean backdrop, resort luxury",
    "Transform into a Valentine's Day special, heart motifs, romantic candlelight, rose gold dominance, love collection",
    "Reimagine as a New Year's Eve gala piece, champagne bubbles, fireworks bokeh, midnight blue elegance, celebration luxury",
    
    # Specific jewelry type enhancements
    "Enhance the gemstone brilliance, make every facet sparkle with maximum fire and scintillation, professional gemology lighting",
    "Add a delicate chain and transform into a pendant necklace, displayed on a mannequin neck, catalog photography",
    "Transform into a matching set by adding complementary earrings nearby, full collection display, luxury brand lookbook",
    "Render with extreme macro detail, every metal texture and gem inclusion visible, scientific jewelry photography",
    "Add an engraved personalization detail, artistic lettering on the metal, bespoke custom jewelry aesthetic",
    "Transform into an heirloom piece with subtle patina, passed-down-through-generations feel, antique jewelry box setting",
]

def load_state():
    """Load processing state from JSON file."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"processed": {}, "total": 0, "completed": 0, "last_index": 0}

def save_state(state):
    """Save processing state."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

async def upload_to_fal_storage(session: aiohttp.ClientSession, file_path: Path) -> str | None:
    """Upload a local image to fal.ai storage and get a URL."""
    headers = {"Authorization": f"Key {FAL_AI_KEY}"}
    
    try:
        with open(file_path, "rb") as f:
            file_content = f.read()
        
        # Try fal.ai file upload endpoint
        upload_url = "https://fal.run/fal-ai/fal-upload"
        form = aiohttp.FormData()
        form.add_field("file", file_content, filename=file_path.name)
        
        async with session.post(upload_url, headers=headers, data=form) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("url") or data.get("file_url")
            
            # Fallback: try base64 data URI
            import base64
            ext = file_path.suffix.lower().replace(".", "")
            mime = f"image/{ext}" if ext in ("jpg", "jpeg", "png", "webp") else "image/png"
            b64 = base64.b64encode(file_content).decode()
            return f"data:{mime};base64,{b64}"
            
    except Exception as e:
        print(f"Upload error for {file_path.name}: {e}")
        return None

async def edit_single_image(session: aiohttp.ClientSession, image_url: str, prompt: str) -> str | None:
    """Call GPT Image 2 Edit and return result URL."""
    headers = {
        "Authorization": f"Key {FAL_AI_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "image_urls": [image_url],
    }
    
    try:
        # Submit
        async with session.post(GPT_EDIT_API, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"Submit error: {resp.status} - {text[:200]}")
                return None
            data = await resp.json()
            request_id = data.get("request_id")
            if not request_id:
                print(f"No request_id: {data}")
                return None
        
        # Poll
        status_url = f"https://fal.run/{GPT_EDIT_MODEL}/requests/{request_id}/status"
        for attempt in range(60):  # Up to 2 minutes
            await asyncio.sleep(2)
            async with session.get(status_url, headers=headers) as status_resp:
                if status_resp.status != 200:
                    continue
                status_data = await status_resp.json()
                status = status_data.get("status")
                
                if status == "COMPLETED":
                    result = status_data.get("result", {})
                    images = result.get("images", [])
                    if images and images[0].get("url"):
                        return images[0]["url"]
                    image = result.get("image", {})
                    if image and image.get("url"):
                        return image["url"]
                    print(f"No image URL in result: {status_data}")
                    return None
                
                elif status in ("FAILED", "CANCELLED"):
                    print(f"Failed: {status_data}")
                    return None
        
        print(f"Timeout for request {request_id}")
        return None
        
    except Exception as e:
        print(f"API error: {e}")
        return None

async def download_image(session: aiohttp.ClientSession, url: str, save_path: Path):
    """Download result image to local folder."""
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                content = await resp.read()
                save_path.write_bytes(content)
                return True
    except Exception as e:
        print(f"Download error: {e}")
    return False

async def process_batch(max_per_run: int = 7):
    """Process a batch of jewelry images. Call this periodically."""
    state = load_state()
    
    # Find unprocessed images
    input_files = sorted([
        f for f in INPUT_DIR.iterdir()
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        and f.name not in state["processed"]
    ])
    
    if not input_files:
        print("✅ All images processed!")
        return
    
    state["total"] = len(input_files) + len(state["processed"])
    print(f"📸 {len(input_files)} images remaining. Processing up to {max_per_run} this batch...")
    
    async with aiohttp.ClientSession() as session:
        count = 0
        for file_path in input_files:
            if count >= max_per_run:
                break
            
            print(f"\n🔄 [{count+1}/{max_per_run}] Processing: {file_path.name}")
            
            # Upload to fal.ai
            image_url = await upload_to_fal_storage(session, file_path)
            if not image_url:
                print(f"  ❌ Failed to upload {file_path.name}")
                continue
            
            # Pick a random prompt
            prompt = random.choice(JEWELRY_PROMPTS)
            print(f"  📝 Prompt: {prompt[:80]}...")
            
            # Generate edit
            result_url = await edit_single_image(session, image_url, prompt)
            if not result_url:
                print(f"  ❌ Generation failed for {file_path.name}")
                continue
            
            # Save result
            base_name = file_path.stem
            output_name = f"{base_name}_edited_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            output_path = OUTPUT_DIR / output_name
            
            success = await download_image(session, result_url, output_path)
            if success:
                state["processed"][file_path.name] = {
                    "output": output_name,
                    "prompt": prompt,
                    "timestamp": datetime.now().isoformat(),
                }
                state["completed"] += 1
                print(f"  ✅ Saved: {output_name}")
            else:
                print(f"  ❌ Failed to download result")
            
            count += 1
            # Small delay between images to be nice to the API
            await asyncio.sleep(3)
    
    save_state(state)
    print(f"\n🎉 Batch complete! {state['completed']}/{state['total']} total done.")
    print(f"📁 Results in: {OUTPUT_DIR}")

if __name__ == "__main__":
    asyncio.run(process_batch())
