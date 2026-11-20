# ezioe

Tự động hoàn thành bài **Tự luyện** trên [ioe.vn](https://ioe.vn) chỉ bằng Python,
không cần mở giao diện game: đăng nhập → lấy danh sách bài của vòng → giải từng
bài → nộp → ghi kết quả vòng → sang vòng tiếp theo.

Ý tưởng giống **IOEAuto**: một "answer engine" chạy phía server, tự sinh đáp án và
gửi thẳng vào API của IOE. Khác ở chỗ công cụ này không cần kho đáp án tĩnh:
đáp án được lấy từ chính dữ liệu server trả về, và `answercheck` được dùng như
một *oracle* để kiểm tra đúng/sai ngay trên client.

## Cài đặt

```bash
git clone https://github.com/SVTBTranVanBinh/ezioe && cd ezioe
cp data/config.example.json data/config.json     # rồi điền tài khoản/mật khẩu
pip install requests soundfile vosk     # bắt buộc (vosk = nghe/ASR offline)
pip install mitmproxy                   # chỉ khi dùng chế độ proxy (tuỳ chọn)
```

Lần đầu chạy, model nhỏ tiếng Anh (~40 MB) được tải về `data/asr/`; sau đó
mọi bài nghe được giải offline, không cần API key.

Python 3.10+ (đã test trên 3.15).

`data/config.json`, `data/session*.json`, `data/report.json` **không** nằm trong
repo (chứa mật khẩu, token, khoá AI) — xem `.gitignore`.

## Dùng

```bash
python run.py config                       # tạo data/config.example.json
python run.py status                       # xem các bài của vòng đang mở
python run.py run                          # làm hết vòng hiện tại rồi ghi kết quả
python run.py run --rounds 5               # làm liên tiếp 5 vòng
python run.py run --pace 120 480           # mỗi bài 2-8 phút, ngẫu nhiên
python run.py run --only 3924              # chỉ làm 1 bài (idExercise)
python run.py run --no-sleep               # bỏ toàn bộ delay (nhanh nhất)
python run.py run --delay 1 3 --between 30 120   # delay ngẫu nhiên (giây)
python run.py reset                        # huỷ kết quả vòng đang mở
python run.py proxy --bank                 # MITM proxy, xem đáp án stream về client
python run.py report                       # bảng điểm các lượt đã chạy
python run.py stats --days 14              # điểm theo ngày (biểu đồ, --csv để xuất)
python run.py balance                      # RUBY / coin
python run.py doctor                       # tự kiểm tra môi trường / phiên / mạng
python run.py gui                          # mở giao diện desktop
python run.py schedule --at 08:30          # tự chạy mỗi ngày bằng Task Scheduler/cron
python run.py schedule --every 90          # hoặc mỗi 90 phút
python run.py unschedule                   # xoá lịch
python run.py run --jobs 3                 # 3 tài khoản chạy song song
python run.py run --with-speaking          # làm cả bài tự chọn (kỹ năng nói, TTS)
python run.py capabilities                 # dò lớp nào còn sống khi nghi bị vá
python run.py endpoints --discover         # tự dò lại API khi IOE đổi version
python run.py schema                       # so response với fixture, báo field đã đổi
python run.py bank export --out kho.json   # sao lưu kho đáp án
python run.py userscript                   # phương án B: userscript cho trình duyệt
python run.py ai --dry-run                 # phương án C: AI nhìn ảnh chọn ô (không bấm)
python run.py run --fallback ai            # API chết thì tự chuyển phương án C
python run.py run --profile careful        # nhịp chạy sẵn: careful/normal/fast/turbo
python run.py watch                        # tự chờ vòng mới mở rồi làm (không cần Task Scheduler)
python run.py weak                         # bài chưa đạt điểm + dạng bài hay mất điểm
python run.py capture --seconds 180        # bắt request thật (mò hợp đồng bài nói)
python run.py backup                       # nén data/ thành zip, giữ 7 bản gần nhất
python run.py restore data/backup-....zip  # nhập lại (không đè đáp án đang tốt hơn)
python run.py bank show "would like"       # tra kho đáp án
python run.py bank prune                   # bỏ đáp án rỗng/0 điểm
python run.py plan --milestone province    # cần bao nhiêu vòng/ngày tới hạn, có kịp không
python run.py plan --target 25 --by 02/03 --apply --schedule   # ghi nhịp + tạo lịch
```

### `plan` — đủ vòng trước hạn hay không

IOE chỉ mở **một vòng mỗi tuần** (tối đa 35 vòng/năm), nên chạy nhanh mấy cũng
không vượt được tốc độ mở vòng. `plan` tính đúng phần làm được:

* `--milestone province` (vòng 25 trước 02/03) hoặc `national` (vòng 30), hoặc tự
  đặt `--target/--by`.
* In ra số vòng còn thiếu, số ngày, vòng/ngày cần, và **có kịp hay không** dựa
  trên số vòng hệ thống sẽ mở trước hạn.
* `--apply` ghi `max_rounds_per_day` + khung giờ chạy; `--schedule` tạo lịch luôn.

Mốc riêng: thêm `"milestones": {"province": [25, "02/03"]}` vào config.

### Nhịp chạy có sẵn (`--profile`)

| Profile | Mỗi bài | Giữa vòng | Giữa câu |
|---|---|---|---|
| `careful` | 3–7 phút | 2–8 phút | 1–3s |
| `normal` (mặc định) | 1–5 phút | 30–180s | 0.3–1s |
| `fast` | 30–90s | 10–40s | 0.2–0.6s |
| `turbo` | ~20s | 0–3s | 0.05–0.2s |

### `watch` — chạy nền thay Task Scheduler

`run.py watch` kiểm tra định kỳ (`--poll` phút), có bài thì làm, chưa có thì in giờ
vòng mở và ngủ. Hợp với máy bật 24/7; `--once` để kiểm tra một lần.

### Chạy ngầm, không cần tham số (`data/config.json`)

```json
{
  "accounts": [{"user": "SVTBTranVanBinh", "password": "..."}],
  "rounds": 3,
  "pace": [60, 300],
  "between": [30, 180],
  "attempts": 3,
  "turbo": false,
  "dry_run": false,
  "active_hours": [7, 23],
  "max_rounds_per_day": 6,
  "wait_for_round": true,
  "notify": {"telegram": {"token": "", "chat_id": ""}},
  "speaking": {"tts": true, "voice": "", "submit": false},
  "endpoints": {"game": "https://api-edu.go.vn/ioe-service/v2/game/"}
}
```

* Mỗi tài khoản có phiên riêng (`data/session_<user>.json`); token được kiểm tra
  đúng chủ (`s_username`) trước khi dùng, nên **thêm tài khoản mới không bị
  "dính" trạng thái vòng của tài khoản trước**.
* `python run.py run` đọc file này: nhiều tài khoản chạy lần lượt, mỗi bài một
  khoảng thời gian ngẫu nhiên (`pace`), nghỉ giữa vòng (`between`), chỉ chạy
  trong `active_hours`, tối đa `max_rounds_per_day` vòng/ngày. Chạy tay bằng
  `--only <id>` thì bỏ qua khung giờ (để sửa một bài lúc nào cũng được).
* Vòng được đọc từ chính tài khoản đang chạy (`roundInfoRes.bilInfo`:
  `current_round` / `sysRound`). Khi tài khoản còn vòng chưa làm, tool làm tiếp
  vòng kế **ngay**, không báo "hết vòng"; chỉ khi tài khoản đã làm tới
  `sysRound` mới chờ vòng mới mở.
* Một vòng chỉ được tính vào `report`/`max_rounds_per_day` khi server xác nhận
  mọi bài `isRank` của vòng đó đã nộp (`Status` 2/3). Bài lỗi thì vòng đó không
  bị tính, lần chạy sau làm lại.
* `--turbo`: một lượt ngắn ~20–30s/bài (budget riêng), khác chế độ thường là
  một lượt dài có thời gian suy nghĩ.
* `wait_for_round`: khi vòng đã xong (hoặc IOE đang **đóng tính năng Tự luyện**)
  mà chưa mở lại, tool **ngủ tới đúng giờ mở** (khung giờ đóng được đọc từ chính
  thông báo của server, giờ mở lấy từ `getTuLuyenOpenSchedule`) rồi làm tiếp —
  đóng máy thì mở lại là chạy. Chạy nhiều tài khoản thì không ngủ, chỉ bỏ qua lượt.
* `speaking.play` (hoặc `--with-speaking`): làm thêm bài tự chọn kỹ năng nói;
  bài tự chọn không chặn việc ghi kết quả vòng.
* `endpoints`: đổi URL API không cần sửa code (dynamic update).
* `notify.telegram`: điền token + chat_id là có thông báo realtime mỗi vòng.
* `jobs`: số tài khoản chạy **song song** (mặc định 1). Kho đáp án và file
  report dùng khoá chung nên chạy song song không hỏng dữ liệu.
* `only`: làm mãi một `idExercise` rồi dừng (không tính vòng).

Lần chạy đầu sẽ hỏi tài khoản/mật khẩu (hoặc truyền `-u` / `-p`, hoặc đặt
`IOE_PASSWORD` / `IOE_PASSWORD_<user>` khi chạy ngầm không có terminal). Token và
`deviceId` được lưu riêng trong `data/session_<user>.json`, các lần sau dùng
lại và tự refresh khi hết hạn.

## Cách hoạt động

### 1. Đăng nhập (`ioeauto/api.py`)

`POST https://identity.go.vn/api/auth/login` với body mà SPA của IOE gửi:

```json
{
  "ipClient": "...", "deviceId": "...", "deviceOs": "...", "deviceOsVersion": "...",
  "serviceCode": "IOE", "api_key": "string", "redirectUrl": "string", "deltatime": 0,
  "username": "...", "password": "<md5>", "captchaToken": "", "publicKey": "0x4AAAA...",
  "oidcParams": {"client_id": "jXEJcp17qKvkZwo", "redirect_uri": "...", "state": "...",
                 "code_challenge": "<S256>", "code_challenge_method": "S256"},
  "remember": false
}
```

Mật khẩu là MD5 (chữ thường) — đúng như `passwordHash-*.js` của trang đăng nhập.
`publicKey` là Turnstile site key; server hiện không bắt buộc token thật.
Trả về `access_token` (JWT) + `refresh_token`; refresh qua
`POST /api/auth/refresh-token`.

### 2. Vòng luyện (`ioeauto/games.py`)

```
TuLuyen/getlistGameAndResultMax   -> danh sách bài (Status, mark, isRank)
TuLuyen/setinfo                   -> urlgame chứa token ngắn
game/getinfo                      -> đề + examKey + token dài
game/startgame                    -> bắt đầu tính giờ
game/answercheck                  -> chấm từng câu, point > 0 nghĩa là đúng
game/finishgame                   -> nộp cả bài
TuLuyen/comfirmresult             -> ghi kết quả vòng (chỉ khi mọi bài isRank đã xong)
```

Hai đặc điểm quan trọng của gateway:

* **Đáp án được gửi xuống client.** `game.question[].ans[]` có `orderTrue` (thứ tự
  đúng) và `content` (nội dung), nên phần lớn bài không cần "giải" gì cả.
* **`answercheck` là oracle.** Nó trả `data.point`; gọi lại với đáp án khác vẫn
  được, nên công cụ có thể thử - sai - thử lại ngay trong một lượt làm bài.
* `finishgame` từ chối (`code 38 "Bài làm không hợp lệ"`) nếu `totalTime` nhỏ hơn
  thời gian thật kể từ `startgame`; vì vậy thời gian được đo và bù cho đủ.
* Bài chỉ được tính là xong khi `isPass = 1` (≈ ≥ 70% điểm), nếu không `Status`
  vẫn là 1 và `comfirmresult` báo "chưa hoàn thành hết bài thi". Vì vậy mỗi bài
  được làm lại tối đa 5 lượt cho tới khi pass.

### 3. Sinh đáp án theo từng dạng bài

| type | Dạng | Cách giải |
|------|------|-----------|
| 3 | điền từ / viết lại câu | ghép các từ theo `orderTrue` bằng `\|`, thử hoán vị nếu sai; nếu `Description` có mẫu `***` thì suy từ bằng biến đổi hình thái (so sánh hơn/nhất) |
| 5 | sắp xếp từ thành câu | ghép theo `orderTrue` |
| 7 | nối cặp | `"trái\|phải"` |
| 10 | trắc nghiệm / nghe | nội dung option |
| 1 | nghe - chép / nghe âm / True-False | **ASR (Vosk) nghe chính mp3 của đề** rồi lấy nguyên văn; True/False thì hỏi oracle `answercheck` (đúng = Point, sai = 0) |
| 2 | điền từ / điền ký tự / nghe chọn đáp án | có audio → ASR + so khớp ngữ cảnh để xếp hạng đáp án; có options → thử từng option bằng oracle; không có gì → tra từ điển theo tần suất (wordfreq) + gợi ý ngữ pháp |
| 12, 13 | nói / đọc câu (AI chấm) | TTS offline → tải audio lên dịch vụ chấm của IOE → nộp `request_id` (`speaking.submit`) |

Mọi đáp án đúng được lưu vào `data/answers.json` (khoá = hash của `type|nội dung câu`),
lần gặp lại sẽ trả lời ngay.

Thứ tự ứng viên được xếp theo **ngữ pháp** (so sánh hơn, sau trợ động từ → phân từ,
sau modal → nguyên mẫu, chỗ trống giữa hai danh từ → giới từ, sau *very*/giới từ →
trạng từ…) rồi mới tới tần suất, và ưu tiên từ có trong chính đề; từ rác do máy tự
chế luôn xếp sau từ thật. Đo lại bằng đề thật:

```bash
python tools/bench_answers.py -u <acc> --exercises 3    # hit@1..@40, danh sách "hụt"
```

Bài tự chọn kỹ năng nói (`--with-speaking`) hiện **chấm 0 điểm**: server chấm bằng AI
và chưa nhận dạng được audio TTS (đã gửi đúng dạng WAV 16 kHz mono). Bài này không
phải điều kiện hoàn thành vòng nên không ảnh hưởng điểm vòng.

### 4. Chế độ MITM (`ioeauto/proxy.py`)

Bản gốc của ý tưởng là bắt traffic: đáp án nằm sẵn trong response nên chỉ cần đọc
là biết. Chạy `python run.py proxy --bank`, trỏ browser qua `127.0.0.1:8080`,
cài CA ở `mitm.it`. Addon in ra đề + đáp án của từng câu và ghi những đáp án
`answercheck` chấm đúng vào kho.

## Khi IOE vá (chuỗi fallback)

Tool chia thành nhiều lớp, lớp trên chết thì tự tụt xuống lớp dưới. Chết lớp nào
thì làm đúng việc ở cột phải:

| IOE làm gì | Tool vẫn chạy bằng | Việc cần làm |
|---|---|---|
| Không gửi `ans[]` nữa | kho đáp án + `answercheck` + ASR + từ điển | Không cần gì; kho tự dày lên. `run.py bank export` để sao lưu |
| Bỏ `answercheck` (chấm server-side) | kho đáp án + ASR + xếp hạng ngữ pháp | Tự tắt oracle (`oracle_dead`). Thêm kho: `run.py bank import file.json` |
| Đổi version API (v3 → v4) | tự dò và ghi đè endpoint | `run.py endpoints --discover` |
| Đổi tên field / cấu trúc response | fixture báo lệch từng field | `run.py schema` xem chỗ lệch, sửa đúng hàm parse |
| Chặn IP, chặn TLS client lạ, bắt captcha | **phương án B**: userscript chạy trong trình duyệt thật | `run.py userscript` → cài Tampermonkey → bấm nút trong trang |
| Bịt luôn cả userscript (đổi giao diện, đổi game) | **phương án C**: trình duyệt thật + AI nhìn ảnh chia lưới | `run.py ai` |

Dò nhanh lớp nào còn sống: `run.py capabilities` (kèm gợi ý), xem lại bằng `run.py doctor`.

### Phương án B — userscript

```bash
python run.py userscript          # sinh data/ioe-fallback.user.js (kèm kho đáp án)
```

Dán vào Tampermonkey, mở `https://ioe.vn/hoc-sinh/tu-luyen`, bấm **Chạy ezioe**.
Script dùng chính phiên đăng nhập của tab nên qua được mọi thứ chặn client lạ.

### Phương án C — AI nhìn ảnh và bấm

```bash
set EZIOE_AI_PROVIDER=gemini & set EZIOE_AI_KEY=...     # hoặc openai / anthropic
python run.py ai --dry-run        # xem AI chọn ô nào, không bấm
python run.py ai                  # làm thật (Edge/Chrome, profile riêng từng acc)
python run.py run --fallback ai   # API chết thì tự chuyển sang phương án C
```

Cách chạy: mở đúng bài bằng phiên trình duyệt → chụp màn hình → chia lưới 8×6 có
nhãn A1..H6 → AI chọn ô → phóng vào ô đó, chia lưới 5×5 lần hai → AI chọn ô con →
bấm tâm ô. Câu **viết** (dictation, fill) thì bấm vào ô trả lời rồi gõ chữ AI đưa;
câu chọn/ghép thì bấm. Hết câu bấm *Nộp bài*, xong cả 4 bài thì bấm *Lưu kết quả*.
Ảnh từng bước lưu ở `data/vision/` để soi lại khi AI chọn sai.

Cấu hình trong `data/config.json` → `vision`: `provider`, `model`, `key_env`,
`base` (dùng endpoint tương thích OpenAI), `headless`, `port`, `profile`,
`max_steps`. Không có khoá AI thì chỉ phương án C không chạy, phần còn lại vẫn nguyên.

### Giữ tài sản khi mọi thứ đổ

```bash
python run.py bank export --out kho.json   # kho đáp án (726 câu) - thứ đáng giữ nhất
python run.py bank import kho.json         # nhập lại, chỉ thêm câu chưa có/chưa tốt
python run.py bank stats
```

## Chống bị phát hiện

* **mỗi bài một khoảng thời gian ngẫu nhiên** `--pace MIN MAX` (mặc định 60–300s,
  luôn chốt dưới 540s = 9 phút): thời gian này được chia cho việc suy nghĩ, gõ
  đáp án và các lần làm lại, nên không bài nào chạy quá 10 phút.
* delay ngẫu nhiên giữa các câu (`--delay`, mặc định 0.3–1.0s) và giữa các vòng
  (`--between`, mặc định 30–180s).
* thời gian nộp bài luôn ≥ thời gian thật, và ≥ 18s mỗi bài.
* lỗi mạng/DNS được thử lại 4 lần; token hết hạn (85/86) tự refresh.
* giữ nguyên `deviceId` của tài khoản giữa các lần chạy.
* có thể chạy nền (`nohup`, Task Scheduler) vì không cần UI.

## Giao diện desktop, khay hệ thống, .exe

```bash
python run.py gui            # cửa sổ: danh sách acc, thiết lập, log trực tiếp, Bắt đầu/Dừng
```

Cửa sổ chỉ sửa `data/config.json` rồi chạy đúng luồng của CLI. Nút *Kiểm tra*
hiện số bài/vòng của từng acc; *Tự kiểm tra* gọi `doctor`. Đóng cửa sổ khi đang
chạy sẽ hỏi dừng trước.

Thu nhỏ xuống khay hệ thống: `pip install pystray Pillow` (tuỳ chọn).

Đóng gói `.exe` (cần `pip install pyinstaller`):

```bash
build.bat                 # hoặc: python packaging/build.py
```

Ra `dist/ioe.exe` (CLI) và `dist/IOE-Auto-GUI.exe` (GUI, không console). Chương
trình đọc `data/` **cạnh file .exe** — copy thư mục `data` (config, session,
answers) ra đó trước khi chạy.

## Lịch chạy tự động

`run.py schedule` tạo một job thật của hệ điều hành (Task Scheduler trên Windows,
crontab trên Linux/macOS), nên nó vẫn chạy sau khi reboot và không cần cửa sổ nào:

```bash
python run.py schedule --at 08:30 --dry-run   # xem lệnh trước
python run.py schedule --at 08:30
python run.py schedule --every 90             # mỗi 90 phút
python run.py unschedule
```

Trên Windows nên chạy bằng PowerShell "Run as administrator" nếu bị từ chối quyền.

## Kiểm tra sức khoẻ

`python run.py doctor` soi: phiên bản Python, quyền ghi `data/`, config, tài
khoản/phiên, model ASR, thư viện tuỳ chọn, dung lượng đĩa — mỗi lỗi kèm cách sửa.
Nên chạy trước khi hẹn lịch.

## Cấu trúc

```
ioeauto/
  api.py     đăng nhập, token, HTTP client, mã lỗi
  games.py   vòng luyện, session bài, sinh & kiểm tra đáp án
  dicts.py   kho từ (tần suất + toàn bộ) cho câu hỏi mở
  words.py   danh sách từ thông dụng
  asr.py     nghe mp3 của đề bằng Vosk (offline)
  capabilities.py  dò lớp nào còn sống, gợi ý khi bị vá
  discovery.py     tự dò endpoint mới (v3 -> v4)
  schema.py  fixture + phát hiện response đổi cấu trúc
  userscript.py    sinh userscript (phương án B)
  cdp.py     điều khiển Chrome/Edge qua DevTools, không cần thư viện
  vision.py  phương án C: chụp ảnh, chia lưới, AI bấm
  # bài nói: speak_answer() trong games.py (hợp đồng ftech: upload -> chấm -> request_id)
  planner.py kế hoạch theo hạn (plan)
  # lệnh phụ: watch, capture, weak, backup/restore, bank show|prune, plan
  proxy.py   addon mitmproxy
  cli.py     giao diện dòng lệnh
  gui.py     cửa sổ tkinter + khay hệ thống
  doctor.py  tự kiểm tra môi trường
  scheduler.py  tạo/xoá lịch Task Scheduler - cron
  paths.py   thư mục làm việc (chạy source hay .exe)
run.py       điểm vào
data/        session.json, answers.json, en10k.txt, words_alpha.txt
```

## Ghi chú

* Đây là công cụ học tập/ngâm cứu API; dùng cho tài khoản của bạn.
* Bài **nói** (type 12 và 13) đã chạy được bằng API, không cần micro: hợp đồng dò
  từ SDK của IOE — `extra_data` phải là **game token** (JWT, thiếu là 0 điểm) — `start_stream_v3` → `vad-file` → `stop_stream_v3` → `answercheck`
  với `ans` là `request_id` (không phải audio base64). Bật bằng `speaking.submit`
  hoặc `--with-speaking`.
* Giọng nói: mặc định `speaking.voice = "en-US-AriaNeural|-20%"` (neural, cần mạng,
  `pip install edge-tts`; tự lùi về SAPI khi mạng lỗi, kết quả cache trong
  `data/tts/`). Giọng neural hơn giọng SAPI cũ 2-3% điểm chấm từng câu
  (98,6 so với 94 của giọng SAPI). Game cho ghi **2 lần** nên đặt thêm
  `speaking.alt_voice` (vd. `en-US-MichelleNeural`) — tool ghi cả hai và nộp bản
  điểm cao hơn. Đặt `""` để quay lại giọng SAPI offline; đo bằng
  `python tools/bench_speak.py`.
  Trần thực tế của bài nói hiện ~600/1000 vì một nửa số câu có `Point: 0`.
* Bài **điền từ giấu cả từ** (type 2, nội dung chỉ là `******` hoặc `___` + `numTChar`)
  không có đáp án trong payload. Ba lớp: (1) bank theo câu, (2) **AI gợi ý ứng
  viên** (`aihint`, bật mặc định khi có khoá AI — AI chỉ đề xuất, `answercheck`
  vẫn chấm từng từ nên sai không lọt), (3) dò song song toàn bộ từ cùng độ dài
  (tới 12.000 từ, 400 giây/câu) khi hai lớp trên không xong. Đáp án đúng lưu vào
  bank theo câu nên các vòng sau tức thì: bài test từ 0 lên **100/100 trong 38
  giây** sau 2 lượt. Tắt bằng `"aihint": {"enabled": false}`.
* Bài **nghe - chép** (type 1) được chấm theo tỉ lệ từ khớp nên thường 40–70%;
  ngưỡng pass là ~70% nên có bài phải chạy lại vài lần mới qua.
* Các dạng còn lại (điền từ, sắp xếp, nối cặp, trắc nghiệm, điền ký tự) đạt 90–100
  nhờ đáp án server gửi kèm và oracle `answercheck`.
* Nếu IOE đổi cấu trúc, chỉ cần sửa `ioeauto/games.py`: endpoint nằm ở đầu file,
  dạng câu hỏi nằm trong `candidates()`.

## Kiểm thử

```bash
pip install pytest
python -m pytest tests -q      # 17 test: sinh đáp án, ASR căn chỗ trống, config, report, multi-account
```

## Ghi chú về `re/`

`re/` giữ lại các artefact dùng lúc reverse-engineering (bundle JS của trang
học sinh đã beautify, HTML, script của game cocos, repo tham khảo IOEAuto).
Không cần cho việc chạy, nhưng tiện khi cần dò lại endpoint nếu IOE đổi code.
