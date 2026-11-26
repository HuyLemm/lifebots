# LifeBots

Bốn bot Telegram, mỗi bot một folder riêng (token, config, trạng thái riêng), dùng nguồn dữ liệu miễn phí.

| Folder | Làm gì |
|---|---|
| `price/` | Giá vàng (SJC, PNJ, thế giới) và xăng E10 RON 95 (mặc định chỉ báo hai thứ này; bạc, Bitcoin, Brent, USD/VND, bản tin sáng, bản tin "còn sống" có sẵn nhưng đang tắt trong config); báo khi giá đổi kèm lý do (đối chiếu số liệu), chênh mua–bán, đỉnh/đáy 30 ngày; cảnh báo theo ngưỡng riêng, biểu đồ, nhắc kỳ điều hành xăng thứ Năm, bản tin buổi sáng 7h, thống kê tuần |
| `news/` | Tin công nghệ (ưu tiên) + chiến tranh, kinh tế, vụ án từ RSS; lọc trùng giữa các báo; nhiều tin cùng lúc gộp thành 1 bản tổng hợp |
| `weather/` | Báo cáo thời tiết 0h/8h/12h/16h/20h + cảnh báo bất thường bất cứ lúc nào (mưa, dông, nóng, UV, gió, AQI...) |
| `stuff/` | Sản phẩm giảm giá (kèm giá) trên Shopee, Lazada, TikTok Shop, đọc từ kênh Telegram săn sale công khai, lọc theo sở thích |
| `shared/` | Dùng chung: gửi Telegram (tự thử lại), lệnh Telegram, log xoay vòng, cảnh báo nguồn hỏng, vòng lặp chạy |

Mỗi folder bot có: `bot.py`, `config.toml` (chỉnh thông số), `.env` (token, không commit), `state.json` (tự sinh).

## Cài đặt
    python -m venv .venv
    .venv\Scripts\activate
    pip install -e ".[dev]"

1. @BotFather > /newbot, tạo 4 bot, dán token vào dòng `BOT_TOKEN=` của `price/.env`, `news/.env`, `weather/.env`, `stuff/.env`.
2. Mở từng bot trong Telegram, bấm Start.
3. Chỉnh `config.toml` trong từng folder.

## Chạy
    python -m price.bot --dry --now    # thử, in ra màn hình, KHÔNG ghi trạng thái
    python -m price.bot --now          # gửi 1 tin thật
    python run_all.py                  # chạy cả 4 bot, tự khởi động lại bot nào chết

**Tự chạy khi đăng nhập Windows:** tác vụ `LifeBots` (Task Scheduler) chạy `run_all.py` ẩn.
- Xem: `Get-ScheduledTask LifeBots`; chạy ngay: `Start-ScheduledTask LifeBots`; dừng: `Stop-ScheduledTask LifeBots` rồi tắt tiến trình python của LifeBots.
- Đăng ký lại nếu cần: Action = `.venv\Scripts\pythonw.exe -X utf8 run_all.py` (thư mục làm việc = folder LifeBots), Trigger = AtLogOn.
- Log: `logs/<bot>.log` (xoay vòng 1MB x 3), lỗi khởi động: `logs/<bot>.stderr.log`.

## Điều khiển ngay trong Telegram
Mọi bot: `/help` (danh sách lệnh), `/status` (tình trạng), `/now` (gửi ngay bản hiện tại), `/pause`, `/resume`. Chỉ chat của bạn (`TELEGRAM_CHAT_ID`) được nghe lệnh.
Bot **price**: `/bao sjc 130` (báo khi SJC bán ra ≤ 130 triệu; `/bao btc trên 70000` báo khi ≥), `/dsbao`, `/xoabao <số|all>`, `/bieudo sjc 30` (biểu đồ 30 ngày). Mã: sjc, nhan, pnj, pnj24k, xau, usd, bac, btc, brent, ron95 (bac, btc, brent chỉ hoạt động khi bật tracker tương ứng trong price/config.toml).
Bot **stuff**: `/keywords`, `/add <từ>`, `/del <từ>`, hoặc bấm nút 🚫 dưới tin để bỏ từ khoá đã khớp. Thêm: `/theodoi <từ>` (deal chứa từ này luôn được báo, gắn 🔥, gửi trước), `/botheodoi`, `/dstheodoi`; `/chan <từ>` (không bao giờ báo deal chứa từ này), `/bochan`, `/dschan`; `/tincay <hãng>` (cộng điểm ⭐ và gắn ✅), `/botincay`, `/dstincay`. Deal có điểm ⭐ 0–5, dòng 📉 "rẻ hơn lần trước", ⏳ hạn dùng và nhắc ⏰ trước khi hết hạn; `min_score` trong config (mặc định 0 = không bỏ deal nào).
Bot **news**: `/tat <chủ đề>`, `/bat <chủ đề>`, `/chude` (công nghệ, thế giới, kinh tế, vụ án); `/chan <từ>`, `/bochan`, `/tuchan`; `/uutien <từ>` (gắn 🔥, gửi trước, bỏ qua lọc từ khoá của nguồn), `/bouutien`, `/dsuutien`; nút **📌 Đọc sau** dưới mỗi tin, `/doclai`, `/xoadoclai` (Chủ nhật 19h tự nhắc danh sách). Tóm tắt AI tuỳ chọn: `[ai] enabled = true` + secret `ANTHROPIC_API_KEY` (mặc định tắt).
Bot **weather**: `/diadiem`, `/them <tên>` (tra toạ độ tự động, tối đa 4 nơi thêm), `/bo <tên>`; nhắc trước khi ra đường qua `commute_times` trong config.
Thay đổi bằng lệnh lưu trong `state.json`, không cần sửa file.

