# echoloom · 回声织

插上录音笔，一天的声音自动变成一份结构化的每日总结。

检测到 JNN 录音笔挂载后，自动复制录音、按整点切分跨小时音频、弹出设备、转录、LLM 润色、清理音频并生成每日总结；有产出后自动拉起本地回顾界面。

## 目录结构

```
echoloom/
├── config.example.yaml    # 配置模板（复制为 config.yaml 后填写）
├── LICENSE                # MIT
├── main.py                # 入口，轮询挂载并驱动流程（asyncio）
├── web.py                 # 入口，起本地可视化界面（main.py 也会自动拉起它）
├── pipeline/
│   ├── config.py          # 配置加载与校验
│   ├── watcher.py         # 挂载点检测 + 弹出设备
│   ├── copier.py          # 复制录音 + 状态去重
│   ├── splitter.py        # 定时长切分 + 可读时间标签
│   ├── client.py          # 转录（whisper / omni）+ LLM 客户端（aiohttp）
│   ├── process.py         # 转录 → 润色 → 每日 summary
│   └── web.py             # summary 列表/正文/搜索/标记接口（aiohttp.web）
├── static/index.html      # 单文件界面：logo + 分月侧栏 + 搜索 + 正文渲染 + 勾选
├── static/logo.svg        # 界面 logo（同时用作 favicon）
├── data/                  # raw / split / text / summary / state.json / marks.json
├── docs/                  # 设计与实验记录
├── .github/workflows/     # CI（pytest）
└── tests/
```

> **平台限制**：本项目针对 **macOS + JNN 录音笔**。挂载检测、`diskutil eject`、
> 顶层自动拉起界面都按 macOS 假设实现，换平台需自行替换对应部分。

## 使用

