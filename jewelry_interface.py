
import json
from pathlib import Path

# This helper simulates the interaction logic for the bot's middleware/handler
# It manages the state and "Calculations" for Option A and B

class JewelrySessionManager:
    def __init__(self, state_path=None):
        if state_path is None:
            state_path = Path(__file__).parent / "jewelry_state.json"
        self.state_path = Path(state_path)
        self.sessions = {} # In-memory state for active user sessions

    def start_session(self, user_id):
        self.sessions[user_id] = {"step": "START", "uploaded_files": [], "config": {}}
        return self.sessions[user_id]

    def handle_option(self, user_id, option):
        session = self.sessions.get(user_id)
        if not session: return "Please start first."
        
        if option == "A":
            session["step"] = "AWAITING_UPLOAD"
            return "📦 Option A Selected: Please upload your batch of jewelry photos now!"
        elif option == "B":
            session["step"] = "AWAITING_SINGLE_UPLOAD"
            return "🖼️ Option B Selected: Please upload the single image you want to edit!"
        return "Invalid option."

    def handle_upload(self, user_id, files):
        session = self.sessions.get(user_id)
        if not session: return "No active session."
        
        session["uploaded_files"] = files
        if session["step"] == "AWAITING_UPLOAD":
            # Stay in upload mode — let user send more photos.
            # Transition to AWAITING_STYLES only when user sends a text message.
            return f"📸 Photo {len(files)} received. Send more or type 'done' when ready."
        elif session["step"] == "AWAITING_SINGLE_UPLOAD":
            session["step"] = "AWAITING_B_PROMPT"
            return "✅ Image received. Would you like me to use my ✨ AI Specialist prompts or ✍️ your own?"
        return "Unexpected upload."

    def handle_batch_config(self, user_id, styles_per_image, num_images):
        session = self.sessions.get(user_id)
        if not session: return "No active session."
        
        uploaded_count = len(session["uploaded_files"])
        # Logic check: if user wants 10 images but only uploaded 5
        actual_images = min(num_images, uploaded_count)
        total_gen = actual_images * styles_per_image
        
        session["config"] = {
            "styles_per_image": styles_per_image,
            "num_images": actual_images
        }
        session["step"] = "CONFIRMATION"
        
        msg = f"💎 **Batch Confirmation**\n\n"
        if num_images > uploaded_count:
            msg += f"⚠️ You requested {num_images} images, but only uploaded {uploaded_count}. I'll use all available.\n"
        
        msg += f"📸 Images to process: {actual_images}\n"
        msg += f"🎨 Styles per image: {styles_per_image}\n"
        msg += f"🚀 **Total images to generate: {total_gen}**\n\n"
        msg += "Ready to start? [Confirm & Generate]"
        
        return msg

    def handle_single_prompt(self, user_id, prompt, is_ai=True):
        session = self.sessions.get(user_id)
        if not session: return "No active session."
        
        session["config"]["prompt"] = prompt
        session["step"] = "READY_SINGLE"
        return f"✨ Prompt set: {prompt}\n\nReady to generate! [Confirm & Generate]"

# Example Usage for Bot Logic:
# mgr = JewelrySessionManager()
# mgr.start_session("user123")
# print(mgr.handle_option("user123", "A"))
# print(mgr.handle_upload("user123", ["img1.jpg", "img2.jpg", "img3.jpg"]))
# print(mgr.handle_batch_config("user123", 2, 5)) # User asked for 5 images but only uploaded 3
