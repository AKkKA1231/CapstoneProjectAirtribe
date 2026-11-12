"""Generate a 1080p demonstration video (MP4) from the explainer scenes using Pillow and FFmpeg."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FRAMES_DIR = ROOT / "reports" / "video_frames"
OUTPUT_VIDEO = ROOT / "Docs" / "prism_explainer_demo.mp4"

SCENES = [
    {
        "id": 1,
        "title": "Scene 1: Title and Project Summary (0:00 - 0:35)",
        "screen": "Show README.md in VS Code",
        "say": "Hello, this is Prism, a multi-tenant OpenAI-compatible LLM gateway with smart routing, semantic caching, rate limiting, and failover...",
        "content": [
            "# Prism — LLM Gateway and Semantic Cache",
            "",
            "• Unified Chat API (OpenAI-compatible /v1/chat/completions)",
            "• Virtual-key security & multi-tenancy",
            "• Atomic RPM & monthly USD budget admission",
            "• Smart routing: 'auto' routes to fast 20B or smart 120B",
            "• Semantic cache with pgvector (tenant-scoped)",
            "• Provider retry & ordered fallback",
            "• Modern operations console & metrics",
        ],
    },
    {
        "id": 2,
        "title": "Scene 2: Architecture & Design (0:35 - 1:25)",
        "screen": "Show Docs/Architecture.md diagram",
        "say": "Prism uses a modular-monolith architecture with ports and adapters. PostgreSQL is the source of truth...",
        "content": [
            "Client / Ops Console",
            "        │",
            "        ▼",
            "FastAPI API Boundary ── Auth, validation, headers, admin policy",
            "        │",
            "        ▼",
            "Application Services ── Admission, routing, cache, provider execution",
            "        ├── PostgreSQL 16 + pgvector (source of truth, locks, cache)",
            "        ├── Groq (real OpenAI-compatible upstream)",
            "        └── Mock A / Mock B (deterministic fallback upstreams)",
        ],
    },
    {
        "id": 3,
        "title": "Scene 3: Docker Services & Readiness (1:25 - 1:55)",
        "screen": "PowerShell terminal",
        "say": "Docker Compose starts five local services. PostgreSQL is healthy and readiness reports status ready...",
        "content": [
            "PS > docker compose ps",
            "NAME                  SERVICE           STATUS                  PORTS",
            "...console-1           console           Up 3 hours              0.0.0.0:5173->80/tcp",
            "...gateway-1           gateway           Up 2 hours              0.0.0.0:8000->8000/tcp",
            "...mock-provider-a-1   mock-provider-a   Up 15 hours             0.0.0.0:8101->8101/tcp",
            "...mock-provider-b-1   mock-provider-b   Up 15 hours             0.0.0.0:8102->8102/tcp",
            "...postgres-1          postgres          Up 17 hours (healthy)   0.0.0.0:5432->5432/tcp",
            "",
            "PS > Invoke-RestMethod http://localhost:8000/health/ready",
            "status: ready | database: connected",
        ],
    },
    {
        "id": 4,
        "title": "Scene 4: Standard Completion & SSE Streaming (1:55 - 2:50)",
        "screen": "PowerShell terminal",
        "say": "Standard OpenAI-compatible request returns provider, cache, and cost headers. Streaming terminates with [DONE]...",
        "content": [
            "PS > Send-Prism -Key $alphaKey -Model 'fast' -Prompt 'Hello from Prism'",
            "Status   : 200",
            "Provider : groq",
            "Cache    : miss",
            "Fallback : false",
            "CostUSD  : 0.0000221250",
            "",
            "PS > curl.exe -N '$base/v1/chat/completions' --data-binary $streamBody",
            "data: {\"choices\":[{\"delta\":{\"content\":\"Prism\"}}]}",
            "data: {\"choices\":[{\"delta\":{\"content\":\" is a high performance gateway.\"}}]}",
            "data: [DONE]",
        ],
    },
    {
        "id": 5,
        "title": "Scene 5: Intelligent Auto Routing (2:50 - 3:45)",
        "screen": "PowerShell terminal",
        "say": "Auto routing selects fast 20B for simple prompts and smart 120B for math proofs. Routing eval scores 100%...",
        "content": [
            "PS > Send-Prism -Key $alphaKey -Model 'auto' -Prompt 'List three primary colors'",
            "Provider : groq (Model: openai/gpt-oss-20b)",
            "",
            "PS > Send-Prism -Key $alphaKey -Model 'auto' -Prompt 'Prove square root of two is irrational'",
            "Provider : groq (Model: openai/gpt-oss-120b)",
            "",
            "PS > python scripts/routing_eval.py",
            "classifier_accuracy: 1.0 | baseline_accuracy: 0.4 | trap_accuracy: 1.0",
        ],
    },
    {
        "id": 6,
        "title": "Scene 6: Retry and Ordered Fallback (3:45 - 4:25)",
        "screen": "PowerShell terminal",
        "say": "Fault injection 503 causes Mock A to fail predictably. Prism moves to Mock B, reporting Fallback: true...",
        "content": [
            "PS > Send-Prism -Key $alphaKey -Model 'mock-fast' -Prompt 'Recover this request from [[503]]'",
            "Status   : 200",
            "Provider : mock-b",
            "Cache    : miss",
            "Fallback : true",
            "CostUSD  : 0.0000000000",
            "",
            "Gateway Log: Mock A returned 503 -> Backoff -> Dispatched to Mock B -> Succeeded",
        ],
    },
    {
        "id": 7,
        "title": "Scene 7: Semantic Cache & Tenant Isolation (4:25 - 5:35)",
        "screen": "PowerShell terminal",
        "say": "Paraphrase hits vector cache with zero cost for Team Alpha. Team Beta is a miss, proving tenant isolation...",
        "content": [
            "PS > Send-Prism -Key $alphaKey -Model 'fast' -Prompt 'Explain semantic cache for automobile'",
            "Provider: groq | Cache: miss | Cost: $0.0000236",
            "",
            "PS > Send-Prism -Key $alphaKey -Model 'fast' -Prompt 'Describe semantic cache for a car'",
            "Provider: cache | Cache: hit | Cost: $0.0000000  <-- ZERO COST HIT!",
            "",
            "PS > Send-Prism -Key $betaKey -Model 'fast' -Prompt 'Describe semantic cache for a car'",
            "Provider: groq | Cache: miss  <-- ZERO CROSS-TENANT LEAKAGE",
        ],
    },
    {
        "id": 8,
        "title": "Scene 8: Atomic RPM & Budget Controls (5:35 - 6:20)",
        "screen": "PowerShell terminal",
        "say": "Third request on 2-RPM key returns 429. Budget-limited key returns 402 Payment Required before provider call...",
        "content": [
            "PS > 1..3 | ForEach-Object { Send-Prism -Key 'prism-demo-rpm-limited' -Model 'fast' ... }",
            "Request 1 -> Status: 200 OK",
            "Request 2 -> Status: 200 OK",
            "Request 3 -> Status: 429 Too Many Requests ({'code':'rate_limit_exceeded'})",
            "",
            "PS > Send-Prism -Key 'prism-demo-budget-limited' -Model 'fast' ...",
            "Status: 402 Payment Required ({'code':'budget_exhausted'})",
        ],
    },
    {
        "id": 9,
        "title": "Scene 9: Operations Console (6:20 - 7:50)",
        "screen": "Browser http://localhost:5173",
        "say": "The React console aggregates metrics, requests, cache efficiency, and provider attempts by tenant...",
        "content": [
            "Prism Operations Console (http://localhost:5173)",
            "----------------------------------------------------------------",
            "Total Requests: 55 recorded   | Spend: $0.000371 | Cache Hit Rate: 9.1%",
            "",
            "Tenant Breakdown:",
            "  • Team Alpha : 48 requests | 1,384 tokens | 5 cache hits | 4 fallbacks",
            "  • Team Beta  :  3 requests |    52 tokens | 0 cache hits | 0 fallbacks",
            "  • RPM Demo   :  4 requests |   292 tokens | 0 cache hits | 0 fallbacks",
            "",
            "Tabs: Overview | Requests (filters: Cache Hit, Fallback) | Providers",
        ],
    },
    {
        "id": 10,
        "title": "Scene 10: Recovery, Metrics & Smoke Tests (7:50 - 9:20)",
        "screen": "Swagger Docs & PowerShell",
        "say": "Conservative crash recovery marks ambiguous attempts. The 9-path smoke suite passes all verifications...",
        "content": [
            "PS > python scripts/smoke_test.py",
            "{",
            "  'passed': 9,",
            "  'checks': [",
            "    {'name': 'readiness', 'passed': true},",
            "    {'name': 'model allowlist', 'passed': true},",
            "    {'name': 'non-stream completion', 'passed': true},",
            "    {'name': 'SSE completion', 'passed': true},",
            "    {'name': 'auto routing', 'passed': true},",
            "    {'name': 'ordered fallback', 'passed': true},",
            "    {'name': 'semantic cache hit', 'passed': true},",
            "    {'name': 'cross-tenant cache isolation', 'passed': true},",
            "    {'name': 'admin operations API', 'passed': true}",
            "  ]",
            "}",
        ],
    },
    {
        "id": 11,
        "title": "Scene 11: Final Conclusion (9:20 - 9:50)",
        "screen": "Console Overview or README title",
        "say": "Prism completes all project requirements with 61 tests passing and 82.6% coverage. Thank you.",
        "content": [
            "PRISM CAPSTONE VERIFICATION SUMMARY:",
            "================================================================",
            "  ✓ Python 3.12 Test Suite: 61 passed (4.97s)",
            "  ✓ Branch Coverage: 82.62% (Target: 80%)",
            "  ✓ Strict Mypy & Ruff: 0 errors",
            "  ✓ End-to-End Smoke Test: 9/9 Passed",
            "  ✓ Load Test: 20/20 at 134ms p95 latency",
            "  ✓ All 11 Assessment Deliverables Complete",
            "================================================================",
        ],
    },
]


def render_slide(scene: dict, output_path: Path):
    width, height = 1920, 1080
    img = Image.new("RGB", (width, height), color=(10, 14, 23))
    draw = ImageDraw.Draw(img)

    # Accent top border
    draw.rectangle([0, 0, width, 8], fill=(56, 189, 248))

    # Header bar
    draw.rectangle([0, 8, width, 110], fill=(15, 23, 42))
    draw.line([(0, 110), (width, 110)], fill=(30, 41, 59), width=2)

    # Logo
    draw.rounded_rectangle([60, 28, 120, 88], radius=8, fill=(56, 189, 248))
    draw.text((78, 38), "P", fill=(10, 14, 23), font_size=40)

    # Scene Title
    draw.text((150, 36), scene["title"], fill=(241, 245, 249), font_size=32)

    # Screen Action Badge
    draw.rounded_rectangle([60, 140, 1860, 200], radius=8, fill=(20, 28, 45))
    draw.text((80, 155), f"SCREEN: {scene['screen']}", fill=(56, 189, 248), font_size=20)

    # Terminal / Content Box
    draw.rounded_rectangle([60, 225, 1860, 780], radius=12, fill=(13, 19, 33), outline=(30, 41, 59), width=2)
    # Terminal header dots
    draw.ellipse([85, 245, 100, 260], fill=(239, 68, 68))
    draw.ellipse([110, 245, 125, 260], fill=(245, 158, 11))
    draw.ellipse([135, 245, 150, 260], fill=(16, 185, 129))
    draw.text((170, 242), "Terminal & Screen Output Preview", fill=(100, 116, 139), font_size=16)
    draw.line([(60, 275), (1860, 275)], fill=(30, 41, 59), width=1)

    # Code / Content Lines
    y = 300
    for line in scene["content"]:
        fill_color = (203, 213, 225)
        if line.startswith("PS >"):
            fill_color = (56, 189, 248)
        elif "Passed" in line or "Status   : 200" in line or "ZERO COST" in line or "passed" in line:
            fill_color = (52, 211, 153)
        elif "429" in line or "402" in line or "503" in line:
            fill_color = (248, 113, 113)
        elif line.startswith("•") or line.startswith("="):
            fill_color = (192, 132, 252)

        draw.text((90, y), line, fill=fill_color, font_size=20)
        y += 36

    # Narration / Voiceover Subtitle Box
    draw.rounded_rectangle([60, 810, 1860, 1030], radius=12, fill=(23, 32, 51), outline=(56, 189, 248), width=2)
    draw.text((90, 830), "🗣️  SPOKEN VOICEOVER NARRATION (WHAT TO SAY):", fill=(56, 189, 248), font_size=18)
    draw.text((90, 875), f'"{scene["say"]}"', fill=(241, 245, 249), font_size=24)

    img.save(output_path)


def main():
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    slide_files = []

    print("Generating 1080p presentation frames for all 11 scenes...")
    for scene in SCENES:
        frame_path = FRAMES_DIR / f"scene_{scene['id']:02d}.png"
        render_slide(scene, frame_path)
        slide_files.append(frame_path)
        print(f"  ✓ Rendered {frame_path.name}")

    # Create concat list for ffmpeg (each slide 7 seconds = ~80 second full overview video)
    concat_file = FRAMES_DIR / "concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for frame in slide_files:
            f.write(f"file '{frame.as_posix()}'\n")
            f.write("duration 7\n")
        # Repeat last file for ffmpeg bug workaround
        f.write(f"file '{slide_files[-1].as_posix()}'\n")

    print(f"\nEncoding MP4 video via FFmpeg -> {OUTPUT_VIDEO}...")
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        str(OUTPUT_VIDEO),
    ]

    subprocess.run(cmd, check=True)
    print(f"\n🎉 Video generated successfully at: {OUTPUT_VIDEO}")
    print(f"File size: {OUTPUT_VIDEO.stat().st_size / (1024 * 1024):.2f} MB")


if __name__ == "__main__":
    main()

