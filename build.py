#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — 基于 iptv-org 的中文电视源生成带中文频道名、台标、EPG 的 M3U 订阅文件。

数据来源：
  1. iptv-org 当前可播放的中文源：  https://iptv-org.github.io/iptv/languages/zho.m3u
  2. iptv-org 频道元数据（含中文 alt_names）：https://iptv-org.github.io/api/channels.json
  3. fanmingming/live 台标库与 EPG：  https://github.com/fanmingming/live
     - 台标： live.fanmingming.cn/tv/<name>.png
     - EPG ： live.fanmingming.cn/e.xml

只依赖 Python 标准库。适合在 GitHub Actions 里定时运行。
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OVERRIDES_PATH = ROOT / "data" / "overrides.json"

IPTV_M3U_URL = "https://iptv-org.github.io/iptv/languages/zho.m3u"
IPTV_CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
FM_M3U_URLS = [
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/index.m3u",
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv6.m3u",
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/itv.m3u",
]
FM_LOGO_BASE = "https://live.fanmingming.cn/tv/"
FM_RAW_LOGO_BASE = "https://raw.githubusercontent.com/fanmingming/live/main/tv/"
FM_EPG_URL = "https://live.fanmingming.cn/e.xml"

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 仅用于在多个 alt_names 里挑“更像常用简体名”的启发式：含以下字符视为繁体，降权。
TRAD_CHARS = set("電視臺灣華資訊購物亞電影綜藝節目頻道聯網際數碼紀實劇場衛視港澳原住民客家大愛好消息東森三立中天年代非凡緯來靖天龍華鏡寰宇")


# --------------------------- 网络抓取 --------------------------- #
def fetch_text(url, tries=4, timeout=40):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (build.py)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"抓取失败 {url}: {last}")


def head_ok(url, timeout=15):
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0 (build.py)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


# --------------------------- M3U 解析 --------------------------- #
EXTINF_RE = re.compile(
    r'^#EXTINF:-1(?P<attrs>[^,]*),(?P<name>.*)$'
)
ATTR_RE = re.compile(r'(?P<k>[\w-]+)="(?P<v>[^"]*)"')


def parse_m3u(text):
    """返回 list[dict]：tid, group, logo, name, attrs(dict), opts[(k,v)], url"""
    entries = []
    cur = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            m = EXTINF_RE.match(line)
            if not m:
                continue
            attrs = {k: v for k, v in ATTR_RE.findall(m.group("attrs"))}
            cur = {
                "tid": attrs.get("tvg-id", ""),
                "logo": attrs.get("tvg-logo", ""),
                "group": attrs.get("group-title", ""),
                "name": m.group("name").strip(),
                "attrs": attrs,
                "opts": [],
                "url": "",
            }
        elif line.startswith("#EXTVLCOPT:") or line.startswith("#EXTHTTP:"):
            if cur is None:
                continue
            if line.startswith("#EXTVLCOPT:"):
                kv = line[len("#EXTVLCOPT:"):].split("=", 1)
                cur["opts"].append((kv[0], kv[1] if len(kv) > 1 else ""))
            else:
                cur["opts"].append(("__http", line[len("#EXTHTTP:"):]))
        elif line.startswith("#"):
            # 其它注释忽略
            continue
        else:
            if cur is None:
                continue
            cur["url"] = line
            entries.append(cur)
            cur = None
    return entries


# --------------------------- fanmingming 目录 --------------------------- #
def build_fm_catalog():
    """从 fanmingming 的 m3u 里抽出台标文件名集合与 tvg-name -> (stem, group) 映射、EPG id 集合。"""
    logo_index = set()
    name_to_stem = {}
    epg_ids = set()
    for url in FM_M3U_URLS:
        try:
            text = fetch_text(url)
        except Exception as e:
            print(f"⚠ 跳过 {url}: {e}", file=sys.stderr)
            continue
        for e in parse_m3u(text):
            tn = e["attrs"].get("tvg-name", "").strip()
            logo = e["logo"]
            stem = ""
            if logo:
                stem = logo.rsplit("/", 1)[-1]
                stem = urllib.parse.unquote(stem)
                if stem.lower().endswith(".png"):
                    stem = stem[:-4]
                if stem:
                    logo_index.add(stem)
            if tn:
                epg_ids.add(tn)
                if stem and tn not in name_to_stem:
                    name_to_stem[tn] = stem
    return logo_index, name_to_stem, epg_ids


# --------------------------- 名称解析 --------------------------- #
def has_cjk(s):
    return bool(CJK_RE.search(s))


def norm_stem(s):
    """把中文显示名归一化为可能的台标文件名。"""
    if not s:
        return ""
    # CCTV / CETV / CGTN / CHC 等英文台标
    m = re.match(r'^(CCTV|CETV|CGTN|CHC|CND)[-\s]?(\d+)?\+?', s, re.I)
    if m:
        brand = m.group(1).upper()
        num = m.group(2)
        if num:
            return f"{brand}{num}"
        return brand
    s2 = s.strip()
    return s2


