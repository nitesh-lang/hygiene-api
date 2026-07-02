p = "amazon_in_crawler_playwright.py"
s = open(p, encoding="utf-8").read()
import re
pat = re.compile(r'        if REQUIRE_LOGIN:.*?page\.wait_for_timeout\(45000\)', re.S)
new = """        if REQUIRE_LOGIN:
            try:
                page.goto("https://www.amazon.in/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
            except Exception:
                pass
            def _is_logged_in():
                try:
                    el = page.query_selector("#nav-link-accountList-nav-line-1, #nav-link-accountList .nav-line-1, #nav-link-accountList")
                    txt = ((el.inner_text() if el else "") or "").strip().lower()
                    return txt != "" and ("sign in" not in txt) and ("hello" in txt or "account" in txt)
                except Exception:
                    return False
            while not _is_logged_in():
                print(chr(10) + "="*60)
                print("  LOGIN REQUIRED")
                print("  The Chrome window is open on Amazon.in.")
                print("  LOG IN by hand now (email, password, OTP).")
                print("  When the top-right shows Hello your-name,")
                print("  come back here and press ENTER.")
                print("="*60)
                try:
                    input("  Press ENTER after you have logged in... ")
                except Exception:
                    page.wait_for_timeout(30000)
                try:
                    page.goto("https://www.amazon.in/", timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
                if not _is_logged_in():
                    try:
                        ans = input("  Not detected. Type go to crawl anyway, or ENTER to retry: ").strip().lower()
                    except Exception:
                        ans = "go"
                    if ans == "go":
                        break"""
m = pat.search(s)
if m:
    s = s[:m.start()] + new + s[m.end():]
    open(p, "w", encoding="utf-8").write(s)
    import py_compile; py_compile.compile(p, doraise=True)
    print("PATCHED OK - file compiles")
else:
    print("OLD BLOCK NOT FOUND")
