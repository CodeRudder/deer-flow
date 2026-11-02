#!/usr/bin/env python3
"""dressup-tryon 一条命令换装试穿（沙箱内可执行，零第三方依赖）。

针对「人物图 + 服装图 → N 个姿势的模特上身图」全流程：素材上传（multipart
字段名 ``file`` 单数）→ 台账复用（同 source_media_id/media_id 的模特/服装条目
直接引用，**定妆照不重建**——定妆照一次约 50s，是主要提速点）→ 建试穿（同步
台账）→ generate 异步作业（提交 → 轮询 tryon 详情到终态）→ VLM 衣着保真质检
（FAIL 且有 suggest 自动 retry，≤max-retries 次）→ 产物按姿势落盘 out-dir。

凭证全部来自环境变量（由平台注入当前用户凭证，per-user 隔离）：

- ``MEDIA_GATEWAY_BASE_URL``  MGW API 基址（必填；支持逗号分隔多候选地址，
  如 ``http://172.17.0.1:8000,http://192.168.1.10:8000``，启动时 /health 探测
  按序取第一个可用者，全挂则报错列出候选清单）
- ``MEDIA_GATEWAY_TOKEN``     当前用户 Bearer 令牌（必填，勿打日志、勿外传）

用法（SKILL.md 的「执行方法」小节）::

    python /mnt/skills/public/dressup-tryon/scripts/tryon.py \
      --person /mnt/user-data/uploads/person.jpg \
      --garment /mnt/user-data/uploads/garment.jpg \
      --poses front side back \
      --out-dir /mnt/user-data/outputs/dressup/<slug>

退出码 0 = 全部姿势生成完成（QC 可能含 pass=false 的说明项）；1 = 有姿势彻底失败。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MODELS_PATH = "/v1/dressup/models"
GARMENTS_PATH = "/v1/dressup/garments"
GARMENTS_FROM_MEDIA_PATH = "/v1/dressup/garments/from_media"
TRYONS_PATH = "/v1/dressup/tryons"
MEDIA_PATH = "/v1/media"

POSES = ("front", "side", "back", "stool", "walking", "bag")
DEFAULT_POSES = ("front", "side", "back")  # 单模特×单服装×3 姿势电商上身图
MAX_QC_RETRIES = 3
PENDING_STATUSES = {"queued", "running", "processing", "pending", "generating", "created"}
POLL_INTERVAL = 3.0
DEFAULT_TIMEOUT = 300.0  # 一次试穿生成实测约 1 分钟；定妆照约 50s


def slugify(text: str, fallback: str = "tryon") -> str:
    slug = re.sub(r"[\s/\\]+", "-", text.strip()).strip("-.")
    return slug[:64] or fallback


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def resolve_base_url(raw: str) -> str:
    """解析 MEDIA_GATEWAY_BASE_URL（支持逗号分隔多候选），/health 探测取第一个可用。

    单地址也探测一次（timeout 2s）；全部不可达时报错退出并列出候选清单。
    """
    candidates: list[str] = []
    for part in raw.split(","):
        url = part.strip().rstrip("/")
        if url and url not in candidates:
            candidates.append(url)
    if not candidates:
        return ""
    for url in candidates:
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{url}/health", method="GET"), timeout=2.0) as resp:
                if resp.status < 400:
                    log(f"MGW 地址探测：选定 {url}（候选 {len(candidates)} 个）")
                    return url
        except Exception as exc:  # noqa: BLE001 - 探测失败换下一个候选
            log(f"MGW 地址不可达：{url}（{exc}）")
    log(f"错误：MGW 候选地址全部不可达（{len(candidates)} 个）：{', '.join(candidates)}")
    raise SystemExit(1)


class Gateway:
    """MGW HTTP 最小封装（标准库 urllib；JSON + multipart + Bearer）。"""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _auth(self, req: urllib.request.Request) -> None:
        req.add_header("Authorization", f"Bearer {self.token}")

    def request(self, method: str, path: str, *, payload: dict | None = None, timeout: float = 30.0) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        self._auth(req)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"MGW {method} {path} -> HTTP {exc.code}: {detail}") from exc

    def upload(self, path: str, filename: str, data: bytes, *, fields: dict | None = None, timeout: float = 120.0) -> dict:
        """multipart/form-data 上传（素材库契约：文件字段名 ``file`` 单数）。"""
        boundary = "----deerflow" + uuid.uuid4().hex
        parts = []
        for name, value in (fields or {}).items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
            )
        parts.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
            + data
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        req = urllib.request.Request(f"{self.base_url}{path}", data=b"".join(parts), method="POST")
        self._auth(req)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"MGW POST {path} -> HTTP {exc.code}: {detail}") from exc

    def fetch_bytes(self, url: str, timeout: float = 120.0) -> bytes:
        req = urllib.request.Request(url if re.match(r"^https?://", url) else f"{self.base_url}{url}", method="GET")
        self._auth(req)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()


def sniff_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "png"


# ── 台账复用 ──────────────────────────────────────────────────────────────
def find_model(gw: Gateway, *, source_media_id: str) -> dict | None:
    items = gw.request("GET", MODELS_PATH).get("items") or []
    return next((m for m in items if m.get("source_media_id") == source_media_id), None)


def find_garment(gw: Gateway, *, media_id: str) -> dict | None:
    items = gw.request("GET", GARMENTS_PATH).get("items") or []
    return next((g for g in items if g.get("media_id") == media_id), None)


def wait_portrait(gw: Gateway, model_id: str, *, deadline: float) -> dict:
    """轮询定妆照回填（portrait_status=done）；失败/超时不拦流程，仅 portrait_url 为空。"""
    while True:
        model = gw.request("GET", f"{MODELS_PATH}/{model_id}")
        status = str(model.get("portrait_status") or "")
        if status in ("done", "failed", "skipped"):
            return model
        if time.monotonic() > deadline:
            log("  定妆照轮询超时（不拦流程，portrait_url 可能为空）")
            return model
        time.sleep(POLL_INTERVAL)


def wait_tryon(gw: Gateway, tryon_id: str, *, deadline: float) -> dict:
    """幂等回填：轮询 tryon 详情到终态（status 离开 generating/created）。"""
    while True:
        detail = gw.request("GET", f"{TRYONS_PATH}/{tryon_id}")
        status = str(detail.get("status") or "")
        if status not in PENDING_STATUSES:
            return detail
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**detail, "status": "timeout"}
        log(f"    tryon {tryon_id} status={status}，剩余 {remaining:.0f}s")
        time.sleep(min(POLL_INTERVAL, max(1.0, remaining)))


def generate_pose(gw: Gateway, tryon_id: str, *, pose: str, is_preset: bool, timeout: float) -> dict:
    payload = {"output": "image", **({"pose": pose} if is_preset else {"pose_text": pose})}
    gw.request("POST", f"{TRYONS_PATH}/{tryon_id}/generate", payload=payload)
    return wait_tryon(gw, tryon_id, deadline=time.monotonic() + timeout)


def qc_loop(gw: Gateway, tryon_id: str, *, max_retries: int, timeout: float) -> dict:
    """VLM 质检闭环：FAIL 且有 suggest → 自动 retry 重出，≤max_retries 次。

    网关侧重试超限返回 409 conflict（计数含网关自身口径）→ 视为终态，转人工。
    """
    qc = gw.request("POST", f"{TRYONS_PATH}/{tryon_id}/qc", payload={})
    retries = 0
    while not qc.get("pass") and retries < max_retries and str(qc.get("suggest") or "").strip():
        retries += 1
        log(f"    QC FAIL（{qc.get('suggest')}）→ 第 {retries}/{max_retries} 次自动重出…")
        try:
            gw.request("POST", f"{TRYONS_PATH}/{tryon_id}/retry", payload={})
        except RuntimeError as exc:
            if "HTTP 409" in str(exc):  # 网关判重试超限：终止闭环，保留建议转人工
                log(f"    网关重试超限（409）：{exc}")
                qc = {**qc, "pass": False, "suggest": f"{qc.get('suggest')}（网关重试超限，需人工调整）"}
                break
            raise
        detail = wait_tryon(gw, tryon_id, deadline=time.monotonic() + timeout)
        if str(detail.get("status")) == "timeout":
            break
        qc = gw.request("POST", f"{TRYONS_PATH}/{tryon_id}/qc", payload={})
    detail = gw.request("GET", f"{TRYONS_PATH}/{tryon_id}")
    return {**qc, "retries": retries, "status": detail.get("status"), "outputs": detail.get("outputs") or []}


def pose_one(gw: Gateway, *, model_id: str, garment_id: str, pose: str, is_preset: bool, max_retries: int, timeout: float, out_dir: Path, index: int) -> dict:
    result: dict = {"pose": pose, "tryon_id": None, "status": "failed", "qc_pass": False, "retries": 0, "file": None, "suggest": "", "missing": []}
    tryon = gw.request("POST", TRYONS_PATH, payload={"model_id": model_id, "garment_id": garment_id})
    result["tryon_id"] = tryon["id"]
    detail = generate_pose(gw, tryon["id"], pose=pose, is_preset=is_preset, timeout=timeout)
    if str(detail.get("status")) != "completed":
        result["status"] = str(detail.get("status"))
        result["suggest"] = json.dumps(detail.get("error") or {}, ensure_ascii=False)[:300]
        return result
    qc = qc_loop(gw, tryon["id"], max_retries=max_retries, timeout=timeout)
    result.update(status=str(qc.get("status")), qc_pass=bool(qc.get("pass")), retries=int(qc.get("retries") or 0), suggest=str(qc.get("suggest") or ""), missing=list(qc.get("missing") or []))
    for output in qc.get("outputs") or []:
        url = str(output.get("url") or "")
        if not url:
            continue
        data = gw.fetch_bytes(url)
        dest = out_dir / f"{index:02d}-{slugify(pose)}.{sniff_ext(data)}"
        dest.write_bytes(data)
        result["file"] = str(dest)
        result["url"] = url
        log(f"  姿势 {pose} 完成 → {dest}（QC {'PASS' if result['qc_pass'] else 'FAIL'}，重试 {result['retries']} 次）")
        break
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dressup-tryon：一条命令生成模特上身图（上传/台账复用/生成/QC 闭环全封装）")
    parser.add_argument("--person", help="人物图路径（/mnt/user-data/…；与 --person-media-id 二选一）")
    parser.add_argument("--garment", help="服装图路径（与 --garment-media-id 二选一）")
    parser.add_argument("--person-media-id", help="已入库人物素材 media_id（med_…）")
    parser.add_argument("--garment-media-id", help="已入库服装素材 media_id（med_…）")
    parser.add_argument("--model-name", default="模特", help="模特台账名（查重复用键之一）")
    parser.add_argument("--model-desc", default="", help="模特描述（建议：年龄·身材·脸型·发型 四件套；缺省从图生成）")
    parser.add_argument("--garment-name", default="服装", help="服装台账名")
    parser.add_argument("--garment-keywords", nargs="*", default=None, help="服装属性关键词（缺省走网关自动 VLM 属性分析）")
    parser.add_argument("--poses", nargs="*", default=list(DEFAULT_POSES), help=f"姿势预设（{POSES}；缺省 {' '.join(DEFAULT_POSES)}）")
    parser.add_argument("--pose-text", nargs="*", default=[], help="自定义姿势描述（白名单外姿势；可与 --poses 混用）")
    parser.add_argument("--max-retries", type=int, default=MAX_QC_RETRIES, help="QC 失败自动重出上限（缺省 3）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="每次生成/重出的轮询总超时秒（缺省 300）")
    parser.add_argument("--out-dir", default=None, help="产物目录（缺省 /mnt/user-data/outputs/dressup/<时间戳>）")
    args = parser.parse_args(argv)

    base_url = resolve_base_url(os.environ.get("MEDIA_GATEWAY_BASE_URL", ""))
    token = os.environ.get("MEDIA_GATEWAY_TOKEN", "")
    if not base_url or not token:
        log("错误：环境变量 MEDIA_GATEWAY_BASE_URL / MEDIA_GATEWAY_TOKEN 未设置（应由平台注入当前用户凭证）")
        return 1

    poses = [(p, True) for p in (args.poses or [])] + [(p, False) for p in (args.pose_text or [])]
    if not poses:
        parser.error("至少需要一个姿势（--poses 或 --pose-text）")
    for pose, is_preset in poses:
        if is_preset and pose not in POSES:
            parser.error(f"pose 白名单外：{pose}（{POSES}）；自定义姿势请用 --pose-text")
    if not (args.person or args.person_media_id) or not (args.garment or args.garment_media_id):
        parser.error("人物与服装输入各需一个：--person/--person-media-id 与 --garment/--garment-media-id")

    gw = Gateway(base_url, token)
    out_dir = Path(args.out_dir) if args.out_dir else Path("/mnt/user-data/outputs/dressup") / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    # ① 素材上传（缺 media_id 时）
    person_media_id = args.person_media_id
    if not person_media_id:
        data = Path(args.person).read_bytes()
        person_media_id = gw.upload(MEDIA_PATH, Path(args.person).name, data, fields={"kind": "images"})["id"]
        log(f"人物图已入库：{person_media_id}")
    garment_media_id = args.garment_media_id
    if not garment_media_id:
        data = Path(args.garment).read_bytes()
        garment_media_id = gw.upload(MEDIA_PATH, Path(args.garment).name, data, fields={"kind": "images"})["id"]
        log(f"服装图已入库：{garment_media_id}")

    # ② 台账复用 + 建档
    existing_model = find_model(gw, source_media_id=person_media_id)
    if existing_model:
        model = existing_model
        log(f"复用已有模特：{model['id']}（{model.get('name')}）——定妆照不重建")
    else:
        model = gw.request("POST", MODELS_PATH, payload={"name": args.model_name, "desc": args.model_desc, "source_media_id": person_media_id})
        log(f"新建模特：{model['id']}，等待定妆照（约 50s）…")
        model = wait_portrait(gw, model["id"], deadline=time.monotonic() + args.timeout)
        log(f"  定妆照状态：{model.get('portrait_status')}")
    existing_garment = find_garment(gw, media_id=garment_media_id)
    if existing_garment:
        garment = existing_garment
        log(f"复用已有服装：{garment['id']}（{garment.get('name')}）")
    elif args.garment_keywords:
        # 契约：keywords 是单个字符串（≤4000），空格/顿号分隔的类目·颜色·材质词
        garment = gw.request("POST", GARMENTS_PATH, payload={"name": args.garment_name, "gtype": "onmodel", "media_id": garment_media_id, "keywords": " ".join(args.garment_keywords)})
        log(f"新建服装：{garment['id']}")
    else:
        garment = gw.request("POST", GARMENTS_FROM_MEDIA_PATH, payload={"media_id": garment_media_id, "name": args.garment_name})
        log(f"新建服装（自动属性分析）：{garment['id']} keywords={garment.get('keywords')}")

    # ③④⑤ 逐姿势：建试穿 → 生成 → QC 闭环
    images = []
    for index, (pose, is_preset) in enumerate(poses, 1):
        log(f"姿势 {index}/{len(poses)}：{pose}")
        images.append(pose_one(gw, model_id=model["id"], garment_id=garment["id"], pose=pose, is_preset=is_preset, max_retries=max(0, args.max_retries), timeout=args.timeout, out_dir=out_dir, index=index))

    summary = {
        "model_id": model["id"],
        "model_reused": existing_model is not None,
        "garment_id": garment["id"],
        "garment_reused": existing_garment is not None,
        "out_dir": str(out_dir),
        "files": [i["file"] for i in images if i.get("file")],
        "tryon_ids": [i["tryon_id"] for i in images],
        "images": images,
        "passed": all(i["status"] == "completed" for i in images),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
