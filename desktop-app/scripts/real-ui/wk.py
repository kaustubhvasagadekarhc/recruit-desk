import asyncio, json, sys
import websockets

class WK:
    """Drive the real app's page through WebKit's remote inspector (WEBKIT_INSPECTOR_HTTP_SERVER)."""
    def __init__(self, url="ws://127.0.0.1:9334/socket/1/1/WebPage"): self.url, self.n, self.tid = url, 0, None
    async def __aenter__(self):
        self.ws = await websockets.connect(self.url, max_size=None)
        # the page's target announces itself
        for _ in range(50):
            m = json.loads(await asyncio.wait_for(self.ws.recv(), 10))
            if m.get("method") == "Target.targetCreated":
                t = m["params"]["targetInfo"]
                if t.get("type") == "page": self.tid = t["targetId"]; break
        if not self.tid: raise RuntimeError("no page target")
        return self
    async def __aexit__(self, *a): await self.ws.close()
    async def call(self, method, **params):
        self.n += 1; i = self.n
        inner = json.dumps({"id": i, "method": method, "params": params})
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": "Target.sendMessageToTarget", "params": {"targetId": self.tid, "message": inner}}))
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), 60))
            if m.get("method") == "Target.dispatchMessageFromTarget":
                r = json.loads(m["params"]["message"])
                if r.get("id") == i:
                    if "error" in r: raise RuntimeError(r["error"])
                    return r.get("result")
    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if r.get("wasThrown"): raise RuntimeError(r)
        return r["result"].get("value")

async def main():
    async with WK() as w:
        print(await w.js("document.title"))
        print(await w.js("document.querySelector('#nav-version')?.textContent"))
        print(await w.js("[...document.querySelectorAll('.tab:not(.hidden)')].map(t=>t.textContent.trim()).join(' | ')"))
if __name__ == "__main__": asyncio.run(main())
