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


**Watch a quick demo | Xem demo nhanh**  
[Check out our TikTok demo](https://vt.tiktok.com/ZSUbBNwKQ/)  
See how to chat with Grok, manage Q&A, and import JSON files!  

**Try it live | Trải nghiệm trực tiếp**  
Visit our [Hugging Face Demo](https://thaiquangvinh-dem302.hf.space/) with these credentials:  
- **Username**: demo  
- **Password**: demo123456789  
- **Bot Password**: V1234567  
*Chat with Grok and sync your personal Q&A to Firestore instantly!*  

---

## ❓ Why Use AIChatVN 2? | Tại sao chọn AIChatVN 2?
- **Personal Assistant | Trợ lý cá nhân**: Chat with Grok in Vietnamese or English for tasks, study, or quick answers, with real-time Firestore sync.  
- **Knowledge Management | Quản lý tri thức**: Store and search your Q&A database with fast full-text search (FTS5).  
- **Customizable Chat Modes | Chế độ chat linh hoạt**: Choose QA, Grok, or Hybrid modes for tailored responses.  
- **File Support | Hỗ trợ tệp**: Upload images, JSON, or CSV files for enhanced chat or data import.  
- **Privacy First | Ưu tiên quyền riêng tư**: Run locally with SQLite or sync securely with Firestore for personal data control.  
- **Open-Source | Mã nguồn mở**: Customize freely under MIT License for personal or commercial use.  

---

## 🚀 Quick Start | Bắt đầu nhanh
Get AIChatVN 2 running in 5 minutes!  

**English**  
1. Clone the repo: `git clone https://github.com/dem30/aichatvn2.git && cd aichatvn2`  
2. Install Docker: [Download Docker](https://www.docker.com/get-started)  
3. Run: `docker run -p 7860:7860 ghcr.io/dem30/aichatvn2:latest`  
4. Open [http://localhost:7860](http://localhost:7860) and start chatting!  
5. (Optional) Add `GROQ_API_KEY` and `FIRESTORE_CREDENTIALS` for full features: Edit `config.py` or set environment variables.  

**Vietnamese**  
1. Tải dự án: `git clone https://github.com/dem30/aichatvn2.git && cd aichatvn2`  
2. Cài Docker: [Tải Docker](https://www.docker.com/get-started)  
3. Chạy: `docker run -p 7860:7860 ghcr.io/dem30/aichatvn2:latest`  
4. Mở [http://localhost:7860](http://localhost:7860) và bắt đầu trò chuyện!  
5. (Tùy chọn) Thêm `GROQ_API_KEY` và `FIRESTORE_CREDENTIALS` để sử dụng đầy đủ tính năng: Sửa `config.py` hoặc đặt biến môi trường.  

*Note: Try our [Hugging Face Demo](https://thaiquangvinh-dem302.hf.space/) with Username: `demo`, Password: `demo123456789`, Bot Password: `V1234567` to experience Grok and Firestore sync without setup!*  

---

## 🌍 Overview | Giới thiệu
**AIChatVN 2** is a next-generation conversational AI system for **businesses** and **developers**. It combines **local database-driven Q&A**, **natural responses via Grok/OpenAI**, and **real-time chat UI**, with secure Firestore sync for personalized data.  

Ideal for:  
- **Custom knowledge assistants** (FAQ, internal data bots).  
- **Interactive AI at kiosks** for sales, support, or community.  
- **On-premise** or **cloud-based** deployment (Docker/Hugging Face).  

**AIChatVN 2** là hệ thống AI trò chuyện thông minh, kết hợp **dữ liệu nội bộ (QA Database)**, **AI ngôn ngữ tự nhiên (Grok/OpenAI)**, **giao diện chat thời gian thực**, và **đồng bộ Firestore** để lưu trữ dữ liệu cá nhân an toàn.  

Phù hợp để:  
- Xây dựng **trợ lý AI nội bộ** (FAQ, hỗ trợ khách hàng).  
- Tạo **AI tương tác tại kios** cho bán hàng, cộng đồng.  
- Triển khai **tại chỗ** hoặc **trên đám mây**.  

---

## ⚙️ Features | Tính năng
- Local + Cloud AI (Grok, OpenAI, etc.) with real-time Firestore sync.  
- Async & Fast API backend (Python/Aiohttp).  
- Realtime WebSocket chat.  
- SQLite-based QA dataset with full-text search (FTS5).  
- File uploads (JSON, CSV, images) up to 1MB.  
- Dockerized environment.  
- Hugging Face Spaces compatible.  
- Multi-language UI (Vietnamese + English).  

---

## 🐳 Deployment with Docker | Triển khai bằng Docker
**English**  

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
Try the live demo at thaiquangvinh-dem302.hf.space with:
Username: demo
Password: demo123456789
Bot Password: V1234567
Vietnamese
Tạo Space mới trên Hugging Face (chọn loại “Docker”).
Tải toàn bộ file dự án (app.py, core.py, Dockerfile, …).
Cấu hình biến môi trường (xem phần Cấu hình bên dưới).
Hệ thống sẽ tự động build và chạy ứng dụng.
Trải nghiệm demo trực tiếp tại thaiquangvinh-dem302.hf.space với:
Tên người dùng: demo
Mật khẩu: demo123456789
Mật khẩu bot: V1234567
📝 Creating Q&A JSON | Tạo tệp Q&A JSON
To import Q&A data into AIChatVN 2, create a JSON file with the following format. This is used in the Training tab to build your custom knowledge base, which syncs to Firestore for personal use.
English
Create a JSON file (e.g., qa_data.json) with this structure:
[
    {
        "question": "What is Python?",
        "answer": "Python is a high-level programming language.",
        "category": "chat",
        "created_at": 1697059200,
        "timestamp": 1697059200
    },
    {
        "question": "How to install Docker?",
        "answer": "Follow the official Docker installation guide.",
        "category": "support"
    }
]
Required fields: question, answer.
Optional fields:
category: Defaults to "chat" (options: "chat", "support", "other").
created_at, timestamp: Unix timestamp (e.g., 1697059200). If omitted, the system sets the current time.
Upload the file in the Training tab via the "Import JSON Q&A" section or drag-and-drop.
Note: File size must be under 1MB (Config.MAX_UPLOAD_SIZE). Avoid duplicates to prevent errors.
Try it now: Create your JSON file, import it, and watch our TikTok demo to see it in action! Test it live at Hugging Face Demo with Username: demo, Password: demo123456789.
Vietnamese
Tạo tệp JSON (ví dụ: qa_data.json) với định dạng sau:
[
    {
        "question": "Python là gì?",
        "answer": "Python là một ngôn ngữ lập trình cấp cao.",
        "category": "chat",
        "created_at": 1697059200,
        "timestamp": 1697059200
    },
    {
        "question": "Cách cài Docker?",
        "answer": "Làm theo hướng dẫn cài đặt chính thức của Docker.",
        "category": "support"
    }
]
Trường bắt buộc: question, answer.
Trường tùy chọn:
category: Mặc định là "chat" (lựa chọn: "chat", "support", "other").
created_at, timestamp: Thời gian Unix (ví dụ: 1697059200). Nếu không có, hệ thống sẽ đặt thời gian hiện tại.
Tải tệp lên trong tab Training qua mục "Import JSON Q&A" hoặc kéo-thả.
Lưu ý: Kích thước tệp dưới 1MB (Config.MAX_UPLOAD_SIZE). Tránh trùng lặp để tránh lỗi.
Thử ngay: Tạo tệp JSON, nhập vào ứng dụng, và xem TikTok demo để biết cách làm! Trải nghiệm trực tiếp tại Hugging Face Demo với Tên người dùng: demo, Mật khẩu: demo123456789.
Tips:
Use a text editor like VS Code to create the JSON file.
Validate your JSON at jsonlint.com to avoid errors.
Download a sample Q&A JSON file: qa_data.json
🔧 Configuration | Cấu hình
To run AIChatVN 2, configure the following environment variables in config.py (local) or Hugging Face Spaces Secrets. See config.py.example for reference.
Variable
Description
How to Obtain
ADMIN_BOT_PASSWORD
Password for bot admin access
Create a secure password (e.g., V1234567)
ADMIN_PASSWORD
Password for admin dashboard
Create a secure password (e.g., demo123456789)
ADMIN_USERNAME
Username for admin dashboard
Choose a username (e.g., demo)
FIRESTORE_CREDENTIALS
JSON credentials for Google Firestore
See below
GROQ_API_KEY
API key for Groq AI 
See below
SECRET_KEY
Secret key for app security
Generate via Python secrets
CHAT_FILE_ALLOWED_FORMATS
File formats for uploads
Set to json,csv,png,jpg,jpeg (default)
Note: File uploads are limited to 1MB (Config.MAX_UPLOAD_SIZE). The Hugging Face demo is pre-configured with Grok API and Firestore for instant use.
🔐 Getting Firestore Credentials | Lấy JSON Firestore
Go to Google Cloud Console.
Create a project (e.g., aichatvn2-project).
Enable Firestore → Firestore Database → Create Database (Native Mode).
Create service account → Grant role: Firestore Admin.
Generate key → Add Key → Create New Key → JSON.
Download the JSON file (e.g., aichatvn2-credentials.json).
Set FIRESTORE_CREDENTIALS to this JSON string in config.py or Hugging Face Secrets.
Note: The Hugging Face demo already includes Firestore sync for personal Q&A storage.
🔑 Getting Groq API Key | Lấy Groq API Key
Go to xAI Developer Portal.
Sign up or log in.
Navigate to API Keys → Create New API Key.
Copy the key (e.g., gsk_xxxxxxxxxxxxxxxx).
Set GROQ_API_KEY in config.py or Hugging Face Secrets.
Note: The Hugging Face demo is pre-configured with Grok API for instant chat.
🧰 Setting Secrets in Hugging Face Spaces | Thiết lập Secrets
Go to your Space (e.g., thaiquangvinh-dem302.hf.space).
Open Settings → Repository Secrets.
Add variables:
ADMIN_BOT_PASSWORD = V1234567
ADMIN_PASSWORD = demo123456789
ADMIN_USERNAME = demo
FIRESTORE_CREDENTIALS = {"type":"service_account",...}
GROQ_API_KEY = xxxxxxxxxxxxxxxx
SECRET_KEY = your_random_secret_key
CHAT_FILE_ALLOWED_FORMATS = json,csv,png,jpg,jpeg
Save and redeploy.
Note: Use the demo credentials above to try the pre-configured Hugging Face Space.
🖥️ Local Setup | Cấu hình Local
# config.py
ADMIN_BOT_PASSWORD = "V1234567"
ADMIN_PASSWORD = "demo123456789"
ADMIN_USERNAME = "demo"
FIRESTORE_CREDENTIALS = '{"type":"service_account",...}'
GROQ_API_KEY = "gsk_xxxxxxxxxxxxxxxx"
SECRET_KEY = "your_random_secret_key"
CHAT_FILE_ALLOWED_FORMATS = "json,csv,png,jpg,jpeg"
🧠 How It Works | Cách hoạt động
User sends a message via the chat interface.
System searches the QA dataset using full-text search (FTS5).
Sends best-matched question to Grok for natural replies.
Syncs data to Firestore for personalized storage.
Displays responses in real-time on the web chat.
Perfect for FAQ bots, customer support, or teaching AI projects.
🎯 Use Cases | Trường hợp sử dụng
Personal Assistant | Trợ lý cá nhân: Ask Grok questions in Vietnamese or English, with Firestore sync for your personal data.
Knowledge Base | Cơ sở tri thức: Build and search a Q&A database for notes or FAQs, stored locally or in Firestore.
Small Business Chatbot | Chatbot doanh nghiệp nhỏ: Create a customer support bot with custom Q&A and file uploads.
Developer Playground | Sân chơi cho nhà phát triển: Customize chat modes (QA/Grok/Hybrid) or extend features using the MIT-licensed code.
💰 Monetization | Kiếm tiền
Donations: Support us via GitHub Sponsors.
Commercial use: Sell as a chatbot or SaaS solution.
Keep attribution: Include “Developed by AIChatVN Team” in your projects.
💼 Commercial Use | Mục đích thương mại
Custom Chatbots: Build tailored chatbots for businesses (e.g., restaurants, retail) using Q&A import and Grok integration.
SaaS Solutions: Host AIChatVN 2 as a subscription-based service for customer support or FAQ automation.
Enterprise Integration: Deploy on-premise with SQLite or Firestore for secure, private AI assistants.
Contact us on Zalo (0944121150) for commercial inquiries or customization support. Please include attribution to AIChatVN Team.
🙌 Support Us | Hỗ trợ chúng tôi
AIChatVN 2 is free and open-source — your support keeps it alive!
🌟 Sponsor: Support us on GitHub Sponsors
📱 Follow us: TikTok for updates, tutorials, and demos!
📞 Contact: Reach us on Zalo (0944121150) for feedback or inquiries.
Your $1 donation or TikTok follow helps us bring Vietnamese AI to the world!
🤝 Contribute | Đóng góp
Help make AIChatVN 2 better!
Report Bugs: Open an issue on GitHub Issues.
Add Features: Submit Pull Requests to enhance Q&A search, chat modes, or Firestore sync.
Join the Community: Share ideas on our TikTok page or via Zalo (0944121150).
Contributors get a shoutout in our README and TikTok videos!
📜 Attribution | Ghi công
If you use or fork AIChatVN 2:
Keep the “Developed by AIChatVN Team” notice.
Mention us in your README or website.
📞 Contact: 0944121150 (Zalo)
👨‍💻 Author | Tác giả
AIChatVN Team
Developed by Vietnamese developers passionate about open-source AI.
📍 Nha Trang, Khánh Hòa, Vietnam (65000)
📧 Zalo: 0944121150
🪪 License | Giấy phép
MIT License – Free for commercial and personal use.
See LICENSE for details.
🚀 AIChatVN 2 — Bring Vietnamese AI to the world!
🚀 AIChatVN 2 — Đưa trí tuệ nhân tạo Việt ra toàn cầu!
---


