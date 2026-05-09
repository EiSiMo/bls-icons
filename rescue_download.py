"""Rescue: download the 6.3GB batch output via openai SDK streaming + retry."""
import os, sys, time, httpx
from pathlib import Path
from openai import OpenAI

KEY = open("C:/Users/moritz/Documents/act-img-gen/.env").read().split("OPENAI_API_KEY=")[1].split()[0]
FILE_ID = "file-Fp5WNtaRTtYjcWQUuTBQk1"
OUT = Path("C:/Users/moritz/Documents/act-img-gen/output/comic_v4/_batches/batch_69feb0845b108190a896638eb6242baa_output.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

# Long timeouts everywhere
client = OpenAI(api_key=KEY, timeout=httpx.Timeout(connect=30, read=900, write=60, pool=30), max_retries=0)

print(f"Downloading {FILE_ID} -> {OUT}")
print(f"  expected size: 6.29 GB")

for attempt in range(1, 11):
    print(f"\n--- attempt {attempt} ---", flush=True)
    t0 = time.time()
    try:
        resp = client.files.content(FILE_ID)
        n = 0
        with OUT.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=4*1024*1024):
                f.write(chunk)
                n += len(chunk)
                mb = n / 1024 / 1024
                if int(mb) % 100 == 0 and int(mb) != getattr(resp, "_last", -1):
                    setattr(resp, "_last", int(mb))
                    elapsed = time.time() - t0
                    rate = mb / elapsed if elapsed else 0
                    print(f"  {mb:.0f} MB ({elapsed:.0f}s, {rate:.1f} MB/s)", flush=True)
        elapsed = time.time() - t0
        print(f"\nDONE: {n/1024/1024:.1f} MB in {elapsed:.0f}s")
        sys.exit(0)
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  FAIL after {elapsed:.0f}s: {type(e).__name__}: {str(e)[:200]}")
        if attempt < 10:
            wait = min(60 * attempt, 300)
            print(f"  retry in {wait}s …")
            time.sleep(wait)

sys.exit(1)