## Độ tin cậy (đã xử lý)
- Tin chỉ được đánh dấu "đã báo" **sau khi Telegram nhận thành công**; lỗi thì thử lại, không mất tin. Telegram 429/5xx tự thử lại.
- Bot không chết khi nguồn đổi định dạng: lỗi được ghi log, đếm, và nếu một nguồn lỗi 3 lần liên tiếp bạn nhận 1 cảnh báo (và 1 tin khi nguồn hoạt động lại).
- Các bot theo dõi lẫn nhau (price canh news/weather/stuff, weather canh price) và báo nếu có bot im lặng. Mỗi ngày 9h bot price gửi bản tin "LifeBots còn sống" (thứ Hai kèm giá tuần qua).
- `--dry` không bao giờ ghi trạng thái hay lịch sử.

## Kiểm tra
    pytest                      # logic, độ tin cậy, và parser chạy trên dữ liệu thật đã lưu (tests/fixtures)
    ruff check . && ruff format --check .
Khi một nguồn đổi cấu trúc, `tests/test_sources.py` là nơi báo trước. Làm mới mẫu bằng cách tải lại file trong `tests/fixtures/`.

## Chạy 24/7 trên server (Docker)
Server cần Docker + Docker Compose:

    # đưa code lên (git clone hoặc scp, KHÔNG gồm .venv); copy các file .env lên
    docker compose up -d --build     # chạy nền, tự khởi động lại khi reboot
    docker compose logs -f           # xem log
    docker compose ps                # trạng thái
    docker compose down              # dừng

Sửa `config.toml` xong chỉ cần `docker compose restart`. Múi giờ container là Asia/Ho_Chi_Minh.
Nhớ dừng tác vụ `LifeBots` trên máy local trước khi bật trên server (hai nơi cùng dùng một token sẽ tranh nhau nhận lệnh và gửi trùng).
Lưu ý: mỗi container chỉ thấy thư mục của chính nó nên cảnh báo "bot im lặng" chỉ hoạt động khi chạy bằng `run_all.py`.

## Ghi chú
- Giá vàng ở bot price là SJC/PNJ lấy từ nguồn chính thức; vàng thế giới chỉ để tham khảo.
- Shopee/Lazada/TikTok Shop không có nguồn miễn phí hợp lệ; bot stuff đọc kênh Telegram công khai nên giá chỉ là giá người đăng ghi.
- Thêm bot mới: tạo folder với `bot.py` + `config.toml` + `.env`, gọi `run(Path(__file__).parent, check, giây)` từ `shared.core`.
- Ảnh đại diện bot: `python avatars/make_avatars.py`, rồi gửi từng ảnh cho @BotFather bằng `/setuserpic`.

## Triển khai lên server bằng 1 lệnh
    .\deploy\deploy.ps1 -Server ubuntu@IP_SERVER                 # đã có Docker
    .\deploy\deploy.ps1 -Server root@IP -InstallDocker           # cài Docker giúp (Ubuntu/Debian)
    .\deploy\deploy.ps1 -Server ubuntu@IP -Key C:\Users\ban\.ssh\id_ed25519 -Port 22

Script: dừng bot local (tránh 2 nơi cùng nhận lệnh/gửi trùng) -> đóng gói (kèm `.env` và `state.json` để bot tiếp tục, không gửi lại tin cũ) -> tải lên -> `docker compose up -d --build` -> in trạng thái. Nếu server lỗi, script **tự bật lại bot local**.
Cập nhật code sau này: chạy lại đúng lệnh trên (chỉ build lại phần thay đổi). Muốn bật lại bot local: `Enable-ScheduledTask LifeBots; Start-ScheduledTask LifeBots` (và dừng trên server: `docker compose down`).

