"""Fill in missing OpenAI OCR outputs in ``data/outputs.json`` (local, no Modal).

This is a self-contained, standard-library-only adaptation of
``benchmarks/proprietary_cost_probe.py`` from the modal-model-experiments repo.
Instead of reading the page corpus from a Modal volume, it reads the real page
WebPs already committed under ``data/pages/`` and calls the OpenAI Responses
API directly, then *safely* merges the results back into ``data/outputs.json``.

Safety properties:
  * Only ever writes entries for OpenAI models — every other model/page is left
    byte-for-byte untouched.
  * Only overwrites an entry on a *successful* response. Failures leave the
    existing (placeholder) entry in place, so the run is resumable: re-run to
    retry only the pages that are still missing.
  * Backs up the current ``outputs.json`` into the gitignored ``data/archive/``
    before the first write, and writes atomically (temp file + ``os.replace``).
  * Checkpoints to disk periodically so a crash never loses completed pages.

Usage (run from the repo root, with OPENAI_API_KEY in the environment):

    # See what would run — no API calls, no writes:
    python data/refresh_openai.py --dry-run

    # Cheap smoke test: one cheap model, first 3 missing pages:
    python data/refresh_openai.py --models openai:gpt-4.1-mini --limit 3

    # Full fill of every OpenAI model on every missing page:
    python data/refresh_openai.py

    # Re-run every page (including the 2 already present) for uniformity:
    python data/refresh_openai.py --refresh-all
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent
REPO_ROOT = DATA_DIR.parent
OUTPUTS_PATH = DATA_DIR / "outputs.json"
ARCHIVE_DIR = DATA_DIR / "archive"

# Same prompt the original probe used for OpenAI (PROMPT_PROFILES["ocr-markdown"]).
PROMPT_TEXT = (
    "Please extract all text from this image. Preserve the reading order and "
    "reproduce tables as Markdown."
)
MAX_TOKENS = 8192
IMAGE_DETAIL = "high"
TIMEOUT_SECONDS = 300
OPENAI_URL = "https://api.openai.com/v1/responses"


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float


# USD per 1M tokens, standard (non-batch) tier — mirrors the snapshot in
# benchmarks/proprietary_cost_probe.py. Provider dashboards remain authoritative.
PRICES: dict[str, ModelPrice] = {
    "openai:gpt-5.5": ModelPrice(5.00, 30.00),
    "openai:gpt-5.5-pro": ModelPrice(30.00, 180.00),
    "openai:gpt-5.4": ModelPrice(2.50, 15.00),
    "openai:gpt-5.4-pro": ModelPrice(30.00, 180.00),
    "openai:gpt-5.4-mini": ModelPrice(0.75, 4.50),
    "openai:gpt-5.4-nano": ModelPrice(0.20, 1.25),
    "openai:gpt-4.1": ModelPrice(2.00, 8.00),
    "openai:gpt-4.1-mini": ModelPrice(0.40, 1.60),
    "openai:gpt-4.1-nano": ModelPrice(0.10, 0.40),
}


def supports_temperature(model_id: str) -> bool:
    """Mirror the probe's temperature gating for the GPT-5.x family."""
    key = model_id.strip().lower()
    if key.startswith("gpt-5.5"):
        return False
    if key == "gpt-5.4-pro":
        return False
    if key in {"gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano"}:
        return False
    return True


def strip_tags(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def cost_usd(input_tokens: int, output_tokens: int, price: ModelPrice | None) -> float | None:
    if price is None:
        return None
    return (
        input_tokens * price.input_per_million + output_tokens * price.output_per_million
    ) / 1_000_000.0


def parse_openai_body(body: dict[str, Any]) -> dict[str, Any]:
    usage = body.get("usage") or {}
    output_text = str(body.get("output_text") or "")
    if not output_text:
        chunks: list[str] = []
        for item in body.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("text"):
                    chunks.append(str(content["text"]))
        output_text = "\n".join(chunks)
    return {
        "output_text": output_text,
        "input_tokens": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        "output_tokens": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def call_openai(model_id: str, image_b64: str, api_key: str) -> dict[str, Any]:
    """POST one page to the Responses API with retries. Returns a result dict.

    On success: {"ok": True, output_text, input_tokens, output_tokens,
                 total_tokens, latency_s}. On failure: {"ok": False, "error": ...}.
    """
    payload: dict[str, Any] = {
        "model": model_id,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/webp;base64,{image_b64}",
                        "detail": IMAGE_DETAIL,
                    },
                    {"type": "input_text", "text": PROMPT_TEXT},
                ],
            }
        ],
        "max_output_tokens": MAX_TOKENS,
    }
    if supports_temperature(model_id):
        payload["temperature"] = 0

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_error = ""
    started = time.perf_counter()
    for attempt in range(1, 5):
        req = urllib.request.Request(OPENAI_URL, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            parsed = parse_openai_body(body)
            return {"ok": True, "latency_s": time.perf_counter() - started, **parsed}
        except urllib.error.HTTPError as exc:
            try:
                preview = exc.read().decode("utf-8")[:300]
            except Exception:
                preview = "<unreadable>"
            last_error = f"HTTP {exc.code}: {preview}"
            if exc.code in {408, 409, 429, 500, 502, 503, 504} and attempt < 4:
                retry_after = exc.headers.get("retry-after") if exc.headers else None
                try:
                    delay = max(0.0, min(300.0, float(retry_after))) if retry_after else min(60.0, 2.0 ** attempt)
                except (TypeError, ValueError):
                    delay = min(60.0, 2.0 ** attempt)
                time.sleep(delay)
                continue
            break
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = f"request failed: {exc}"
            if attempt < 4:
                time.sleep(min(30.0, 2.0 ** attempt))
                continue
            break
    return {"ok": False, "latency_s": time.perf_counter() - started, "error": last_error}


def load_outputs() -> dict[str, Any]:
    doc = json.loads(OUTPUTS_PATH.read_text())
    if not isinstance(doc, dict) or not isinstance(doc.get("pages"), list):
        raise SystemExit(f"{OUTPUTS_PATH} is not a valid viewer outputs file")
    return doc


def is_real(entry: Any) -> bool:
    return bool(entry) and bool(entry.get("text") or entry.get("html"))


def atomic_write(doc: dict[str, Any]) -> None:
    tmp = OUTPUTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, OUTPUTS_PATH)


