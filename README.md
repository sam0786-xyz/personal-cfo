# 💰 Personal CFO: AI Financial Guardian

An aggressive, highly analytical, and brutally sarcastic financial guardian designed to protect your runway. Powered by **Google Gemini 2.5 Flash**, **Google Cloud Firestore**, **FastAPI**, and **Astral `uv`**, this project is wired directly to an iPhone Action Button (via Apple Shortcuts) to evaluate your spending habits in real time.

---

## 🎨 Luxury Dashboard Interface

When you run the server, it hosts a beautiful glassmorphic dark-mode single-page dashboard at `http://localhost:8000`:

* **Dynamic Runway Tracker**: Massive INR runway balance display with premium count-up animations.
* **Trajectory Decay Curve**: Interactive responsive SVG chart visualization mapping your runway decay down to June 22nd.
* **Interactive Sandbox**: Pitch discretionary purchases directly from your web browser.
* **Harsh Audit Roast Feed**: Complete transaction logs showing timestamps, cost pills, and custom CFO avatar roast bubbles.

---

## ⚡ Core Features

1. **Brutal Spending Roasts**: Calculates precise net-worth decay percentages, assesses ROI, and delivers severe, customized sarcastic burns to halt unnecessary spending.
2. **Cloud Serverless Database**: Overhauled to use **Google Cloud Firestore** for ultra-reliable, cross-device real-time persistence of runway balances and audit feeds.
3. **Safety Tuning**: Refactored Gemini API safety categories (`HARM_CATEGORY_HARASSMENT`) to block only high risks, ensuring the guardian can deliver its character-building financial roasts.
4. **Immediate iPhone Action Button Call**: Triggers a fast POST endpoint (`/cfo-check`) that returns a structured vocal string for your phone to speak aloud instantly.
5. **Modern `uv` Architecture**: Fully managed environment utilizing Astral `uv` for 500ms dependency resolutions and virtual environment isolation.

---

## 🛠️ Technology Stack

* **Backend**: Python 3.13, [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), `google-genai` SDK, `google-cloud-firestore`
* **Frontend**: Semantic HTML5, Vanilla CSS3 (Custom HSL color variables, Glassmorphism backdrop-filters, `@keyframes` slide-ins), native ES6 JavaScript (No heavy frameworks, loads in milliseconds)
* **AI Model**: Google Gemini 2.5 Flash (via official Google AI Studio client)
* **Database**: Google Cloud Firestore (Serverless document database)
* **Environment**: Astral [`uv`](https://github.com/astral-sh/uv)

---

## 📁 Repository Structure

```
personal-cfo/
├── pyproject.toml              # Modern uv project manifest
├── uv.lock                     # Strict lockfile pinning dependencies
├── main.py                     # FastAPI controller, Firestore and Gemini clients
├── .env                        # Local API secrets (Git-ignored)
├── .gitignore                  # Git ignore rules including .venv & .env
└── static/                     # Premium Glassmorphic Web Client
    ├── index.html              # Clean semantic layout structure
    ├── style.css               # Space-dark HSL design system stylesheet
    └── app.js                  # SVG runway charts, fetch calls & count-up counters
```

---

## 🚀 Local Setup & Installation

### Prerequisite: Install Astral `uv`
If you do not have `uv` installed, run:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 1: Clone and Enter the Project
```bash
git clone https://github.com/sam0786-xyz/personal-cfo.git
cd personal-cfo
```

### Step 2: Configure Environment Variables
Create a `.env` file in the root of the project:
```env
GEMINI_API_KEY=your_paid_api_key_here
```

### Step 3: Google Cloud Firestore Setup
Ensure you have active Google Cloud credentials locally. If running locally, authenticate your machine via:
```bash
gcloud auth application-default login
```
*(The app expects a Firestore project configured. It will read and write transactions directly to the `cfo_state/wallet` document).*

### Step 4: Boot the Server
Thanks to `uv run`, you do not need to activate virtual environments manually. Simply run:
```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 📱 iPhone Action Button Integration

Map this server directly to your iPhone Action Button in under 2 minutes:

1. **Locate your Mac's Local IP** (e.g. `192.168.1.72`) by running `ipconfig getifaddr en0` in your terminal.
2. Open the **Apple Shortcuts** app on your iPhone and create a new Shortcut named **"Pitch to CFO"**.
3. Replicate the following layout:

| Shortcut Block | Details & Action Config |
| :--- | :--- |
| **Ask for Input** | Prompt: *"What do you want to buy?"* |
| **Get Contents of URL** | URL: `http://<YOUR_MAC_IP>:8000/cfo-check`<br>Method: **POST**<br>Headers: `Content-Type: application/json`<br>Request Body: **JSON**<br>Field: `expense_text` ➔ `Provided Input` |
| **Get Dictionary from** | Input: `Contents of URL` |
| **Get Value for Key** | Key: `message` in `Dictionary` |
| **Speak** | Input: `Dictionary Value` |

4. Go to **Settings > Action Button** on your phone, slide to **Shortcut**, and assign **"Pitch to CFO"**.
5. Press the physical Action Button, pitch your expense, and let your M4 Mac roar through your speakers!
