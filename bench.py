#!/usr/bin/env python3
"""AceTeam Benchmarks: auto-scored tasks against models served by Ollama.

Every task is checked by code (tests, exact answers, SQL results), never by a judge model.

Usage:
  python3 bench.py --haystack data/moby-dick.txt --models ornith-1.5:9b qwen3.8:27b --think off on

Suites:
  short   10 single-turn tasks: JSON extraction, 3 Python functions, 2 tool calls, math, SQL, regex, format
  long    recall over ~16k/32k/64k-token documents, 2 longer coding tasks, a multi-step agent bug fix
"""
import argparse, json, os, random, re, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.request

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/chat"
THINK = False


def chat(model, messages, ctx=8192, tools=None, timeout=1800):
    body = {"model": model, "messages": messages, "stream": False, "think": THINK,
            "options": {"temperature": 0, "num_ctx": ctx + (8192 if THINK else 0)}, "keep_alive": "10m"}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    r["_wall"] = time.time() - t
    r["_tps"] = r.get("eval_count", 0) / max(r.get("eval_duration", 1) / 1e9, 1e-9)
    return r


def ask(model, prompt, tools=None, ctx=8192):
    r = chat(model, [{"role": "user", "content": prompt}], ctx=ctx, tools=tools)
    return r["message"], r


def code_block(text):
    m = re.findall(r"```(?:python|py|sql)?\s*\n(.*?)```", text, re.S)
    return (m[-1] if m else text).strip()


def run_py(src, tests, timeout=20):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.py")
        open(p, "w").write(src + "\n\n" + tests + "\nprint('OK')\n")
        try:
            out = subprocess.run([sys.executable, "-I", p], capture_output=True, text=True, timeout=timeout, cwd=d)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        return out.stdout.strip().endswith("OK"), (out.stderr.strip().splitlines() or [""])[-1][:140]


# ---------------------------------------------------------------- short suite
def fn_tool(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}


