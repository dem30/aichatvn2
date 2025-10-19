
---
title: aichatvn2
emoji: 👁
colorFrom: red
colorTo: gray
sdk: docker
pinned: false
---

# AIChatVN 2 🚀  
### Multi-Language Smart Chat App | AI-Powered Conversational System  
*(Song ngữ: English / Vietnamese)*  

---

## 🌍 Overview | Giới thiệu

**AIChatVN 2** is a next-generation conversational AI system for **businesses** and **developers**.  
It combines **local database-driven Q&A**, **natural responses via Groq/OpenAI**, and **real-time chat UI**.  

Ideal for:
- **Custom knowledge assistants** (FAQ, internal data bots).  
- **Interactive AI at kiosks** for sales, support, or community.  
- **On-premise** or **cloud-based** deployment (Docker/Hugging Face).  

**AIChatVN 2** là hệ thống AI trò chuyện thông minh, kết hợp **dữ liệu nội bộ (QA Database)**, **AI ngôn ngữ tự nhiên (Groq/OpenAI)**, và **giao diện chat thời gian thực**.  

Phù hợp để:
- Xây dựng **trợ lý AI nội bộ** (FAQ, hỗ trợ khách hàng).  
- Tạo **AI tương tác tại kios** cho bán hàng, cộng đồng.  
- Triển khai **tại chỗ** hoặc **trên đám mây**.  

---

## ⚙️ Features | Tính năng

✅ Local + Cloud AI (Groq, OpenAI, etc.)  
✅ Async & Fast API backend (Python/Aiohttp)  
✅ Realtime WebSocket chat  
✅ SQLite-based QA dataset  
✅ Dockerized environment  
✅ Hugging Face Spaces compatible  
✅ Multi-language UI (Vietnamese + English)  

---

## 🐳 Deployment with Docker | Triển khai bằng Docker

### English

