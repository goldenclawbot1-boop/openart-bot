import asyncio
import logging
import aiohttp
import json
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- NEW JEWELRY IMPORTS ---
from jewelry_interface import JewelrySessionManager
from jewelry_engine import process_jewelry_request, AVAILABLE_MODELS, DEFAULT_MODEL

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
FAL_AI_KEY = os.environ["FAL_AI_KEY"]

# --- ACCESS CONTROL ---
# Whitelist: only these Telegram user IDs can use the bot.
# Add your own ID and any authorized users here.
ALLOWED_USERS = {
    8677244120,  # GoldenClaw (admin)
    # Add more authorized user IDs below:
    # 123456789,
    # 987654321,
}

# Admin IDs — can manage the whitelist
ADMIN_IDS = {8677244120}

def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Model endpoints
FLUX_MODEL = "fal-ai/flux/dev"
FLUX_API = f"https://fal.run/{FLUX_MODEL}"
GPT_EDIT_MODEL = "openai/gpt-image-2/edit"
GPT_EDIT_API = f"https://fal.run/{GPT_EDIT_MODEL}"

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot + Dispatcher
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Access Control Middleware ---
from aiogram import BaseMiddleware
from aiogram.types import Update

class AccessMiddleware(BaseMiddleware):
    """Block all non-whitelisted users. /start is allowed for everyone (shows rejection message)."""
    
    async def __call__(self, handler, event, data):
        # Extract user ID from event
        user_id = None
        if hasattr(event, 'from_user') and event.from_user:
            user_id = event.from_user.id
        elif hasattr(event, 'message') and event.message and event.message.from_user:
            user_id = event.message.from_user.id
        elif hasattr(event, 'callback_query') and event.callback_query and event.callback_query.from_user:
            user_id = event.callback_query.from_user.id
        
        if user_id is None:
            return  # Can't determine user — let it through
        
        if is_allowed(user_id):
            return await handler(event, data)
        
        # Blocked user — only allow /start to show rejection
        if hasattr(event, 'message') and event.message and event.message.text:
            text = event.message.text.strip().lower()
            if text == "/start":
                return await handler(event, data)
        
        # Silently ignore all other interactions from blocked users
        return

dp.update.middleware(AccessMiddleware())

# Paths — relative to project root, works on any machine
BASE_DIR = Path(__file__).parent
JEWELRY_INPUT_DIR = BASE_DIR / "jewelry_input"
JEWELRY_OUTPUT_DIR = BASE_DIR / "jewelry_output"
JEWELRY_STATE_FILE = BASE_DIR / "jewelry_state.json"

# State Managers
jewelry_mgr = JewelrySessionManager()

class EditState(StatesGroup):
    waiting_for_photo = State()
    waiting_for_edit_prompt = State()

# --- STYLES ---
STYLES = {
    "📸 photoreal": {"prefix": "A hyper-realistic, high-detail photograph of", "suffix": "8K resolution, professional photography"},
    "🎨 anime": {"prefix": "High-quality anime style art of", "suffix": "vibrant colors, studio anime quality"},
    "🖌️ digital": {"prefix": "A stunning digital illustration of", "suffix": "digital art, concept art, 4K"},
    "🎬 cinematic": {"prefix": "A cinematic movie still of", "suffix": "cinematic lighting, film grain"},
}
user_prompts = {}

def get_style_keyboard():
    builder = InlineKeyboardBuilder()
    for style_name in STYLES.keys():
        builder.button(text=style_name, callback_data=f"style_{style_name}")
    builder.adjust(2)
    return builder.as_markup()

def enhance_prompt(user_prompt, style_key):
    style = STYLES.get(style_key, STYLES["📸 photoreal"])
    return f"{style['prefix']} {user_prompt}, {style['suffix']}"