def name_candidates(name):
    """生成台标匹配候选（精确、去空格、去常见后缀、CCTV 归一）。"""
    cands = []
    if not name:
        return cands
    cands.append(name)
    ns = re.sub(r"\s+", "", name)
    cands.append(ns)
    # 去掉常见后缀词
    for suf in ["频道", "电视台", "电视", "综合频道", "新闻综合频道", "台"]:
        if name.endswith(suf) and len(name) > len(suf):
            cands.append(name[: -len(suf)])
    # CCTV 类归一
    nm = norm_stem(name)
    if nm and nm not in cands:
        cands.append(nm)
    # 去掉中间空格/横线后的纯英文前缀（如 "TVB翡翠台" 保留；"CGTN 环球商业" -> CGTN）
    m = re.match(r'^([A-Z]{2,})\s', name)
    if m:
        cands.append(m.group(1))
    return cands


def find_logo_stem(name, logo_index, name_to_stem):
    for c in name_candidates(name):
        if c in logo_index:
            return c
        # 大小写不敏感（ASCII）
        low = c.lower()
        for stem in logo_index:
            if stem.lower() == low:
                return stem
    # 直接按显示名映射
    if name in name_to_stem:
        return name_to_stem[name]
    return None


def pick_best_alt(alts):
    """从 iptv-org alt_names 里挑一个中文显示名。优先：含简体字多的、然后短名。"""
    cn = [a for a in alts if has_cjk(a)]
    if not cn:
        return None

    def trad_score(a):
        return sum(1 for ch in a if ch in TRAD_CHARS)

    cn.sort(key=lambda a: (trad_score(a), len(a)))
    return cn[0]


# --------------------------- 分组与排序 --------------------------- #
GROUP_ORDER = ["央视频道", "卫视频道", "地方频道", "港澳台", "海外频道", "其他"]


def classify(base_id, name, group):
    country = base_id.rsplit(".", 1)[-1] if "." in base_id else ""
    bid = base_id.lower()
    if any(bid.startswith(p) for p in ("cctv", "cgtv", "cetv", "chc", "cnd", "cgt")) or "CGTN" in name:
        return "央视频道"
    if "卫视" in name or "satellite" in bid:
        return "卫视频道"
    if country in ("hk", "tw", "mo"):
        return "港澳台"
    if country == "cn":
        return "地方频道"
    return "海外频道"


def group_rank(g):
    try:
        return GROUP_ORDER.index(g)
    except ValueError:
        return len(GROUP_ORDER)


def channel_sort_key(name):
    """央视按数字、卫视按拼音(近似用名字)、其余按名字排序。"""
    m = re.match(r'^(?:CCTV-?)(\d+)', name)
    if m:
        return (0, int(m.group(1)), name)
    return (1, 0, name)


# --------------------------- 流质量评分 --------------------------- #
QUAL_RE = re.compile(r'\((\d{3,4})[piP]?\)|4K|8K|2160p|1080p|720p|576p|480p', re.I)


def quality_score(entry):
    s = 0
    blob = f"{entry['name']} {entry['group']}".lower()
    if "4k" in blob or "8k" in blob or "2160" in blob:
        s += 5
    elif "1080" in blob:
        s += 4
    elif "720" in blob:
        s += 3
    elif "hd" in blob:
        s += 3
    elif "sd" in blob:
        s += 2
    else:
        s += 1
    if "geo-blocked" in blob or "geo blocked" in blob:
        s -= 5
    if "not 24/7" in blob:
        s -= 1
    return s


# --------------------------- 主流程 --------------------------- #
def load_channels():
    try:
        data = json.loads(fetch_text(IPTV_CHANNELS_URL))
        return {c["id"]: c for c in data}
    except Exception as e:
        print(f"⚠ 无法获取 iptv-org channels.json：{e}", file=sys.stderr)
        return {}


def resolve(meta, base_id, channel, logo_index, name_to_stem, epg_ids, args):
    ov = meta.get("overrides", {}).get(base_id, {})
    name = ov.get("name")
    if not name:
        if channel:
            alts = channel.get("alt_names", []) or []
            name = pick_best_alt(alts)
        if not name:
            name = channel.get("name", base_id) if channel else base_id
    name = name.strip()

    # EPG id
    epg = ov.get("epg")
    if not epg:
        if name in epg_ids:
            epg = name
        elif base_id in epg_ids:
            epg = base_id
        else:
            epg = ""
    # 兼容 fanmingming：央视 epg 用 CCTV1 这种；显示名若是 “CCTV-1 综合” 也归一
    if not epg:
        nm = norm_stem(name)
        if nm in epg_ids:
            epg = nm

    # 台标
    stem = ov.get("logo") or find_logo_stem(name, logo_index, name_to_stem)
    logo_url = ""
    if stem:
        logo_url = FM_LOGO_BASE + urllib.parse.quote(stem) + ".png"
    elif args.probe_logos:
        probe = FM_RAW_LOGO_BASE + urllib.parse.quote(name) + ".png"
        if head_ok(probe):
            logo_url = FM_LOGO_BASE + urllib.parse.quote(name) + ".png"

    group = ov.get("group") or classify(base_id, name, meta.get("group", ""))
    return name, epg, logo_url, group


