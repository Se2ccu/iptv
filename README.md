# 中文 IPTV 自建订阅（台标 + 中文名 + EPG）

用 [iptv-org](https://github.com/iptv-org/iptv) 当前可播放的中文源
（`https://iptv-org.github.io/iptv/languages/zho.m3u`）作为**节目流**，
用 [fanmingming/live](https://github.com/fanmingming/live) 作为**台标库与 EPG**，
自动生成一份带中文频道名、台标、节目单的 M3U 订阅，并用 GitHub Pages 发布。

> 能，完全可以。本项目就是把“找源 + 配名 + 配台标 + 发布”这一串自动化了。

## 目录结构

```
.
├── build.py                  # 生成脚本（仅用 Python 标准库）
├── data/
│   └── overrides.json        # 手工校正表（缺名/繁体/别名）
├── docs/
│   ├── index.html            # 网页播放器（GitHub Pages）
│   └── playlist.m3u          # 由 build.py 生成
└── .github/workflows/build.yml   # 每天 CI 重建并发布
```

## 工作原理

1. 抓取 `zho.m3u` → 得到每个频道的**直播流 URL**、原始 `tvg-id`、原始台标。
2. 抓取 iptv-org `channels.json` → 取每个频道的 `alt_names` 里的**中文名**
   （如 `CCTV-1 综合`、`湖南卫视`、`TVB翡翠台`）。
3. 抓取 fanmingming 的 `index.m3u / ipv6.m3u / itv.m3u` → 建立
   **台标文件名索引**和 **EPG id 集合**。
4. 对每个频道：
   - 中文名：`overrides.json` → `alt_names` → 英文名；
   - 台标：先查 fanmingming 台标库，命中则用 `https://live.fanmingming.cn/tv/<名>.png`，
     否则回退 iptv-org 原始 logo；
   - EPG：匹配到的频道设 `tvg-id` 为 fanmingming 的 EPG id，头部写 `x-tvg-url`；
   - 分组：央视 / 卫视 / 地方 / 港澳台 / 海外。
5. 同一频道有多条流时，默认只保留画质最高的一条（`--all-streams` 可全留）。
6. 输出 `docs/playlist.m3u`，同时 `docs/index.html` 是一个基于 hls.js 的网页播放器。

## 本地生成

```bash
python3 build.py                      # 生成 docs/playlist.m3u
python3 build.py --probe-logos        # 对未命中的台标逐个探测（较慢，CI 用）
python3 build.py --all-streams        # 保留同一频道全部流
python3 build.py --no-epg             # 不写 EPG
```

## 部署到自己的 GitHub

1. 把本目录推到你自己的仓库（公开仓库即可）。
2. **Settings → Pages → Build and deployment → Source** 选 `GitHub Actions`
   （本仓库已带 `build.yml`，会自动构建并部署 `docs/`）。
3. 等首次 Action 跑完后，你的发布链接就是：

   - 订阅地址（给 IPTV App / PotPlayer / VLC）：
     `https://<你的用户名>.github.io/<仓库名>/playlist.m3u`
   - 网页播放器：
     `https://<你的用户名>.github.io/<仓库名>/`

4. 想更新台标/中文名，编辑 `data/overrides.json` 后提交即可，CI 会自动重建。

> 也可以直接用 raw 链接（更新有约 5 分钟缓存）：
> `https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/docs/playlist.m3u`

## 订阅示例

PotPlayer：`打开 → 打开URL →` 填上面的 `playlist.m3u` 链接；
或装 IPTV 类 App（如 影视仓 / DIYP / TiviMate / IPTV Smarters）→ 添加 M3U 订阅 → 粘贴链接。

## 注意事项

- **http 源与网页播放器**：很多中文源是 `http://`，而 GitHub Pages 是 `https://`，
  浏览器会因“混合内容”拦截；这类源只能用**本地播放器/App**订阅播放，网页播放器
  主要用于 https 的 `.m3u8` 源。
- **CORS**：即便源是 https，部分源服务器不允许跨域，hls.js 也播不了；本地播放器无此限制。
  所以推荐：**网页看可播的，整体订阅用 App**。
- **地域与时效**：iptv-org 的源会失效、会变更，CI 每天重建可保持新鲜；但具体某条能否播放
  取决于你所在网络与地区。
- **台标与 EPG 来源**：台标/EPG 均来自 fanmingming/live，EPG 由 112114.xyz 分发，
  准确性以原项目为准。
- **合法性**：本项目不存储任何流媒体，仅整理公开元数据；使用后果由使用者自负。

## 调整

- `data/overrides.json` 里键是 iptv-org 的频道基础 ID（`tvg-id` 去掉 `@SD/@HD` 后的部分），
  可填 `name`（中文名）、`logo`（台标文件名，不含 `.png`）、`epg`（EPG id）、`group`（分组）。
- 想换台标域名（如改成 raw 直链）改 `build.py` 里的 `FM_LOGO_BASE`。
- 想加/换源，可直接改 `IPTV_M3U_URL` 指向其它 m3u。
