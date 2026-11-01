#!/usr/bin/env python3
"""t2i-series 一条命令生成主题组图（沙箱内可执行，零第三方依赖）。

针对一组 prompt（或 主题 + 张数 + 统一风格段）逐张提交媒体网关（MGW）文生图
作业，前台同步等待完成并把图片字节写入 out-dir（01.png / 02.png …）。MGW 是
异步作业制（提交 202 → 轮询 GET /v1/images/jobs/{id} → completed 后从
data[].url 取产物），本脚本在进程内封装全部提交/轮询/下载——**不要**在沙箱里
另起后台进程或手写驱动脚本。

凭证全部来自环境变量（由平台注入当前用户凭证）：

- ``MEDIA_GATEWAY_BASE_URL``     MGW API 基址（必填）
- ``MEDIA_GATEWAY_TOKEN``        当前用户 Bearer 令牌（必填）
- ``MEDIA_GATEWAY_IMAGE_MODEL``  缺省档位（--mode 未给时使用）

用法（SKILL.md 的「执行方法」小节）::

    python /mnt/skills/public/t2i-series/scripts/generate_series.py \
      --theme "城市黄昏系列" --count 2 --style "统一插画风，低饱和暖色调" \
      --out-dir /mnt/user-data/outputs/t2i-series/city-dusk

退出码 0 = 全部张成功；1 = 有张失败（摘要 JSON 里逐张列出）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SUBMIT_PATH = "/v1/images/generations"
JOB_PATH_TEMPLATE = "/v1/images/jobs/{job_id}"

# qwen-image-std 实测约 40-60s/张；总超时按张数放大，缺省 480s/张。
DEFAULT_TIMEOUT_PER_IMAGE = 480.0
POLL_INTERVAL = 3.0
PENDING_STATUSES = {"queued", "running", "processing", "pending"}
COUNT_MIN, COUNT_MAX = 1, 8

DEFAULT_STYLE = "统一插画风，柔和自然光，低饱和暖色调"
DEFAULT_ASPECT_RATIO = "16:9"

# 叙事节拍：缺省逐张画面骨架（开篇→入场→高潮→收束，张数多时循环收束）。
BEATS = ("开篇定场", "主体入场", "冲突/高潮", "收束定格")


def slugify(text: str, fallback: str = "series") -> str:
    """主题 → 文件名安全 slug（保留中日文，替换空白与路径符号）。"""
    slug = re.sub(r"[\s/\\]+", "-", text.strip()).strip("-.")
    return slug[:64] or fallback


def build_prompt(theme: str, index: int, count: int, subject: str, style: str) -> str:
    """合成单张提示词：统一风格段 + 主题 + 该张节拍（全组共享风格段）。"""
    beat = BEATS[min(index - 1, len(BEATS) - 1)] if count > 1 else ""
    parts = [style.strip(), f"主题：{theme.strip()}"]
    if subject:
        parts.append(f"画面：{subject.strip()}")
    elif beat:
        parts.append(f"画面：第{index}张（{beat}）：与主题一致的主体")
    return "；".join(p for p in parts if p)


def idempotency_key(theme: str, index: int, attempt: int, prompt: str) -> str:
    """每张每次尝试一个幂等键：同 key 同体重用；重试换 key 不撞 409。

    主题只进 SHA 摘要不进明文——HTTP 头只允许 ASCII。
    """
    theme_slug = hashlib.sha256(theme.encode("utf-8")).hexdigest()[:8]
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    return f"t2i-series:{theme_slug}:img{index}:v{attempt}:{digest}"


class Gateway:
    """MGW HTTP 最小封装（标准库 urllib，Bearer + Idempotency-Key）。"""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, *, payload: dict | None = None, idempotency_key: str | None = None, timeout: float = 30.0) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        if idempotency_key:
            req.add_header("Idempotency-Key", idempotency_key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"MGW {method} {path} -> HTTP {exc.code}: {detail}") from exc

    def fetch_bytes(self, url: str, timeout: float = 120.0) -> bytes:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()


def sniff_ext(data: bytes) -> str:
    """按魔数定扩展名（MGW 产物可能是 PNG 或 JPEG 字节）。"""
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "png"


def wait_job(gw: Gateway, job_id: str, *, deadline: float, log) -> dict:
    """轮询单个作业到终态（同步前台循环，封装 MGW 异步语义）。"""
    path = JOB_PATH_TEMPLATE.format(job_id=job_id)
    while True:
        body = gw.request("GET", path)
        status = str(body.get("status") or "")
        if status not in PENDING_STATUSES:
            return body
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"status": "timeout", "id": job_id, "error": {"code": "timeout", "message": "轮询超时（--timeout）"}}
        log(f"    job {job_id} status={status}，剩余 {remaining:.0f}s")
        time.sleep(min(POLL_INTERVAL, max(1.0, remaining)))


def fetch_artifact_url(job: dict) -> str:
    """completed 作业体里的产物地址（``data[].url``，兼容 result.data 嵌套）。"""
    data = job.get("data")
    if not isinstance(data, list) and isinstance(job.get("result"), dict):
        data = job["result"].get("data")
    for item in data or []:
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return ""


def generate_one(gw: Gateway, *, index: int, prompt: str, mode: str, aspect_ratio: str, short_edge: int | None, theme: str, max_attempts: int, timeout: float, out_dir: Path, dry_run: bool, log) -> dict:
    """单张闭环：提交 → 轮询 → 下载；失败按次重试 ≤max_attempts。"""
    payload: dict = {"mode": mode, "prompt": prompt, "aspect_ratio": aspect_ratio, "output": "image"}
    if short_edge:
        payload["short_edge"] = short_edge
    result: dict = {"index": index, "file": None, "job_id": None, "status": "failed", "attempts": 0, "error": None}
    for attempt in range(1, max_attempts + 1):
        result["attempts"] = attempt
        key = idempotency_key(theme, index, attempt, prompt)
        if dry_run:
            log(f"  [dry-run] 第{index}张 尝试 {attempt}: POST {SUBMIT_PATH} key={key}")
            log(f"  [dry-run] payload={json.dumps(payload, ensure_ascii=False)}")
            result.update(status="dry-run", job_id="(dry-run)")
            return result
        try:
            log(f"  第{index}张 尝试 {attempt}/{max_attempts}: 提交作业…")
            body = gw.request("POST", SUBMIT_PATH, payload=payload, idempotency_key=key)
            job_id = str(body.get("id") or body.get("task_id") or "")
            if not job_id:
                raise RuntimeError(f"提交响应缺少作业 id: {json.dumps(body, ensure_ascii=False)[:300]}")
            result["job_id"] = job_id
            log(f"  第{index}张 job={job_id}，轮询至终态…")
            job = wait_job(gw, job_id, deadline=time.monotonic() + timeout, log=log)
            status = str(job.get("status") or "")
            if status == "completed":
                url = fetch_artifact_url(job)
                if not url:
                    raise RuntimeError("作业 completed 但 data[] 中没有产物 url")
                # MGW 的 data[].url 可能是相对路径（/v1/images/jobs/{id}/file?…），相对基址解析。
                absolute_url = url if re.match(r"^https?://", url) else f"{gw.base_url}{url}"
                data = gw.fetch_bytes(absolute_url)
                dest = out_dir / f"{index:02d}.{sniff_ext(data)}"
                dest.write_bytes(data)
                size = len(data)
                log(f"  第{index}张 完成 → {dest}（{size} bytes）")
                result.update(status="completed", file=str(dest))
                return result
            error = job.get("error") or {"code": status, "message": f"作业终态 {status}"}
            log(f"  第{index}张 失败：{json.dumps(error, ensure_ascii=False)[:300]}")
            result["error"] = error
        except Exception as exc:  # noqa: BLE001 - 单张失败重试，最后统一汇总
            result["error"] = {"code": "exception", "message": str(exc)[:500]}
            log(f"  第{index}张 异常：{exc}")
    result["status"] = "failed"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="t2i-series：一条命令生成主题组图（MGW 异步作业由脚本内部提交/轮询/下载）")
    parser.add_argument("--theme", help="主题一句话（与 --prompt-group 二选一）")
    parser.add_argument("--count", type=int, default=4, help="组图张数（1–8，缺省 4；仅 --theme 模式）")
    parser.add_argument("--style", default=DEFAULT_STYLE, help="统一风格段，拼进每张提示词开头（--theme 模式）")
    parser.add_argument("--prompt-group", nargs="+", help="逐张画面描述列表（每张一条，覆盖 --count/--style 组装）")
    parser.add_argument("--mode", default=os.environ.get("MEDIA_GATEWAY_IMAGE_MODEL", ""), help="MGW 档位（缺省 $MEDIA_GATEWAY_IMAGE_MODEL）")
    parser.add_argument("--aspect-ratio", default=DEFAULT_ASPECT_RATIO, help="全组统一画幅（缺省 16:9）")
    parser.add_argument("--short-edge", type=int, default=None, help="可选短边像素")
    parser.add_argument("--seed", type=int, default=None, help="可选种子（复现用；重试时自动 +1000*attempt+张号）")
    parser.add_argument("--out-dir", default=None, help="产物目录（缺省 /mnt/user-data/outputs/t2i-series/<slug>）")
    parser.add_argument("--max-attempts", type=int, default=3, help="每张最大尝试次数（缺省 3）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_PER_IMAGE, help="每张作业轮询总超时秒数（缺省 480）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将提交的请求，不实际提交")
    args = parser.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    base_url = os.environ.get("MEDIA_GATEWAY_BASE_URL", "")
    token = os.environ.get("MEDIA_GATEWAY_TOKEN", "")
    mode = args.mode
    if not mode:
        parser.error("--mode 未给且环境变量 MEDIA_GATEWAY_IMAGE_MODEL 为空")

    if args.prompt_group:
        prompts = list(args.prompt_group)
        theme = args.theme or prompts[0][:40]
        count = len(prompts)
    else:
        if not (args.theme or "").strip():
            parser.error("--theme 与 --prompt-group 至少提供一个")
        if not COUNT_MIN <= args.count <= COUNT_MAX:
            parser.error(f"--count 超出范围：{args.count}（{COUNT_MIN}–{COUNT_MAX}）")
        theme = args.theme
        count = args.count
        prompts = [build_prompt(theme, i, count, "", args.style) for i in range(1, count + 1)]

    out_dir = Path(args.out_dir) if args.out_dir else Path("/mnt/user-data/outputs/t2i-series") / slugify(theme)
    log(f"t2i-series: theme={theme!r} 张数={count} mode={mode} out-dir={out_dir}" + (" [dry-run]" if args.dry_run else ""))
    if args.dry_run:
        for i, prompt in enumerate(prompts, 1):
            log(f"  第{i}张 prompt: {prompt}")
            print(json.dumps({"mode": mode, "prompt": prompt, "aspect_ratio": args.aspect_ratio, "idempotency_key": idempotency_key(theme, i, 1, prompt)}, ensure_ascii=False))
        return 0

    if not base_url or not token:
        log("错误：环境变量 MEDIA_GATEWAY_BASE_URL / MEDIA_GATEWAY_TOKEN 未设置（应由平台注入当前用户凭证）")
        return 1
    gw = Gateway(base_url, token)
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    jobs: list[str] = []
    images: list[dict] = []
    for i, prompt in enumerate(prompts, 1):
        seed = None if args.seed is None else args.seed + 1000 + i
        result = generate_one(
            gw,
            index=i,
            prompt=prompt,
            mode=mode,
            aspect_ratio=args.aspect_ratio,
            short_edge=args.short_edge,
            theme=theme,
            max_attempts=max(1, args.max_attempts),
            timeout=args.timeout,
            out_dir=out_dir,
            dry_run=False,
            log=log,
        )
        if seed is not None:
            result["seed"] = seed
        images.append(result)
        if result.get("job_id"):
            jobs.append(str(result["job_id"]))
        if result.get("file"):
            files.append(str(result["file"]))

    summary = {
        "theme": theme,
        "mode": mode,
        "count": count,
        "out_dir": str(out_dir),
        "files": files,
        "job_ids": jobs,
        "images": images,
        "passed": len(files) == count,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