def main():
    ap = argparse.ArgumentParser(description="生成带中文频道名/台标/EPG 的 M3U 订阅")
    ap.add_argument("-o", "--output", default=str(ROOT / "docs" / "playlist.m3u"))
    ap.add_argument("--all-streams", action="store_true", help="保留同一频道的全部流（默认仅保留画质最高的一条）")
    ap.add_argument("--probe-logos", action="store_true", help="对未命中的台标逐个 HEAD 探测（较慢）")
    ap.add_argument("--no-epg", action="store_true", help="不写入 x-tvg-url")
    args = ap.parse_args()

    overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    overrides.pop("_comment", None)

    print("→ 抓取 fanmingming 台标目录 …", file=sys.stderr)
    logo_index, name_to_stem, epg_ids = build_fm_catalog()
    print(f"  台标 {len(logo_index)} 个，EPG id {len(epg_ids)} 个", file=sys.stderr)

    print("→ 抓取 iptv-org 频道元数据 …", file=sys.stderr)
    channels = load_channels()
    print(f"  频道 {len(channels)} 个", file=sys.stderr)

    print("→ 抓取 iptv-org zho.m3u …", file=sys.stderr)
    zho = fetch_text(IPTV_M3U_URL)
    raw_entries = parse_m3u(zho)
    print(f"  流条目 {len(raw_entries)} 条", file=sys.stderr)

    # 按基础 id 聚合
    by_id = {}
    for e in raw_entries:
        base = e["tid"].split("@")[0] if e["tid"] else e["name"]
        by_id.setdefault(base, []).append(e)

    resolved = []
    name_ok = logo_ok = 0
    for base_id, items in by_id.items():
        channel = channels.get(base_id)
        sample = items[0]
        name, epg, logo_url, group = resolve(
            {"overrides": overrides, "group": sample["group"]},
            base_id, channel, logo_index, name_to_stem, epg_ids, args
        )
        if has_cjk(name) or name:
            if has_cjk(name):
                name_ok += 1
        if logo_url:
            logo_ok += 1

        # 选流
        if args.all_streams:
            chosen = sorted(items, key=quality_score, reverse=True)
        else:
            chosen = [max(items, key=quality_score)]

        for it in chosen:
            resolved.append({
                "base_id": base_id,
                "name": name,
                "epg": epg,
                "logo": logo_url or it["logo"],  # 没有台标时回退到 iptv-org 原始 logo
                "group": group,
                "url": it["url"],
                "opts": it["opts"],
            })

    # 排序
    resolved.sort(key=lambda r: (group_rank(r["group"]), channel_sort_key(r["name"]), r["name"]))

    # 写出
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        if args.no_epg:
            f.write("#EXTM3U\n")
        else:
            f.write(f'#EXTM3U x-tvg-url="{FM_EPG_URL}"\n')
        for r in resolved:
            tvg_id = r["epg"] or r["base_id"]
            attrs = [
                f'tvg-id="{tvg_id}"',
                f'tvg-name="{tvg_id}"',
            ]
            if r["logo"]:
                attrs.append(f'tvg-logo="{r["logo"]}"')
            attrs.append(f'group-title="{r["group"]}"')
            f.write("#EXTINF:-1 " + " ".join(attrs) + "," + r["name"] + "\n")
            for k, v in r["opts"]:
                if k == "__http":
                    f.write(v + "\n")
                else:
                    f.write(f"#EXTVLCOPT:{k}={v}\n")
            f.write(r["url"] + "\n")

    print(f"\n✅ 已生成 {out}", file=sys.stderr)
    print(f"   频道数：{len(resolved)}  有中文名：{name_ok}  有台标：{logo_ok}", file=sys.stderr)
    # 失败报告
    miss_name = [r["base_id"] for r in resolved if not has_cjk(r["name"])]
    miss_logo = [r["base_id"] for r in resolved if not r["logo"]]
    if miss_name:
        print(f"   无中文名({len(miss_name)})：{', '.join(sorted(set(miss_name))[:20])} …", file=sys.stderr)
    if miss_logo:
        print(f"   无台标({len(miss_logo)})：{', '.join(sorted(set(miss_logo))[:20])} …", file=sys.stderr)


if __name__ == "__main__":
    main()