def build_entry(result: dict[str, Any], price: ModelPrice | None) -> dict[str, Any]:
    text = str(result.get("output_text") or "")
    inp = int(result.get("input_tokens") or 0)
    out = int(result.get("output_tokens") or 0)
    return {
        "html": text if text else None,
        "text": strip_tags(text) if text else None,
        "latency_ms": round(float(result.get("latency_s") or 0.0) * 1000.0, 1),
        "tokens": out,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": int(result.get("total_tokens") or (inp + out)),
        "cost_usd": cost_usd(inp, out, price),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="", help="comma list of openai:<model>; default = all OpenAI models in outputs.json")
    ap.add_argument("--workers", type=int, default=24, help="concurrent requests (default 24)")
    ap.add_argument("--limit", type=int, default=0, help="max pages per model (0 = all missing)")
    ap.add_argument("--refresh-all", action="store_true", help="re-run pages that already have real output")
    ap.add_argument("--dry-run", action="store_true", help="report planned work; no API calls, no writes")
    ap.add_argument("--no-backup", action="store_true", help="skip the pre-write backup into data/archive/")
    ap.add_argument("--checkpoint-every", type=int, default=20, help="flush outputs.json after N successful pages")
    args = ap.parse_args()

    doc = load_outputs()
    all_openai = [m for m in doc.get("models", []) if m.startswith("openai:")]
    if args.models:
        wanted = [m.strip() for m in args.models.split(",") if m.strip()]
        for m in wanted:
            if m not in all_openai:
                raise SystemExit(f"model {m!r} is not an OpenAI model present in outputs.json: {all_openai}")
        models = wanted
    else:
        models = all_openai
    if not models:
        raise SystemExit("no OpenAI models to process")

    # Build the work list: (page_index, page_id, model) for entries needing data.
    work: list[tuple[int, str, str]] = []
    per_model_count: dict[str, int] = {m: 0 for m in models}
    for m in models:
        n = 0
        for i, page in enumerate(doc["pages"]):
            entry = page.get("outputs", {}).get(m)
            if args.refresh_all or not is_real(entry):
                if args.limit and n >= args.limit:
                    break
                work.append((i, str(page["id"]), m))
                n += 1
        per_model_count[m] = n

    print("Planned work (pages needing OpenAI output):")
    for m in models:
        print(f"   {m:<22} {per_model_count[m]}")
    print(f"   TOTAL requests: {len(work)}")
    pro = [m for m in models if "pro" in m]
    if pro:
        print(f"   NOTE: {', '.join(pro)} are premium-priced ($30/$180 per 1M tok) — expect higher cost.")

    if args.dry_run:
        print("\n(dry run — no API calls, no writes)")
        return
    if not work:
        print("\nNothing to do.")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set in the environment")

    # Pre-flight backup.
    if not args.no_backup:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = ARCHIVE_DIR / f"outputs.before-openai-refresh.{stamp}.json"
        shutil.copy2(OUTPUTS_PATH, backup)
        print(f"\nBacked up current outputs.json -> {backup.relative_to(REPO_ROOT)}")

    # Pre-read image bytes once per unique page (shared across models).
    img_cache: dict[str, str] = {}

    def image_b64_for(page_index: int) -> str:
        page = doc["pages"][page_index]
        pid = str(page["id"])
        if pid not in img_cache:
            img_path = REPO_ROOT / page.get("image", f"data/pages/{pid}.webp")
            img_cache[pid] = base64.b64encode(Path(img_path).read_bytes()).decode("ascii")
        return img_cache[pid]

    done = 0
    ok = 0
    fail = 0
    since_ckpt = 0
    start = time.time()

    def task(item: tuple[int, str, str]) -> tuple[tuple[int, str, str], dict[str, Any]]:
        page_index, _pid, model = item
        model_id = model.split(":", 1)[1]
        result = call_openai(model_id, image_b64_for(page_index), api_key)
        return item, result

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(task, item) for item in work]
        for fut in as_completed(futures):
            (page_index, pid, model), result = fut.result()
            done += 1
            if result.get("ok"):
                price = PRICES.get(model)
                doc["pages"][page_index].setdefault("outputs", {})[model] = build_entry(result, price)
                ok += 1
                since_ckpt += 1
            else:
                fail += 1
                print(f"   FAIL {model} {pid}: {result.get('error','')[:160]}")
            if since_ckpt >= max(1, args.checkpoint_every):
                atomic_write(doc)
                since_ckpt = 0
            if done % 10 == 0 or done == len(work):
                rate = done / max(1e-9, time.time() - start)
                print(f"   {done}/{len(work)} done (ok={ok} fail={fail}) ~{rate:.1f}/s", flush=True)

    atomic_write(doc)
    print(f"\nWrote {OUTPUTS_PATH.relative_to(REPO_ROOT)} — {ok} pages filled, {fail} failed.")
    if fail:
        print("Re-run the same command to retry only the still-missing pages.")


if __name__ == "__main__":
    main()
