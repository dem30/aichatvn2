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
☁️ Deploy on Hugging Face Spaces | Triển khai trên Hugging Face
English
Create a new Space on Hugging Face (type: “Docker”).
Upload all project files (app.py, core.py, Dockerfile, etc.).
Configure environment variables (see Configuration below).
Hugging Face will auto-build and host the app.
Share your public AI chatbot instantly!
Vietnamese
Tạo Space mới trên Hugging Face (chọn loại “Docker”).
Tải toàn bộ file dự án (app.py, core.py, Dockerfile, …).
Cấu hình biến môi trường (xem phần Cấu hình bên dưới).
Hệ thống sẽ tự động build và chạy ứng dụng.
Chia sẻ chatbot AI công khai ngay lập tức!
🔧 Configuration | Cấu hình
To run AIChatVN 2, configure the following environment variables in config.py (local) or Hugging Face Spaces Secrets. See config.py.example for reference.
Để chạy AIChatVN 2, cấu hình các biến môi trường sau trong file config.py (local) hoặc Secrets trên Hugging Face Spaces. Xem config.py để tham khảo.
Environment Variables | Biến Môi Trường
Variable
Description
How to Obtain
ADMIN_BOT_PASSWORD
Password for bot admin access
Create a secure password (e.g., MyBotPass123!).
ADMIN_PASSWORD
Password for admin dashboard
Create a secure password (e.g., AdminPass456!).
ADMIN_USERNAME
Username for admin dashboard
Choose a username (e.g., aichatvn_admin).
FIRESTORE_CREDENTIALS
JSON credentials for Google Firestore
See instructions below.
GROQ_API_KEY
API key for Groq AI (xAI)
See instructions below.
SECRET_KEY
Secret key for app security
Generate a random string (e.g., python -c "import secrets; print(secrets.token_hex(32))").
Getting Firestore Credentials | Lấy JSON Firestore
Go to Google Cloud Console.
Create a project (e.g., aichatvn2-project).
Enable Firestore: Navigate to Firestore Database > Create Database (Native Mode).
Create a service account:
Go to IAM & Admin > Service Accounts > Create Service Account.
Grant role: Firestore Admin.
Generate key: Keys > Add Key > Create New Key > JSON.
Download the JSON file (e.g., aichatvn2-credentials.json).