依赖用 [uv](https://docs.astral.sh/uv/) 管理，Python 版本由 `.python-version` 固定：

```bash
cp config.example.yaml config.yaml   # 复制模板，填好接口与密钥
uv sync                 # 安装依赖（自动创建 .venv）
uv run main.py          # 前台持续运行（挂载后自动处理）
uv run main.py --once   # 只跑一轮
uv run web.py           # 浏览器里回顾每日 summary（自动开标签页，默认 127.0.0.1:34100）
uv run web.py --no-browser   # 只起服务，不开浏览器
uv run pytest           # 跑测试
```

## 回顾界面

`uv run web.py` 会起服务并自动打开浏览器。左侧按天倒序列出全部 summary，**按月分组**（月份头吸顶），顶部搜索框做**全文检索**（服务端 `/api/search` 扫正文，不是只搜侧栏那一行预览），命中后显示「筛出 X / Y 天」。右侧渲染正文，`←` `→` 或按钮往前后翻天。

**只有「3. 待办事项」这一节能勾**：`render()` 跟踪当前小节标题，命中待办小节时才给列表项挂 `data-key`，界面据此在左侧生成方格按钮。其余三节（事件与任务、想法与灵感、其他值得记录的内容）是只读的分点列表。

方格只有**两个状态**：未完成（空框）/ 完成（✓ + 划线置灰），点一下切换，再点取消。状态存 `data/marks.json`：

```json
{"20261004": {"确认包是否需要修改": "done"}}
```

**退出后一定保留** —— 每次勾选都立刻 `POST /api/mark` 落盘（先写 `.tmp` 再 `os.replace` 原子替换），不是攒在浏览器里。实测：标记 2 条 → 杀掉服务 → 重启 → 页面刷新，勾选仍在。侧栏日期后的 `✓N` 是当天完成条数。键是条目正文本身（剥掉 markdown 记号后），服务端只当不透明字符串存；summary 被 LLM 重写后正文变了，旧标记就自然失效，不会错位。summary 是 LLM 产出的裸 Markdown，格式并不统一 —— 标题有 `###`/`####`/纯加粗三种，列表有 `-`/`*` 与两级缩进（缩进宽度也不一致），有的带客套开场、`---` 分隔和 `> 备注`，还有一份被 `max_tokens` 截断在半个列表项上。待办小节里 LLM 写的 `[ ]` 任务列表标记会被去掉，因为界面上已有真方格。`static/index.html` 里的 `render()` 按行把这几种形态归一化，接口只吐原文不做转换。

## 模型配置

### 统一用一个多模态模型（omni）

在 `config.yaml` 顶层写一个 `omni` 段，它**同时顶替**下面的转录和 LLM 两套配置：

```yaml
omni:
  url: http://host:18301/v1/chat/completions   # OpenAI 兼容
  api_key: ...
  model: qwen3-omni-30b-a3b-fp8
  prompt: "请把这段录音逐字转写为简体中文文本，不要翻译、总结或添加说明。"
  max_tokens: 8192
  timeout: 1800
  retries: 3
```

音频走 `input_audio`（base64），文本走普通 `text` 内容。删掉或注释这一段即回退到下面两套独立配置。

> 服务端需支持音频：vLLM 要装 `vllm[audio]`，否则 `input_audio` 返回 500 `Please install vllm[audio] for audio support`。

**两个硬约束，配置时注意：**

1. **单次音频时长上限 600 秒**（vLLM 的 `VLLM_MAX_AUDIO_DECODE_DURATION_S` 默认值）。超了直接返回 400 拒收，不会静默截断。这决定了 `audio.segment_seconds` 必须 ≤ 600。
2. **上下文上限**。实测 omni 的音频约 13.4 tokens/秒，`max_model_len=32768` 扣掉 `max_tokens` 后约能吃 35 分钟。即便把上面的时长上限放开，段长也不宜超过 30 分钟。

另外 omni 遇到非语音会输出括号描述（`（键盘敲击声）`），纯音乐段可能「听」出歌词。vLLM 在 `temperature=0` 下也不是完全确定的，同一段录音两次转录可能不同。

### 分开配置（无 omni 段时）

| 用途 | 接口 | 说明 |
| --- | --- | --- |
| 转录 | `POST /v1/audio/transcriptions` | OpenAI 兼容，multipart 流式上传 wav |
| LLM | `POST /v1/chat/completions` | 用于润色与每日总结 |

## 复读退化防护

omni 在非语音片段上容易陷入复读循环（反复输出同一字、同一句，或输出带时间戳的重复行）。两道防线：

- 请求侧：`omni.frequency_penalty`（默认 0.5）抑制循环。实测 `repetition_penalty` 对此无效。调参集（20260930，19 段）上判废数 3 → 1 → 0 对应 fp 0.2 / 0.5 / 1.0，取 0.5 是为了留出余地 —— fp 更高虽然判废归零，但有可能是把正常的高频重复也一并罚掉造成的假象。
- 结果侧：`_degenerate_reason()` 判废后返回空串，交由上层按「转录为空」处理。三种判据：
  - `finish_reason == "length"`（顶满 `max_tokens`）
  - 最长连续同字符 ≥ 20（抓「哔哔哔」「拜拜拜」）
  - zlib 压缩比 < 0.2（抓被标点/时间戳隔开的重复，如 `[00:00] 你` × 120）

阈值取自 30 段真实录音的实测分布：退化段 0.029~0.192，正常段最低 0.361，中间留有余量。短文本无需额外保护，zlib 头部开销会让它们的比值自然高于阈值。

判废走的是「返回空串」而不是抛异常：这样该段仍会被标记为已处理，坏文本不落盘，也不会因为一直失败而卡住当天的 summary 生成。

## 安全提示

回顾界面**没有任何鉴权**，默认只监听 `127.0.0.1`。若把 `web.host` 改成 `0.0.0.0`
或某个内网/公网地址（如经 Tailscale 暴露），任何能访问该地址的人都能读取全部
summary 并改写 `marks.json`，请自行加反向代理与鉴权。

`config.yaml` 含接口密钥，已在 `.gitignore` 中，切勿提交。

## 依赖

- Python 3.12+（`uv` 会自动下载）
- `aiohttp`、`pyyaml`
- `ffmpeg` / `ffprobe`（`brew install ffmpeg`）

## 拆分规则

录音文件名 `YYYYMMDDHHMMSS.WAV` 即起始时间。所有录音最终都按日期归档到 `data/split/<YYYYMMDD>/`：
- 未超过 `audio.segment_seconds`：原样复制（保持原名）
- 超过：按固定段长切开，输出以每段起始时间命名：`<原文件名>__<段起始时间>.WAV`

段长上限由 `audio.segment_seconds` 给定，默认 600 秒（omni 服务端单次音频上限）。改用 whisper 时可以调回 3600。

## 设备弹出与音频清理

单条原始录音可达 576MB，而文本只有几百字节，所以流程里有两处回收：

- **弹出设备**（`mount.eject_after_copy`，默认开）：复制并删除源文件后，确认 `RECORD` 目录下已无未取走的录音，才执行 `diskutil eject /Volumes/JNN`。本轮没有新文件时不弹出。
- **清理音频**（`storage.delete_audio_after_process`，默认开）：一条录音的**全部分片**都转录+润色完成后，删除它的 `data/raw/<源名>.WAV` 与所有分片，只留 `data/text` 文本。任一分片失败则整条录音的音频都保留，便于重试。

两个开关设为 `false` 可保留全部音频（磁盘换重试能力）。

## 自动拉起回顾界面

一轮流程里**转录+润色处理了音频、或生成了新的每日 summary** 之后，若回顾界面没在跑，`main.py` 会自动在后台拉起一个（`web.auto_start`，默认开）：

```yaml
web:
  host: 127.0.0.1
  port: 34100
  auto_start: true      # 有产出后自动拉起
  open_browser: true    # 拉起就开浏览器，手动和自动一个行为；临时不想开用 --no-browser
```

先探端口，占用就跳过，不会重复拉起；子进程 `start_new_session=True` 脱离流水线独立活着，日志追加到 `data/web.log`。手动 `uv run web.py` 和自动拉起读的是同一份配置，行为一致。服务不会随流水线退出而关闭，用完 `pkill -f "web.py"` 停掉。

## state.json 里的路径

`state.json` 中 `processed` 与 `split` 存的都是**相对路径**（相对 `data/split/`），跟项目目录无关，整个目录搬家后仍然有效。`copied.src` 例外，记的是设备上的源文件位置。

历史数据里 `split` 曾存过绝对路径，指向项目搬家前的旧目录，早已失效；`StateStore` 载入时会按切分件的命名规则（`YYYYMMDDHHMMSS[__段起始].WAV`，跨小时的段落进第二天目录）重建为相对路径。

## License

[MIT](LICENSE)
