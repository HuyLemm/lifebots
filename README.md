# LifeBots

Bốn bot Telegram, mỗi bot một folder riêng (token, config, trạng thái riêng), dùng nguồn dữ liệu miễn phí.

| Folder | Làm gì |
|---|---|
| `price/` | Giá vàng (SJC, PNJ, thế giới), xăng dầu Petrolimex, tỷ giá USD/VND; báo ngay khi giá cập nhật; lưu lịch sử, tóm tắt tuần |
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
Mọi bot: `/status` (tình trạng), `/now` (gửi ngay bản hiện tại), `/pause`, `/resume`. Chỉ chat của bạn (`TELEGRAM_CHAT_ID`) được nghe lệnh.
Bot **stuff**: `/keywords`, `/add <từ>`, `/del <từ>`, hoặc bấm nút 🚫 dưới tin để bỏ từ khoá đã khớp. Thay đổi lưu trong `state.json`, không cần sửa file.

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