```bash
# 1️⃣ Clone the repo
git clone https://github.com/dem30/aichatvn2.git
cd aichatvn2

# 2️⃣ Build Docker image
docker build -t aichatvn2 .

# 3️⃣ Run container
docker run -d -p 7860:7860 aichatvn2

# 4️⃣ Access in browser
http://localhost:7860

Vietnamese

# 1️⃣ Tải dự án về
git clone https://github.com/dem30/aichatvn2.git
cd aichatvn2

# 2️⃣ Xây dựng image Docker
docker build -t aichatvn2 .

# 3️⃣ Chạy container
docker run -d -p 7860:7860 aichatvn2

# 4️⃣ Mở trình duyệt
http://localhost:7860


---

☁️ Deploy on Hugging Face Spaces | Triển khai trên Hugging Face

English

1. Create a new Space on Hugging Face (type: “Docker”).


2. Upload all project files (app.py, core.py, Dockerfile, etc.).


3. Configure environment variables (see Configuration below).


4. Hugging Face will auto-build and host the app.


5. Share your public AI chatbot instantly!



Vietnamese

1. Tạo Space mới trên Hugging Face (chọn loại “Docker”).


2. Tải toàn bộ file dự án (app.py, core.py, Dockerfile, …).


3. Cấu hình biến môi trường (xem phần Cấu hình bên dưới).


4. Hệ thống sẽ tự động build và chạy ứng dụng.


5. Chia sẻ chatbot AI công khai ngay lập tức!




---

🔧 Configuration | Cấu hình

To run AIChatVN 2, configure the following environment variables in config.py (local) or Hugging Face Spaces Secrets.
See config.py.example for reference.

Để chạy AIChatVN 2, cấu hình các biến môi trường sau trong config.py (local) hoặc Secrets trên Hugging Face Spaces.
Xem config.py.example để tham khảo.

Variable	Description	How to Obtain

ADMIN_BOT_PASSWORD	Password for bot admin access	Create a secure password
ADMIN_PASSWORD	Password for admin dashboard	Create a secure password
ADMIN_USERNAME	Username for admin dashboard	Choose a username
FIRESTORE_CREDENTIALS	JSON credentials for Google Firestore	See below
GROQ_API_KEY	API key for Groq AI (xAI)	See below
SECRET_KEY	Secret key for app security	Generate via Python secrets



---

🔐 Getting Firestore Credentials | Lấy JSON Firestore

1. Go to Google Cloud Console.


2. Create a project (e.g., aichatvn2-project).


3. Enable Firestore → Firestore Database → Create Database (Native Mode).


4. Create service account → Grant role: Firestore Admin.


5. Generate key → Add Key → Create New Key → JSON.


6. Download the JSON file (e.g., aichatvn2-credentials.json).



Set FIRESTORE_CREDENTIALS to this JSON string in config.py or Hugging Face Secrets.


---

🔑 Getting Groq API Key | Lấy Groq API Key

1. Go to xAI Developer Portal.


2. Sign up or log in.


3. Navigate to API Keys → Create New API Key.


4. Copy the key (e.g., gsk_xxxxxxxxxxxxxxxx).


5. Set GROQ_API_KEY in config.py or Hugging Face Secrets.




---

🧰 Setting Secrets in Hugging Face Spaces | Thiết Lập Secrets

1. Go to your Space (e.g., thaiquangvinh-dem302.hf.space).


2. Open Settings → Repository Secrets.


3. Add variables:



ADMIN_BOT_PASSWORD = your_bot_password
ADMIN_PASSWORD = your_admin_password
ADMIN_USERNAME = your_admin_username
FIRESTORE_CREDENTIALS = {Firestore JSON string}
GROQ_API_KEY = gsk_xxxxxxxxxxxxxxxx
SECRET_KEY = your_random_secret_key

4. Save and redeploy.




---

🖥️ Local Setup | Cấu hình Local

# config.py
ADMIN_BOT_PASSWORD = "MyBotPass123!"
ADMIN_PASSWORD = "AdminPass456!"
ADMIN_USERNAME = "aichatvn_admin"
FIRESTORE_CREDENTIALS = '{"type":"service_account",...}'
GROQ_API_KEY = "gsk_xxxxxxxxxxxxxxxx"
SECRET_KEY = "your_random_secret_key"


---

🧠 How It Works | Cách hoạt động

1️⃣ User sends message →
2️⃣ System searches QA dataset →
3️⃣ Sends best-matched question to AI (Groq/OpenAI) →
4️⃣ AI generates natural reply →
5️⃣ Realtime display on web chat.

Perfect for FAQ bots, customer support, or teaching AI projects.


---

💰 Monetization | Kiếm Tiền

English:

Donations: via GitHub Sponsors.

Commercial use: sell as chatbot/SaaS solution.

Keep attribution to AIChatVN Team.


Vietnamese:

Quyên góp qua GitHub Sponsors.

Bán như chatbot tùy chỉnh hoặc nền tảng SaaS.

Giữ ghi công cho AIChatVN Team.



---

🙌 Support Us | Hỗ Trợ Chúng Tôi

AIChatVN 2 is free and open source — your support keeps it alive!
🌟 Sponsor: github.com/sponsors/dem30
📞 Zalo: 0944121150

AIChatVN 2 miễn phí và mã nguồn mở — sự hỗ trợ của bạn giúp chúng tôi phát triển!


---

🤝 Contribute | Đóng Góp

We welcome contributions!

Report bugs via GitHub Issues.

Join our Discord community.

Submit Pull Requests.


Hãy cùng cải thiện AIChatVN 2!


---

💼 Commercial Use | Mục đích thương mại

You can:

Sell as chatbot solution.

Integrate into enterprise systems.

Monetize as SaaS AI assistant.


Please include attribution to AIChatVN Team.


---

📜 Attribution | Ghi Công

If you use or fork AIChatVN 2:

Keep the “Developed by AIChatVN Team” notice.

Mention us in README or website.
📞 Contact: 0944121150 (Zalo)



---

👨‍💻 Author | Tác giả

AIChatVN Team
Developed by Vietnamese developers passionate about open-source AI.

📍 Nha Trang, Khánh Hòa, Vietnam (65000)
📧 Zalo: 0944121150


---

🪪 License | Giấy phép

MIT License – Free for commercial and personal use.
See LICENSE for details.

Giấy phép MIT – Miễn phí cho cá nhân và thương mại.


---

🚀 AIChatVN 2 — Bring Vietnamese AI to the world!

🚀 AIChatVN 2 — Đưa trí tuệ nhân tạo Việt ra toàn cầu!

---