# --- fal.ai Helpers ---
async def call_fal_api(model, api_url, payload):
    headers = {"Authorization": f"Key {FAL_AI_KEY}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, headers=headers, json=payload) as resp:
                if resp.status != 200: 
                    text = await resp.text()
                    print(f"LOG: fal.ai submit error {resp.status}: {text}")
                    return None
                submit_data = await resp.json()
                request_id = submit_data.get("request_id")
                if not request_id: 
                    print(f"LOG: No request_id in response: {submit_data}")
                    return None
            status_url = f"https://fal.run/{model}/requests/{request_id}/status"
            for _ in range(60):
                await asyncio.sleep(2)
                async with session.get(status_url, headers=headers) as status_resp:
                    if status_resp.status != 200:
                        continue
                    status_data = await status_resp.json()
                    if status_data.get("status") == "COMPLETED":
                        result = status_data.get("result", {})
                        images = result.get("images", [])
                        if images: return images[0]["url"]
                        image = result.get("image", {})
                        if image: return image["url"]
                        print(f"LOG: Completed but no image URL found in {status_data}")
                        return None
                    if status_data.get("status") in ("FAILED", "CANCELLED"): 
                        print(f"LOG: fal.ai request status: {status_data.get('status')}")
                        return None
    except Exception as e:
        print(f"LOG: Exception in call_fal_api: {e}")
    return None

async def generate_flux_image(prompt):
    return await call_fal_api(FLUX_MODEL, FLUX_API, {"prompt": prompt, "image_size": "landscape_4_3"})

async def edit_image_gpt(photo_url, edit_prompt):
    return await call_fal_api(GPT_EDIT_MODEL, GPT_EDIT_API, {"prompt": edit_prompt, "image_urls": [photo_url]})

# --- Helpers ---

WELCOME_TEXT = (
    "🎨 **Welcome to the AI Art Studio!**\n\n"
    "🖼️ **Create:** `/draw <prompt>`\n"
    "✂️ **Edit:** `/edit`\n"
    "💎 **Jewelry Studio:** Choose an option below for professional renders!\n"
)

# Per-user model preference (defaults to "gpt")
user_model = {}

def get_welcome_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Option A: Batch Upload", callback_data="jewel_opt_a")
    builder.button(text="🖼️ Option B: Single Image", callback_data="jewel_opt_b")
    builder.button(text="📊 Check Status", callback_data="jewel_status")
    builder.button(text="🎲 Random Generate", callback_data="jewel_random")
    builder.button(text="⚙️ Switch Model", callback_data="jewel_model_menu")
    builder.adjust(2, 2, 1)
    return builder.as_markup()

async def show_welcome_menu(chat_id):
    """Re-show the welcome menu so user can easily generate more."""
    await bot.send_message(chat_id, WELCOME_TEXT, parse_mode="Markdown", reply_markup=get_welcome_keyboard())

# --- Handlers ---

# --- Model Selection ---
@dp.callback_query(F.data == "jewel_model_menu")
async def handle_model_menu(callback: CallbackQuery):
    """Show available models for user to choose."""
    user_id = callback.from_user.id
    current = user_model.get(user_id, DEFAULT_MODEL)
    
    builder = InlineKeyboardBuilder()
    for key, info in AVAILABLE_MODELS.items():
        active = " ✅" if key == current else ""
        builder.button(
            text=f"{info['emoji']} {info['name']}{active}",
            callback_data=f"jewel_model_{key}"
        )
    builder.adjust(1)
    
    current_info = AVAILABLE_MODELS[current]
    await callback.message.answer(
        f"⚙️ **Select AI Model**\n\n"
        f"Current: {current_info['emoji']} **{current_info['name']}** — {current_info['desc']}\n\n"
        f"Choose a model:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("jewel_model_"))
async def handle_model_select(callback: CallbackQuery):
    """Set user's preferred model."""
    user_id = callback.from_user.id
    model_key = callback.data.replace("jewel_model_", "")
    
    if model_key not in AVAILABLE_MODELS:
        await callback.answer("Unknown model.")
        return
    
    user_model[user_id] = model_key
    info = AVAILABLE_MODELS[model_key]
    
    await callback.message.answer(
        f"✅ Model switched to {info['emoji']} **{info['name']}** — {info['desc']}",
        parse_mode="Markdown"
    )
    await show_welcome_menu(callback.message.chat.id)
    await callback.answer()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    if not is_allowed(user_id):
        await message.answer(
            "🔒 **Access Restricted**\n\n"
            "This bot is private. You are not authorized to use it.\n\n"
            "If you believe this is a mistake, contact the bot owner.",
            parse_mode="Markdown"
        )
        return
    jewelry_mgr.start_session(user_id)
    await message.answer(WELCOME_TEXT, parse_mode="Markdown", reply_markup=get_welcome_keyboard())

# --- Admin Commands ---
@dp.message(Command("adduser"))
async def cmd_adduser(message: Message):
    """Admin: add a user to the whitelist. Usage: /adduser <user_id>"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        return  # Silently ignore non-admins
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Usage: `/adduser <user_id>`", parse_mode="Markdown")
        return
    try:
        new_id = int(parts[1])
    except ValueError:
        await message.answer("❌ Invalid user ID. Must be a number.")
        return
    
    if new_id in ALLOWED_USERS:
        await message.answer(f"ℹ️ User `{new_id}` is already whitelisted.")
        return
    
    ALLOWED_USERS.add(new_id)
    await message.answer(f"✅ User `{new_id}` added to whitelist. They can now use the bot.")

@dp.message(Command("removeuser"))
async def cmd_removeuser(message: Message):
    """Admin: remove a user from the whitelist. Usage: /removeuser <user_id>"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Usage: `/removeuser <user_id>`", parse_mode="Markdown")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("❌ Invalid user ID. Must be a number.")
        return
    
    if target_id in ADMIN_IDS:
        await message.answer("❌ Cannot remove an admin from the whitelist.")
        return
    
    if target_id not in ALLOWED_USERS:
        await message.answer(f"ℹ️ User `{target_id}` is not in the whitelist.")
        return
    
    ALLOWED_USERS.discard(target_id)
    await message.answer(f"✅ User `{target_id}` removed from whitelist.")

@dp.message(Command("listusers"))
async def cmd_listusers(message: Message):
    """Admin: list all whitelisted users."""
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    if not ALLOWED_USERS:
        await message.answer("📋 No users in whitelist.")
        return
    
    lines = []
    for uid in sorted(ALLOWED_USERS):
        tag = " 👑 admin" if uid in ADMIN_IDS else ""
        lines.append(f"  • `{uid}`{tag}")
    
    await message.answer(
        f"📋 **Whitelisted Users ({len(ALLOWED_USERS)}):**\n\n" + "\n".join(lines),
        parse_mode="Markdown"
    )

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    """Global cancel — works from any state. Resets session and shows welcome menu."""
    user_id = message.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if session:
        # Reset session completely
        session["step"] = "START"
        session["config"] = {}
        session["uploaded_files"] = []
    await message.answer("↩ **Cancelled.** Starting fresh.", parse_mode="Markdown", reply_markup=get_welcome_keyboard())

@dp.callback_query(F.data.startswith("jewel_opt_"))
async def handle_jewelry_option(callback: CallbackQuery):
    user_id = callback.from_user.id
    option = "A" if callback.data == "jewel_opt_a" else "B"
    response = jewelry_mgr.handle_option(user_id, option)
    await callback.message.answer(response)
    await callback.answer()

@dp.callback_query(F.data == "jewel_status")
async def handle_status_check(callback: CallbackQuery):
    input_files = list(JEWELRY_INPUT_DIR.glob("*.jpg")) + list(JEWELRY_INPUT_DIR.glob("*.png")) + list(JEWELRY_INPUT_DIR.glob("*.jpeg"))
    output_files = list(JEWELRY_OUTPUT_DIR.glob("*.png"))
    
    # Filter out .DS_Store
    input_count = len([f for f in input_files if f.name != ".DS_Store"])
    output_count = len(output_files)
    
    msg = (
        f"📊 **Folder Status**\n\n"
        f"📥 **Input** (`jewelry_input/`): **{input_count}** images waiting\n"
        f"📤 **Output** (`jewelry_output/`): **{output_count}** generated images\n"
    )
    await callback.message.answer(msg, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "jewel_random")
async def handle_random_generate(callback: CallbackQuery):
    user_id = callback.from_user.id
    jewelry_mgr.start_session(user_id)
    session = jewelry_mgr.sessions[user_id]
    
    # Scan input folder for available images
    input_files = [f for f in (list(JEWELRY_INPUT_DIR.glob("*.jpg")) + list(JEWELRY_INPUT_DIR.glob("*.png")) + list(JEWELRY_INPUT_DIR.glob("*.jpeg"))) if f.name != ".DS_Store"]
    
    if not input_files:
        await callback.message.answer("📥 **No images in input folder!** Upload some first via Option A or B.")
        await callback.answer()
        return
    
    # Store available files in session
    session["uploaded_files"] = [str(f) for f in input_files]
    session["step"] = "AWAITING_RANDOM_COUNT"
    
    await callback.message.answer(
        f"🎲 **Random Generate**\n\n"
        f"📥 {len(input_files)} images available in `jewelry_input/`\n\n"
        f"How many images should I randomly pick and generate?\n"
        f"Enter a number (max {len(input_files)}):",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "jewel_cancel")
async def handle_cancel(callback: CallbackQuery):
    """Cancel current decision and return to prompt-mode selection."""
    user_id = callback.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session:
        await callback.answer("No active session.")
        return
    
    files = session.get("uploaded_files", [])
    if not files:
        # No files — go back to welcome
        await callback.message.answer("↩ Cancelled. Let's start over.")
        await show_welcome_menu(callback.message.chat.id)
        await callback.answer()
        return
    
    # Reset to prompt selection
    session["step"] = "AWAITING_B_PROMPT"
    session["config"]["prompt"] = None
    session["config"]["prompt_mode"] = None
    session["config"]["user_prompts"] = []
    session["config"]["prompt_index"] = 0
    
    if len(files) > 1:
        # Multi-image: show 4 prompt-mode buttons
        builder = InlineKeyboardBuilder()
        builder.button(text="✨ AI Same Prompt", callback_data="jewel_prompt_ai_same")
        builder.button(text="✨ AI Diff Prompts", callback_data="jewel_prompt_ai_diff")
        builder.button(text="✍️ My Same Prompt", callback_data="jewel_prompt_user_same")
        builder.button(text="✍️ My Diff Prompts", callback_data="jewel_prompt_user_diff")
        builder.adjust(2)
        await callback.message.answer(
            "↩ **Cancelled.** Choose again:\\n\\n"
            "✨ **AI Same Prompt:** One luxury prompt applied to all images.\\n"
            "✨ **AI Diff Prompts:** Each image gets a unique random prompt.\\n"
            "✍️ **My Same Prompt:** You write one prompt for all images.\\n"
            "✍️ **My Diff Prompts:** You write a different prompt for each image.",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
    else:
        # Single image: show 2 buttons
        builder = InlineKeyboardBuilder()
        builder.button(text="✨ AI Specialist", callback_data="jewel_prompt_ai")
        builder.button(text="✍️ My Own Prompt", callback_data="jewel_prompt_user")
        await callback.message.answer(
            "↩ **Cancelled.** Choose again:\\n\\n"
            "✨ **AI Specialist:** I'll use my luxury jewelry library to make it look professional.\\n"
            "✍️ **My Own Prompt:** You tell me exactly how to change it.",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
    await callback.answer()

@dp.callback_query(F.data == "jewel_prompt_ai")
async def handle_ai_specialist(callback: CallbackQuery):
    user_id = callback.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session: return
    
    from jewelry_engine import JEWELRY_PROMPTS
    import random
    prompt = random.choice(JEWELRY_PROMPTS)
    
    session["config"]["prompt"] = prompt
    session["config"]["prompt_mode"] = "same"
    session["step"] = "CONFIRMATION"
    
    files = session.get("uploaded_files", [])
    count_msg = f"\n📸 Images to process: {len(files)}" if len(files) > 1 else ""
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Confirm & Process", callback_data="confirm_gen")
    builder.button(text="↩ Cancel", callback_data="jewel_cancel")
    builder.adjust(1)
    
    await callback.message.answer(
        f"✨ **AI Specialist — Same Prompt:**\n\n`{prompt}`{count_msg}\n\nReady to generate?", 
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "jewel_prompt_ai_same")
async def handle_ai_same(callback: CallbackQuery):
    """AI Specialist — Same prompt for all images"""
    user_id = callback.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session: return
    
    from jewelry_engine import JEWELRY_PROMPTS
    import random
    prompt = random.choice(JEWELRY_PROMPTS)
    
    session["config"]["prompt"] = prompt
    session["config"]["prompt_mode"] = "same"
    session["step"] = "CONFIRMATION"
    
    files = session.get("uploaded_files", [])
    num_images = session["config"].get("num_images", len(files))
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Confirm & Process", callback_data="confirm_gen")
    builder.button(text="↩ Cancel", callback_data="jewel_cancel")
    builder.adjust(1)
    
    await callback.message.answer(
        f"✨ **AI Specialist — Same Prompt for All:**\n\n`{prompt}`\n\n📸 {num_images} images will use this prompt.\nReady to generate?", 
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "jewel_prompt_ai_diff")
async def handle_ai_diff(callback: CallbackQuery):
    """AI Specialist — Different prompt per image"""
    user_id = callback.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session: return
    
    session["config"]["prompt_mode"] = "diff"
    session["config"]["prompt"] = None  # Engine will pick per image
    session["step"] = "CONFIRMATION"
    
    files = session.get("uploaded_files", [])
    num_images = session["config"].get("num_images", len(files))
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Confirm & Process", callback_data="confirm_gen")
    builder.button(text="↩ Cancel", callback_data="jewel_cancel")
    builder.adjust(1)
    
    await callback.message.answer(
        f"✨ **AI Specialist — Different Prompt per Image:**\n\n📸 Each of the {num_images} images will get a unique random prompt from the luxury jewelry library.\nReady to generate?", 
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "jewel_prompt_user_same")
async def handle_user_same(callback: CallbackQuery):
    """User prompt — Same for all images"""
    user_id = callback.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session: return
    
    session["config"]["prompt_mode"] = "same"
    session["step"] = "AWAITING_B_PROMPT_TEXT"
    
    files = session.get("uploaded_files", [])
    num_images = session["config"].get("num_images", len(files))
    
    await callback.message.answer(
        f"✍️ **Your Prompt — Same for All:**\n\nPlease type the exact prompt you want to use.\n📸 This prompt will be applied to all {num_images} images."
    )
    await callback.answer()

@dp.callback_query(F.data == "jewel_prompt_user_diff")
async def handle_user_diff(callback: CallbackQuery):
    """User prompt — Different per image (collect one by one)"""
    user_id = callback.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session: return
    
    session["config"]["prompt_mode"] = "diff"
    session["config"]["user_prompts"] = []
    session["config"]["prompt_index"] = 0
    
    files = session.get("uploaded_files", [])
    num_images = session["config"].get("num_images", len(files))
    session["config"]["num_images"] = num_images
    
    session["step"] = "AWAITING_B_PROMPT_TEXT_MULTI"
    
    await callback.message.answer(
        f"✍️ **Your Prompts — Different per Image:**\n\n📸 You'll type {num_images} prompts, one for each image.\n\n**Prompt 1/{num_images}:** Type your first prompt now."
    )
    await callback.answer()

@dp.callback_query(F.data == "jewel_prompt_user")
async def handle_user_prompt_request(callback: CallbackQuery):
    user_id = callback.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session: return
    
    await callback.message.answer("✍️ Please type the exact prompt you want to use for this image.")
    session["step"] = "AWAITING_B_PROMPT_TEXT"
    await callback.answer()

@dp.message(F.photo)
async def handle_all_photos(message: Message, state: FSMContext):
    user_id = message.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if session and session["step"] in ("AWAITING_UPLOAD", "AWAITING_SINGLE_UPLOAD"):
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"upload_{timestamp}_{photo.file_unique_id[:8]}.jpg"
        save_path = JEWELRY_INPUT_DIR / filename
        await bot.download_file(file_info.file_path, save_path)
        files = session.get("uploaded_files", [])
        files.append(str(save_path))
        session["uploaded_files"] = files
        response = jewelry_mgr.handle_upload(user_id, files)
        
        if session["step"] == "AWAITING_B_PROMPT":
            files = session.get("uploaded_files", [])
            if len(files) > 1:
                # Multi-image: show 4 prompt-mode buttons
                builder = InlineKeyboardBuilder()
                builder.button(text="✨ AI Same Prompt", callback_data="jewel_prompt_ai_same")
                builder.button(text="✨ AI Diff Prompts", callback_data="jewel_prompt_ai_diff")
                builder.button(text="✍️ My Same Prompt", callback_data="jewel_prompt_user_same")
                builder.button(text="✍️ My Diff Prompts", callback_data="jewel_prompt_user_diff")
                builder.adjust(2)
                builder.button(text="↩ Cancel", callback_data="jewel_cancel")
                builder.adjust(1)
                
                await message.answer(
                    f"{response}\n\n"
                    "Choose your creative direction for these pieces:\n\n"
                    "✨ **AI Same Prompt:** One luxury prompt applied to all images.\n"
                    "✨ **AI Diff Prompts:** Each image gets a unique random prompt.\n"
                    "✍️ **My Same Prompt:** You write one prompt for all images.\n"
                    "✍️ **My Diff Prompts:** You write a different prompt for each image.",
                    reply_markup=builder.as_markup(),
                    parse_mode="Markdown"
                )
                return
            
            # Single image: show 2 buttons
            builder = InlineKeyboardBuilder()
            builder.button(text="✨ AI Specialist", callback_data="jewel_prompt_ai")
            builder.button(text="✍️ My Own Prompt", callback_data="jewel_prompt_user")
            builder.adjust(2)
            builder.button(text="↩ Cancel", callback_data="jewel_cancel")
            builder.adjust(1)
            
            await message.answer(
                f"{response}\n\n"
                "Choose your creative direction for this piece:\n\n"
                "✨ **AI Specialist:** I'll use my luxury jewelry library to make it look professional.\n"
                "✍️ **My Own Prompt:** You tell me exactly how to change it.",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
            return
        
        await message.answer(response)
        return
    
    current_state = await state.get_state()
    if current_state == EditState.waiting_for_photo:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        photo_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        await state.update_data(photo_url=photo_url)
        await state.set_state(EditState.waiting_for_edit_prompt)
        await message.answer("📸 Photo received! Now describe what to change...")
        return

@dp.message()
async def handle_jewelry_text_inputs(message: Message):
    user_id = message.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session: return
    
    if session["step"] == "AWAITING_UPLOAD":
        text = message.text.strip().lower()
        if text in ("done", "ok", "ready", "go"):
            files = session.get("uploaded_files", [])
            if not files:
                await message.answer("❌ No photos received yet. Send at least one photo first.")
                return
            session["step"] = "AWAITING_B_PROMPT"
            if len(files) > 1:
                # Multi-image: show 4 prompt-mode buttons
                builder = InlineKeyboardBuilder()
                builder.button(text="✨ AI Same Prompt", callback_data="jewel_prompt_ai_same")
                builder.button(text="✨ AI Diff Prompts", callback_data="jewel_prompt_ai_diff")
                builder.button(text="✍️ My Same Prompt", callback_data="jewel_prompt_user_same")
                builder.button(text="✍️ My Diff Prompts", callback_data="jewel_prompt_user_diff")
                builder.adjust(2)
                builder.button(text="↩ Cancel", callback_data="jewel_cancel")
                builder.adjust(1)
                await message.answer(
                    f"✅ {len(files)} images received. Choose your creative direction for these pieces:\n\n"
                    "✨ **AI Same Prompt:** One luxury prompt applied to all images.\n"
                    "✨ **AI Diff Prompts:** Each image gets a unique random prompt.\n"
                    "✍️ **My Same Prompt:** You write one prompt for all images.\n"
                    "✍️ **My Diff Prompts:** You write a different prompt for each image.",
                    reply_markup=builder.as_markup(),
                    parse_mode="Markdown"
                )
                return
            
            # Single image: show 2 buttons
            builder = InlineKeyboardBuilder()
            builder.button(text="✨ AI Specialist", callback_data="jewel_prompt_ai")
            builder.button(text="✍️ My Own Prompt", callback_data="jewel_prompt_user")
            builder.adjust(2)
            builder.button(text="↩ Cancel", callback_data="jewel_cancel")
            builder.adjust(1)
            await message.answer(
                f"✅ {len(files)} images received. Would you like me to use my ✨ AI Specialist prompts or ✍️ your own?\n\n"
                "Choose your creative direction for this piece:\n\n"
                "✨ **AI Specialist:** I'll use my luxury jewelry library to make it look professional.\n"
                "✍️ **My Own Prompt:** You tell me exactly how to change it.",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
            return
        # Otherwise ignore — user is still uploading photos
        return

    if session["step"] == "AWAITING_RANDOM_COUNT":
        try:
            count = int(message.text)
            files = session.get("uploaded_files", [])
            max_count = len(files)
            if count < 1 or count > max_count:
                await message.answer(f"❌ Please enter a number between 1 and {max_count}.")
                return
            session["config"]["num_images"] = count
            session["config"]["styles_per_image"] = 1
            session["step"] = "AWAITING_B_PROMPT"
            if count > 1:
                # Multi-image: show 4 prompt-mode buttons
                builder = InlineKeyboardBuilder()
                builder.button(text="✨ AI Same Prompt", callback_data="jewel_prompt_ai_same")
                builder.button(text="✨ AI Diff Prompts", callback_data="jewel_prompt_ai_diff")
                builder.button(text="✍️ My Same Prompt", callback_data="jewel_prompt_user_same")
                builder.button(text="✍️ My Diff Prompts", callback_data="jewel_prompt_user_diff")
                builder.adjust(2)
                builder.button(text="↩ Cancel", callback_data="jewel_cancel")
                builder.adjust(1)
                await message.answer(
                    f"🎲 Will randomly pick **{count}** of **{max_count}** images.\n\n"
                    "Choose your creative direction for these pieces:\n\n"
                    "✨ **AI Same Prompt:** One luxury prompt applied to all images.\n"
                    "✨ **AI Diff Prompts:** Each image gets a unique random prompt.\n"
                    "✍️ **My Same Prompt:** You write one prompt for all images.\n"
                    "✍️ **My Diff Prompts:** You write a different prompt for each image.",
                    reply_markup=builder.as_markup(),
                    parse_mode="Markdown"
                )
                return
            
            # Single image: show 2 buttons
            builder = InlineKeyboardBuilder()
            builder.button(text="✨ AI Specialist", callback_data="jewel_prompt_ai")
            builder.button(text="✍️ My Own Prompt", callback_data="jewel_prompt_user")
            builder.adjust(2)
            builder.button(text="↩ Cancel", callback_data="jewel_cancel")
            builder.adjust(1)
            await message.answer(
                f"🎲 Will randomly pick **{count}** of **{max_count}** images.\n\n"
                "Choose your creative direction for this piece:\n\n"
                "✨ **AI Specialist:** I'll use my luxury jewelry library to make it look professional.\n"
                "✍️ **My Own Prompt:** You tell me exactly how to change it.",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
            return
        except:
            await message.answer("❌ Please enter a number.")
            return

    if session["step"] == "AWAITING_STYLES":
        try:
            styles = int(message.text)
            if styles not in (1, 2):
                await message.answer("❌ Please enter 1 or 2.")
                return
            await message.answer("📸 Now, how many images should I pick from your batch?")
            session["config"]["styles_per_image"] = styles
            session["step"] = "AWAITING_COUNT"
            return
        except: await message.answer("❌ Please enter a number.")

    if session["step"] == "AWAITING_COUNT":
        try:
            count = int(message.text)
            response = jewelry_mgr.handle_batch_config(user_id, session["config"]["styles_per_image"], count)
            builder = InlineKeyboardBuilder()
            builder.button(text="✅ Confirm & Process", callback_data="confirm_gen")
            builder.button(text="↩ Cancel", callback_data="jewel_cancel")
            builder.adjust(1)
            await message.answer(response, reply_markup=builder.as_markup())
            session["step"] = "CONFIRMATION"
            return
        except: await message.answer("❌ Please enter a number.")

    if session["step"] == "AWAITING_B_PROMPT_TEXT":
        text = message.text.strip().lower()
        if text in ("cancel", "/cancel"):
            # User wants to go back to prompt selection
            files = session.get("uploaded_files", [])
            session["step"] = "AWAITING_B_PROMPT"
            session["config"]["prompt"] = None
            session["config"]["prompt_mode"] = None
            if len(files) > 1:
                builder = InlineKeyboardBuilder()
                builder.button(text="✨ AI Same Prompt", callback_data="jewel_prompt_ai_same")
                builder.button(text="✨ AI Diff Prompts", callback_data="jewel_prompt_ai_diff")
                builder.button(text="✍️ My Same Prompt", callback_data="jewel_prompt_user_same")
                builder.button(text="✍️ My Diff Prompts", callback_data="jewel_prompt_user_diff")
                builder.adjust(2)
                builder.button(text="↩ Cancel", callback_data="jewel_cancel")
                builder.adjust(1)
                await message.answer(
                    "↩ **Cancelled.** Choose again:\\n\\n"
                    "✨ **AI Same Prompt:** One luxury prompt applied to all images.\\n"
                    "✨ **AI Diff Prompts:** Each image gets a unique random prompt.\\n"
                    "✍️ **My Same Prompt:** You write one prompt for all images.\\n"
                    "✍️ **My Diff Prompts:** You write a different prompt for each image.",
                    reply_markup=builder.as_markup(),
                    parse_mode="Markdown"
                )
            else:
                builder = InlineKeyboardBuilder()
                builder.button(text="✨ AI Specialist", callback_data="jewel_prompt_ai")
                builder.button(text="✍️ My Own Prompt", callback_data="jewel_prompt_user")
                builder.adjust(2)
                builder.button(text="↩ Cancel", callback_data="jewel_cancel")
                builder.adjust(1)
                await message.answer(
                    "↩ **Cancelled.** Choose again:\\n\\n"
                    "✨ **AI Specialist:** I'll use my luxury jewelry library to make it look professional.\\n"
                    "✍️ **My Own Prompt:** You tell me exactly how to change it.",
                    reply_markup=builder.as_markup(),
                    parse_mode="Markdown"
                )
            return
        prompt = message.text
        session["config"]["prompt"] = prompt
        session["step"] = "CONFIRMATION"
        
        files = session.get("uploaded_files", [])
        count_msg = f"\n📸 Images to process: {len(files)}" if len(files) > 1 else ""
        
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Confirm & Process", callback_data="confirm_gen")
        builder.button(text="↩ Cancel", callback_data="jewel_cancel")
        builder.adjust(1)
        
        await message.answer(
            f"✍️ **Your Prompt:**\n`{prompt}`{count_msg}\n\nReady to generate?", 
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
        return

    if session["step"] == "AWAITING_B_PROMPT_TEXT_MULTI":
        text = message.text.strip().lower()
        if text in ("cancel", "/cancel"):
            # User wants to go back to prompt selection
            files = session.get("uploaded_files", [])
            session["step"] = "AWAITING_B_PROMPT"
            session["config"]["prompt"] = None
            session["config"]["prompt_mode"] = None
            session["config"]["user_prompts"] = []
            session["config"]["prompt_index"] = 0
            builder = InlineKeyboardBuilder()
            builder.button(text="✨ AI Same Prompt", callback_data="jewel_prompt_ai_same")
            builder.button(text="✨ AI Diff Prompts", callback_data="jewel_prompt_ai_diff")
            builder.button(text="✍️ My Same Prompt", callback_data="jewel_prompt_user_same")
            builder.button(text="✍️ My Diff Prompts", callback_data="jewel_prompt_user_diff")
            builder.adjust(2)
            builder.button(text="↩ Cancel", callback_data="jewel_cancel")
            builder.adjust(1)
            await message.answer(
                "↩ **Cancelled.** Choose again:\\n\\n"
                "✨ **AI Same Prompt:** One luxury prompt applied to all images.\\n"
                "✨ **AI Diff Prompts:** Each image gets a unique random prompt.\\n"
                "✍️ **My Same Prompt:** You write one prompt for all images.\\n"
                "✍️ **My Diff Prompts:** You write a different prompt for each image.",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
            return
        prompt = message.text
        user_prompts = session["config"].get("user_prompts", [])
        user_prompts.append(prompt)
        session["config"]["user_prompts"] = user_prompts
        
        prompt_index = session["config"].get("prompt_index", 0) + 1
        session["config"]["prompt_index"] = prompt_index
        num_images = session["config"].get("num_images", 1)
        
        if prompt_index >= num_images:
            # All prompts collected
            session["step"] = "CONFIRMATION"
            builder = InlineKeyboardBuilder()
            builder.button(text="✅ Confirm & Process", callback_data="confirm_gen")
            builder.button(text="↩ Cancel", callback_data="jewel_cancel")
            builder.adjust(1)
            
            prompts_list = "\n".join([f"  {i+1}. `{p[:80]}{'...' if len(p)>80 else ''}`" for i, p in enumerate(user_prompts)])
            await message.answer(
                f"✍️ **Your Prompts — All Collected:**\n\n{prompts_list}\n\n📸 {num_images} images, each with its own prompt.\nReady to generate?",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
        else:
            # Ask for next prompt
            await message.answer(
                f"✍️ **Prompt {prompt_index+1}/{num_images}:** Type your next prompt now."
            )
        return

@dp.callback_query(F.data == "confirm_gen")
async def handle_confirm_generate(callback: CallbackQuery):
    user_id = callback.from_user.id
    session = jewelry_mgr.sessions.get(user_id)
    if not session or session["step"] != "CONFIRMATION": return
    
    # Acknowledge callback first to prevent timeout
    try:
        await callback.answer()
    except:
        pass
    
    await callback.message.answer("🚀 **Processing started!**")
    
    try:
        files = session.get("uploaded_files", [])
        if not files:
            await callback.message.answer("❌ No image found in session.")
            return
        
        prompt = session["config"].get("prompt", "professional jewelry style")
        prompt_mode = session["config"].get("prompt_mode", "same")
        user_prompts = session["config"].get("user_prompts", [])
        model_key = user_model.get(user_id, DEFAULT_MODEL)
        
        if len(files) > 1:
            # Batch: process images, respecting num_images if set (random generate)
            num_images = session["config"].get("num_images", len(files))
            params = {
                "image_paths": files,
                "num_images": num_images,
                "styles_per_image": 1,
                "prompt": prompt,
                "prompt_mode": prompt_mode,
                "user_prompts": user_prompts,
            }
            results = await process_jewelry_request('BATCH', params, callback_msg=callback.message, model_key=model_key)
            if not results:
                await callback.message.answer("❌ AI Engine returned no results.")
                return
            for res in results:
                await callback.message.answer_photo(photo=types.FSInputFile(JEWELRY_OUTPUT_DIR / res["file"]), caption=f"💎 {res['prompt']}")
        else:
            # Single image
            params = {
                "image_path": files[0],
                "prompt": prompt,
            }
            res = await process_jewelry_request('SINGLE', params, callback_msg=callback.message, model_key=model_key)
            if res: 
                await callback.message.answer_photo(photo=types.FSInputFile(JEWELRY_OUTPUT_DIR / res["file"]), caption=f"💎 {res['prompt']}")
            else: 
                await callback.message.answer("❌ AI Engine failed to generate the image.")
    except Exception as e: 
        import traceback
        print(f"CRITICAL ERROR: {traceback.format_exc()}")
        await callback.message.answer(f"❌ **Critical Error:**\n`{str(e)}`")
    finally:
        await show_welcome_menu(callback.message.chat.id)

@dp.message(Command("draw"))
async def cmd_draw(message: Message):
    prompt = message.text.replace("/draw", "").strip()
    if not prompt: return await message.answer("❌ Prompt missing!")
    user_prompts[message.chat.id] = prompt
    await message.answer("✨ **Choose style:**", reply_markup=get_style_keyboard())

@dp.callback_query(F.data.startswith("style_"))
async def handle_style(callback: CallbackQuery):
    style = callback.data.replace("style_", "")
    prompt = user_prompts.get(callback.message.chat.id)
    if not prompt: return
    await callback.answer("Generating...")
    url = await generate_flux_image(enhance_prompt(prompt, style))
    if url: await callback.message.answer_photo(photo=url, caption=f"🎨 {style}")
    else: await callback.message.answer("❌ Failed.")
    await show_welcome_menu(callback.message.chat.id)

@dp.message(Command("edit"))
async def cmd_edit(message: Message, state: FSMContext):
    await state.set_state(EditState.waiting_for_photo)
    await message.answer("✂️ Send the photo you want to edit.")

@dp.message(EditState.waiting_for_edit_prompt)
async def receive_edit_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    url = await edit_image_gpt(data["photo_url"], message.text)
    if url: await message.answer_photo(photo=url, caption="✅ Edited!")
    else: await message.answer("❌ Failed.")
    await state.clear()
    await show_welcome_menu(message.chat.id)

async def main():
    try: await dp.start_polling(bot)
    finally: await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