WEATHER = fn_tool("get_weather", "Current weather for a city", {"city": {"type": "string"},
                  "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}}, ["city", "unit"])
EMAIL = fn_tool("send_email", "Send an email", {"to": {"type": "string"}, "subject": {"type": "string"},
                "body": {"type": "string"}}, ["to", "subject", "body"])
CAL = fn_tool("create_event", "Create a calendar event", {"title": {"type": "string"},
              "date": {"type": "string", "description": "YYYY-MM-DD"},
              "time": {"type": "string", "description": "HH:MM 24h"}}, ["title", "date", "time"])


def c_json(m):
    try:
        d = json.loads(code_block(m["content"]).strip("` \n").removeprefix("json").strip())
    except Exception as e:
        return False, f"bad json: {e}"[:80]
    return (d.get("name") == "Priya Raman" and d.get("email") == "priya.r@example.com"
            and float(d.get("amount", 0)) == 1249.5), json.dumps(d)[:100]


def c_tool(name, check):
    def f(m):
        tc = m.get("tool_calls") or []
        if len(tc) != 1:
            return False, f"{len(tc)} tool calls"
        fn = tc[0]["function"]
        return fn["name"] == name and check(fn.get("arguments") or {}), json.dumps(fn)[:100]
    return f


def c_math(m):
    nums = re.findall(r"-?\d+(?:\.\d+)?", m["content"].replace(",", ""))
    return bool(nums) and float(nums[-1]) == 3.64, m["content"].strip()[-60:]


def c_sql(m):
    q = code_block(m["content"]).rstrip(";")
    db = sqlite3.connect(":memory:")
    db.executescript("""create table customers(id int, name text, country text);
    create table orders(id int, customer_id int, total real, created date);
    insert into customers values(1,'Ana','CA'),(2,'Ben','US'),(3,'Chen','CA'),(4,'Dee','CA');
    insert into orders values(1,1,100,'2026-10-02'),(2,1,50,'2026-10-20'),(3,2,500,'2026-10-05'),
      (4,3,80,'2026-10-11'),(5,3,90,'2026-09-30'),(6,4,10,'2026-10-15');""")
    try:
        rows = [tuple(r) for r in db.execute(q).fetchall()]
    except Exception as e:
        return False, f"sql error: {e}"[:80]
    return [(r[0], float(r[1])) for r in rows] == [("Ana", 150.0), ("Chen", 80.0), ("Dee", 10.0)], str(rows)[:100]


def c_regex(m):
    raw = m["content"].strip().strip("`").strip().splitlines()[-1].strip()
    if raw[:2] in ("r'", 'r"'):
        raw = raw[2:-1]
    raw = raw.strip("/")
    try:
        rx = re.compile(raw)
    except re.error as e:
        return False, f"bad regex: {e}"[:80]
    good = ["K1A 0B1", "M5V 3L9", "H2X1Y4"]
    bad = ["D1A 0B1", "K1A 0B", "12345", "K1A-0B1x", "W1A 1A1"]
    return all(rx.fullmatch(s) for s in good) and not any(rx.fullmatch(s) for s in bad), raw[:80]


def c_format(m):
    lines = [l for l in m["content"].strip().splitlines() if l.strip()]
    ok = len(lines) == 3 and all(re.match(r"^\s*[-*] ", l) and len(re.sub(r"^\s*[-*] ", "", l).split()) < 8 for l in lines)
    return ok, " | ".join(lines)[:100]


def c_py(tests):
    return lambda m: run_py(code_block(m["content"]), tests)


SHORT = [
    ("json_extract", "Extract the customer's name, email and invoice amount from this text and reply with ONLY a JSON object with keys "
     "name, email, amount (amount as a number).\n\nHi team, invoice #4471 for Priya Raman (priya.r@example.com) came to $1,249.50 "
     "after the discount. Please confirm by Friday.", None, c_json),
    ("py_palindrome", "Write a Python function is_palindrome(s: str) -> bool that ignores case and any non-alphanumeric characters. "
     "Reply with only the code in a python code block.", None,
     c_py("assert is_palindrome('A man, a plan, a canal: Panama')\nassert not is_palindrome('race a car')\n"
          "assert is_palindrome('')\nassert is_palindrome('No lemon, no melon')")),
    ("py_merge", "Write a Python function merge_intervals(intervals: list[list[int]]) -> list[list[int]] that merges overlapping or "
     "touching intervals and returns them sorted by start. Input may be unsorted. Reply with only the code in a python code block.", None,
     c_py("assert merge_intervals([[1,3],[2,6],[8,10],[15,18]])==[[1,6],[8,10],[15,18]]\nassert merge_intervals([[1,4],[4,5]])==[[1,5]]\n"
          "assert merge_intervals([])==[]\nassert merge_intervals([[5,7],[1,2]])==[[1,2],[5,7]]")),
    ("py_bugfix", "This function should return the second largest DISTINCT value in a list, or None if there isn't one. Fix it. "
     "Reply with only the corrected function in a python code block.\n\n```python\ndef second_largest(xs):\n    xs.sort()\n    return xs[-2]\n```",
     None, c_py("assert second_largest([3,1,4,1,5,9,2,6])==6\nassert second_largest([5,5,4])==4\nassert second_largest([7]) is None\n"
                "assert second_largest([2,2]) is None\nassert second_largest([-1,-3,-2])==-2")),
    ("tool_weather", "What's the weather in Toronto right now? I want it in Celsius.", [WEATHER, EMAIL],
     c_tool("get_weather", lambda a: "toronto" in str(a.get("city", "")).lower() and a.get("unit") == "celsius")),
    ("tool_pick", "Put 'Sync with Dana' on my calendar for Friday November 20 2026 at 11:30 am.", [WEATHER, EMAIL, CAL],
     c_tool("create_event", lambda a: a.get("date") == "2026-11-20" and a.get("time") == "11:30" and "dana" in str(a.get("title", "")).lower())),
    ("math", "A worker runs 3 jobs per hour for 6 hours, then 4.5 jobs per hour for 4.5 hours. On average how many jobs per hour did it "
     "run over the whole period, rounded to 2 decimals? Finish with the number alone on the last line.", None, c_math),
    ("sql", "SQLite schema: customers(id, name, country), orders(id, customer_id, total, created DATE). Write one query returning name and "
     "total order value for customers in country 'CA' for orders created in October 2026, highest total first. Columns: name, total. "
     "Reply with only the SQL in a sql code block.", None, c_sql),
    ("regex", "Write a Python regular expression that fully matches a Canadian postal code like 'K1A 0B1' or 'H2X1Y4' (optional single "
     "space; first letter excludes D F I O Q U W Z; other letters exclude D F I O Q U). Reply with only the pattern on one line, "
     "no code block, no quotes.", None, c_regex),
    ("format", "Give exactly three bullet points (each starting with '- '), each under 8 words, on why homelab backups need offsite "
     "copies. No intro, no outro.", None, c_format),
]


def run_short(model):
    out = {}
    for name, prompt, tools, check in SHORT:
        try:
            m, r = ask(model, prompt, tools)
            ok, note = check(m)
            out[name] = {"pass": ok, "wall_s": round(r["_wall"], 1), "tok_s": round(r["_tps"], 1), "note": note}
        except Exception as e:
            out[name] = {"pass": False, "note": f"error {e}"[:100]}
        print(f"  {name:16} {'PASS' if out[name]['pass'] else 'FAIL'}  {out[name]}", flush=True)
    return out


# ---------------------------------------------------------------- long suite
NEEDLES = [
    ("The access code for the Lisbon storage locker is 7194-QX.", "What is the access code for the Lisbon storage locker?", "7194-QX"),
    ("Project Heron is led by Mara Voss.", None, None),
    ("Mara Voss works out of building K-12 on the north campus.",
     "Which building does the lead of Project Heron work out of? Answer with the building name only.", "K-12"),
    ("The backup generator at the Hamilton site was last serviced on 2026-03-09.",
     "On what date was the Hamilton site backup generator last serviced? Answer YYYY-MM-DD.", "2026-03-09"),
]


def haystack(src, target_tokens):
    body = src[: int(target_tokens * 3.6)]
    paras = body.split("\n\n")
    for i, (fact, _, _) in enumerate(NEEDLES):
        paras.insert(int(len(paras) * (0.12 + 0.27 * i)), fact)
    return "\n\n".join(paras)


def long_ctx(model, src, tokens):
    qs = [(q, a) for _, q, a in NEEDLES if q]
    prompt = ("Read the document below, then answer the questions. Reply with exactly one line per question, formatted "
              "'Q1: <answer>', 'Q2: <answer>', 'Q3: <answer>'. No other text.\n\n<document>\n" + haystack(src, tokens) +
              "\n</document>\n\n" + "\n".join(f"Q{i+1}: {q}" for i, (q, _) in enumerate(qs)))
    m, r = ask(model, prompt, ctx=int(tokens * 1.25) + 2048)
    got = sum(1 for i, (_, a) in enumerate(qs) if re.search(rf"Q{i+1}:\s*.*{re.escape(a)}", m["content"], re.I))
    pe, pd = r.get("prompt_eval_count", 0), r.get("prompt_eval_duration", 1) / 1e9
    return {"pass": got == len(qs), "score": f"{got}/{len(qs)}", "prompt_tokens": pe, "prefill_tok_s": round(pe / max(pd, 1e-9)),
            "wall_s": round(r["_wall"], 1), "answer": m["content"].strip()[:160]}


CODE_TASKS = [
    ("lru_ttl", "Write a Python class TTLCache(capacity: int, ttl: float, clock=time.monotonic) with get(key, default=None), "
     "put(key, value), delete(key) -> bool, and __len__. Least-recently-used eviction when over capacity (get and put both count "
     "as use). Entries older than ttl seconds (by the injected clock) are expired: get returns default, they don't count in len, "
     "and they are evicted before any live entry. Include the imports. Reply with only the code in a python code block.", """
t=[0.0]
c=TTLCache(2, 10, clock=lambda: t[0])
c.put('a',1); c.put('b',2); assert c.get('a')==1
c.put('c',3); assert c.get('b') is None and c.get('a')==1 and c.get('c')==3
t[0]=5; c.put('a',11); t[0]=12
assert c.get('c') is None and c.get('a')==11 and len(c)==1
c.put('d',4); c.put('e',5); assert c.get('a') is None and len(c)==2
assert c.delete('d') and not c.delete('zz') and len(c)==1
c2=TTLCache(2,10,clock=lambda: t[0]); c2.put('x',1); t[0]=30; c2.put('y',2); c2.put('z',3)
assert c2.get('y')==2 and c2.get('z')==3 and len(c2)==2
"""),
    ("calc", "Write a Python function evaluate(expr: str) -> float that evaluates arithmetic with + - * / ^ (right-associative power), "
     "unary minus, parentheses, integers and decimals, and whitespace. Precedence: ^ above unary minus above * / above + -, so "
     "-2^2 == -4. Raise ValueError on malformed input or division by zero. Do NOT use eval, exec, ast or compile. Reply with only "
     "the code in a python code block.", """
import math
assert evaluate('1 + 2 * 3') == 7
assert evaluate('(1 + 2) * 3') == 9
assert evaluate('2 ^ 3 ^ 2') == 512
assert evaluate('-2^2') == -4
assert evaluate('(-2)^2') == 4
assert math.isclose(evaluate('10 / 4 - .5'), 2.0)
assert evaluate('--3') == 3
assert math.isclose(evaluate('3.5*(2 - -1)'), 10.5)
for bad in ['1 +', '(2', '2)', '1 / 0', '', '3 3', 'a+1']:
    try:
        evaluate(bad); raise AssertionError('no error for ' + repr(bad))
    except ValueError:
        pass
"""),
]


def long_code(model, name, prompt, tests):
    m, r = ask(model, prompt)
    src = code_block(m["content"])
    if name == "calc" and re.search(r"\b(eval|exec|compile)\s*\(|import ast", src):
        return {"pass": False, "note": "used eval/ast", "wall_s": round(r["_wall"], 1)}
    ok, note = run_py(src, tests)
    return {"pass": ok, "note": note, "lines": src.count("\n") + 1, "wall_s": round(r["_wall"], 1)}


PROJECT = {
    "inventory/__init__.py": "",
    "inventory/models.py": "from dataclasses import dataclass\n\n\n@dataclass\nclass Item:\n    sku: str\n    name: str\n    qty: int\n    unit_cents: int\n",
    "inventory/store.py": '''from .models import Item


class Store:
    def __init__(self):
        self._items = {}

    def add(self, item: Item):
        if item.sku in self._items:
            self._items[item.sku].qty = item.qty
        else:
            self._items[item.sku] = item

    def remove(self, sku: str, qty: int) -> int:
        """Remove up to qty units; return how many were removed. Drop the item when it reaches zero."""
        it = self._items[sku]
        taken = min(qty, it.qty)
        it.qty -= taken
        return taken

    def total_value_cents(self) -> int:
        return sum(i.qty * i.unit_cents for i in self._items.values())

    def low_stock(self, threshold: int) -> list[str]:
        """SKUs with qty strictly below threshold, sorted by qty then sku."""
        raise NotImplementedError
''',
    "tests/test_store.py": '''import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inventory.models import Item
from inventory.store import Store


def test_add_merges_quantity():
    s = Store(); s.add(Item("a", "Apple", 3, 50)); s.add(Item("a", "Apple", 2, 50))
    assert s._items["a"].qty == 5


def test_remove_drops_empty_and_unknown():
    s = Store(); s.add(Item("b", "Bolt", 4, 10))
    assert s.remove("b", 10) == 4
    assert "b" not in s._items
    assert s.remove("nope", 1) == 0


def test_total_value():
    s = Store(); s.add(Item("a", "A", 2, 150)); s.add(Item("c", "C", 1, 99))
    assert s.total_value_cents() == 399


def test_low_stock():
    s = Store()
    for sku, q in [("x", 5), ("y", 1), ("z", 1), ("w", 9)]:
        s.add(Item(sku, sku, q, 1))
    assert s.low_stock(6) == ["y", "z", "x"]


if __name__ == "__main__":
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try:
                f(); print("PASS", n)
            except Exception as e:
                fails += 1; print("FAIL", n, type(e).__name__, e)
    print("FAILED" if fails else "ALL PASSED", fails)
''',
}
AGENT_TOOLS = [
    fn_tool("list_files", "List project files", {}, []),
    fn_tool("read_file", "Read a project file", {"path": {"type": "string"}}, ["path"]),
    fn_tool("write_file", "Overwrite a project file with full new content", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    fn_tool("run_tests", "Run the test suite and return its output", {}, []),
]


def agent(model, max_steps=20):
    d = tempfile.mkdtemp(prefix="bench-agent-")
    for p, c in PROJECT.items():
        os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
        open(os.path.join(d, p), "w").write(c)

    def tests():
        o = subprocess.run([sys.executable, "-I", "tests/test_store.py"], capture_output=True, text=True, timeout=20, cwd=d)
        return (o.stdout + o.stderr)[-1500:]

    msgs = [{"role": "user", "content": "You are fixing a small Python project. Use the tools. Make all tests in tests/test_store.py "
             "pass by fixing the code in inventory/. Do NOT modify anything under tests/. When the tests pass, reply DONE."}]
    steps = calls = 0
    wall = 0.0
    for steps in range(1, max_steps + 1):
        r = chat(model, msgs, ctx=16384, tools=AGENT_TOOLS)
        wall += r["_wall"]
        m = r["message"]
        msgs.append(m)
        if not m.get("tool_calls"):
            break
        for tc in m["tool_calls"]:
            calls += 1
            f, a = tc["function"], tc["function"].get("arguments") or {}
            try:
                if f["name"] == "list_files":
                    res = "\n".join(sorted(os.path.relpath(os.path.join(r_, x), d) for r_, _, fs in os.walk(d) for x in fs if not x.endswith(".pyc")))
                elif f["name"] == "read_file":
                    res = open(os.path.join(d, os.path.normpath(a["path"]))).read()
                elif f["name"] == "write_file":
                    p = os.path.normpath(a["path"])
                    if p.startswith(("tests", "..", "/")):
                        res = "refused: tests/ is read-only"
                    else:
                        os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
                        open(os.path.join(d, p), "w").write(a["content"])
                        res = "ok"
                elif f["name"] == "run_tests":
                    res = tests()
                else:
                    res = "unknown tool"
            except Exception as e:
                res = f"error: {e}"
            msgs.append({"role": "tool", "content": res, "tool_name": f["name"]})
    final = tests()
    ok = "ALL PASSED" in final and open(os.path.join(d, "tests/test_store.py")).read() == PROJECT["tests/test_store.py"]
    shutil.rmtree(d, ignore_errors=True)
    return {"pass": ok, "steps": steps, "tool_calls": calls, "wall_s": round(wall, 1), "final": final.strip().splitlines()[-1][:80]}


def run_long(model, src):
    out = {}
    for tok in (16000, 32000, 64000):
        k = f"recall_{tok // 1000}k"
        try:
            out[k] = long_ctx(model, src, tok)
        except Exception as e:
            out[k] = {"pass": False, "note": f"error {e}"[:100]}
        out[k]["placement"] = " ".join(subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout.splitlines()[-1].split()[2:6])
        print(f"  {k:16} {'PASS' if out[k]['pass'] else 'FAIL'}  {out[k]}", flush=True)
    for name, prompt, tests in CODE_TASKS:
        try:
            out[name] = long_code(model, name, prompt, tests)
        except Exception as e:
            out[name] = {"pass": False, "note": f"error {e}"[:100]}
        print(f"  {name:16} {'PASS' if out[name]['pass'] else 'FAIL'}  {out[name]}", flush=True)
    try:
        out["agent_fix"] = agent(model)
    except Exception as e:
        out["agent_fix"] = {"pass": False, "note": f"error {e}"[:100]}
    print(f"  {'agent_fix':16} {'PASS' if out['agent_fix']['pass'] else 'FAIL'}  {out['agent_fix']}", flush=True)
    return out


def main():
    global THINK
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--think", nargs="+", default=["off"], choices=["off", "on"])
    ap.add_argument("--haystack", required=True, help="plain text file used as the long-context document")
    ap.add_argument("--suites", nargs="+", default=["short", "long"], choices=["short", "long"])
    ap.add_argument("--out", default="results.json")
    a = ap.parse_args()
    src = open(a.haystack, errors="ignore").read()
    results = {}
    for model in a.models:
        for mode in a.think:
            THINK = mode == "on"
            key = f"{model} think={mode}"
            print(f"== {key}", flush=True)
            chat(model, [{"role": "user", "content": "hi"}])
            results[key] = {}
            if "short" in a.suites:
                results[key]["short"] = run_short(model)
            if "long" in a.suites:
                results[key]["long"] = run_long(model, src)
        subprocess.run(["ollama", "stop", model], capture_output=True)
    json.dump(results, open(a.out, "w"), indent=1)
    print("\n== summary")
    for key, suites in results.items():
        parts = []
        for s, tasks in suites.items():
            parts.append(f"{s} {sum(t['pass'] for t in tasks.values())}/{len(tasks)}")
        print(f"{key:32} " + "  ".join(parts))


if __name__ == "__main__":
    main()