### Server miễn phí
Bot chỉ gọi ra ngoài, **không cần mở cổng nào**, và 4 bot dùng khoảng 200–250 MB RAM nên VM 1 GB là đủ.
- **Google Cloud e2-micro** (Always Free): 1 VM ở Oregon `us-west1`, Iowa `us-central1` hoặc South Carolina `us-east1`, 30 GB đĩa, 1 GB truyền dữ liệu Bắc Mỹ/tháng. Cần tài khoản thanh toán (thẻ). Nên đặt cảnh báo ngân sách để không bị tính phí ngoài ý muốn.
- **Oracle Cloud Always Free:** VM cấu hình cao, chọn được vùng Singapore, nhưng hay báo hết tài nguyên, có chính sách thu hồi máy ít dùng, và hạn mức Ampere A1 bị giảm từ 8/2026.
- **Không cần thẻ:** máy cũ hoặc Raspberry Pi ở nhà, hoặc cứ để máy tính này chạy (tác vụ `LifeBots`).
Máy 1 GB nên thêm swap: `sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`.

## Chạy bằng GitHub Actions (không thẻ, không server)
Bot chạy theo lịch trên máy chủ miễn phí của GitHub, `git push` là deploy. Đánh đổi: lệnh Telegram (`/status`...) trả lời ở lượt chạy kế tiếp, và **lịch `schedule` của GitHub rất thưa** (có thể chỉ vài lượt mỗi ngày). Vì vậy dùng thêm **cron-job.org** (miễn phí) gọi API `workflow_dispatch` mỗi 15 phút, xem mục dưới.

    winget install --id GitHub.cli -e      # 1 lần; mở terminal mới
    gh auth login                          # 1 lần
    .\deploy\github_setup.ps1 -Repo lifebots -IKnowItIsPublic

Script tạo repo **công khai** (gói free chỉ không giới hạn phút chạy với repo công khai), đưa token vào GitHub Secrets (mã hoá, không nằm trong code), nạp trạng thái hiện tại, dừng bot local (tránh hai nơi cùng nhận lệnh) và chạy lượt đầu. Công khai: code và `config.toml` (từ khoá, kênh). Bí mật: token, chat id.
- Workflow: `.github/workflows/lifebots.yml`. Chế độ chạy: `python -m <bot>.bot --once` (mỗi bot chỉ kiểm tra khi đến hạn `interval_seconds` của nó).
- Trạng thái nằm ở nhánh `state` (1 commit, ghi đè mỗi lượt nên không phình). Mỗi ngày một bản **sao lưu** được thêm vào nhánh `state-backup` (có lịch sử, khôi phục bằng cách checkout file từ nhánh đó).
- Mỗi lần push, workflow `tests` chạy `ruff` + `pytest` (tab Actions). Biểu đồ cần Pillow, workflow tự cài.

### cron-job.org (lịch đều đặn)
1. GitHub → Settings → Developer settings → Fine-grained token: chỉ repo này, quyền **Actions: Read and write**, hạn 1 năm.
2. cron-job.org → Create cronjob: URL `https://api.github.com/repos/<user>/<repo>/actions/workflows/lifebots.yml/dispatches`, mỗi 15 phút, method **POST**, body `{"ref":"main"}`, header `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `Content-Type: application/json`, `X-GitHub-Api-Version: 2022-11-28`. Thành công = HTTP 204.
3. Settings của cron-job.org: bật **thông báo email khi job lỗi** (token hết hạn, GitHub từ chối...).
4. Ghi ngày hết hạn token vào `cron_token_expires` trong `price/config.toml`: bot price nhắc mỗi ngày trong 14 ngày cuối.
- Lưu ý điều khoản: GitHub cấm dùng Actions như "serverless computing"; tải ở đây rất nhẹ nhưng vẫn là vùng xám. Phương án dự phòng: máy ảo + `deploy/deploy.ps1`.
- Xem log: tab **Actions** trên GitHub. Chạy tay: `gh workflow run lifebots.yml`.
- Lịch của GitHub bị tạm dừng nếu repo không hoạt động 60 ngày; workflow có bước tự bật lại, nhưng nếu thấy bot im lặng hãy vào tab Actions bấm **Enable workflow**.
- Quay lại máy local: `Enable-ScheduledTask LifeBots; Start-ScheduledTask LifeBots` và tắt workflow trên GitHub (tránh hai nơi cùng chạy).