Set FIRESTORE_CREDENTIALS to this string in config.py or Hugging Face Secrets.
Getting Groq API Key | Lấy Groq API Key
Go to xAI Developer Portal.
Sign up or log in with your account.
Navigate to API Keys > Create New API Key.
Copy the key (e.g., gsk_xxxxxxxxxxxxxxxx).
Set GROQ_API_KEY to this key in config.py or Hugging Face Secrets.
Setting Secrets in Hugging Face Spaces | Thiết Lập Secrets trên Hugging Face
Go to your Hugging Face Space (e.g., thaiquangvinh-dem302.hf.space).
Navigate to Settings > Repository Secrets.
Add each variable as a secret:
Name: ADMIN_BOT_PASSWORD, Value: (your password).
Name: ADMIN_PASSWORD, Value: (your password).
Name: ADMIN_USERNAME, Value: (your username).
Name: FIRESTORE_CREDENTIALS, Value: (Firestore JSON string).
Name: GROQ_API_KEY, Value: (Groq API key).
Name: SECRET_KEY, Value: (random string).
Save and redeploy the Space to apply changes.
Local Setup | Cấu Hình Local
Copy config.py.example to config.py.
Fill in the values for each variable.
Example config.py:
# config.py
ADMIN_BOT_PASSWORD = "MyBotPass123!"
ADMIN_PASSWORD = "AdminPass456!"
ADMIN_USERNAME = "aichatvn_admin"
FIRESTORE_CREDENTIALS = '{"type":"service_account",...}'
GROQ_API_KEY = "gsk_xxxxxxxxxxxxxxxx"
SECRET_KEY = "your_random_secret_key"
🧠 How It Works | Cách hoạt động
1️⃣ User sends message →
2️⃣ System searches QA dataset →
3️⃣ Sends best-matched question to AI (Groq/OpenAI) →
4️⃣ AI generates natural reply →
5️⃣ Realtime display on web chat
Perfect for FAQ bots, customer support, or teaching AI projects.
💰 Monetization | Kiếm Tiền
Want to monetize your fork of AIChatVN 2?
Donations: Set up your own GitHub Sponsors for funding.
Commercial Use: Sell as a custom chatbot or SaaS platform (see below).
Please keep the original attribution to AIChatVN Team to support our work!
Muốn kiếm tiền từ fork của AIChatVN 2?
Quyên góp: Sử dụng GitHub Sponsors cho tài khoản của bạn.
Thương mại: Bán dưới dạng chatbot tùy chỉnh hoặc nền tảng SaaS (xem dưới).
Vui lòng giữ ghi công cho AIChatVN Team để hỗ trợ công việc của chúng tôi!
🙌 Support Us | Hỗ Trợ Chúng Tôi
AIChatVN 2 is free and open source, but your support keeps us going!
🌟 Sponsor on GitHub: github.com/sponsors/dem30
📞 Contact for direct support: Zalo 0944121150
Your contributions help us improve features, fix bugs, and bring Vietnamese AI to the world!
AIChatVN 2 miễn phí và mã nguồn mở, nhưng sự hỗ trợ của bạn giúp chúng tôi tiếp tục!
🌟 Tài trợ trên GitHub: github.com/sponsors/dem30
📞 Liên hệ hỗ trợ trực tiếp: Zalo 0944121150
Đóng góp của bạn giúp cải thiện tính năng, sửa lỗi, và đưa AI Việt ra toàn cầu!
�
🤝 Contribute | Đóng Góp
We welcome contributions to make AIChatVN 2 better!
Report bugs or suggest features via GitHub Issues.
Join our community on Discord.
Submit pull requests with new features or fixes.
Hãy cùng cải thiện AIChatVN2!
Báo lỗi hoặc đề xuất tính năng qua GitHub Issues.
Tham gia cộng đồng trên Discord.
Gửi pull request với tính năng mới hoặc sửa lỗi.
💼 Commercial Use | Mục đích thương mại
You can:
Sell it as a custom chatbot solution for clients.
Integrate into corporate websites or training systems.
Monetize as an AI assistant SaaS platform.
Please include attribution to AIChatVN Team in your app/UI.
Bạn có thể:
Bán lại dưới dạng giải pháp chatbot tùy chỉnh.
Tích hợp vào website doanh nghiệp hoặc hệ thống nội bộ.
Phát triển thành nền tảng SaaS AI trợ lý thông minh.
Vui lòng ghi công AIChatVN Team trong ứng dụng/UI.
📜 Attribution | Ghi Công
If you use or fork AIChatVN 2, please give credit to the AIChatVN Team by:
Keeping the "Developed by AIChatVN Team" notice in your app/UI.
Mentioning us in your README or website (Contact: 0944121150 Zalo).
This helps us continue building awesome AI tools for the community!
Nếu bạn sử dụng hoặc fork AIChatVN 2, vui lòng ghi công AIChatVN Team bằng cách:
Giữ thông báo "Developed by AIChatVN Team" trong ứng dụng/UI.
Đề cập đến chúng tôi trong README hoặc website (Liên hệ: 0944121150 Zalo).
👨‍💻 Author | Tác giả
AIChatVN Team
Developed by Vietnamese developers passionate about open-source AI.
📍 Address: Nha Trang, Khánh Hòa, Vietnam (Postal Code: 65000)
📧 Contact: Zalo 0944121150
🪪 License | Giấy phép
MIT License – Free for commercial and personal use.
See LICENSE for details.
Giấy phép MIT – Miễn phí cho cá nhân và thương mại.
Xem LICENSE để biết thêm chi tiết.
🚀 AIChatVN 2 — Bring Vietnamese AI to the world!
🚀 AIChatVN 2 — Đưa trí tuệ nhân tạo Việt ra toàn cầu!
