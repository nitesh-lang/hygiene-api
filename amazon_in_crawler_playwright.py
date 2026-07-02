#!/usr/bin/env python3
# amazon_in_crawler_playwright.py
r"""
Amazon.in Deep Crawler (Playwright)
- Strict MRP / Deal logic
- Bulletproof Return Policy (short)
- Bulletproof Warranty Policy (short)
- No changes except required fixes

Optional dependencies for image-content checks (CS/Support QR + Ours-vs-Their):
    pip install pyzbar pillow pytesseract
    sudo apt-get install -y libzbar0 tesseract-ocr
  - pyzbar + libzbar0  -> detects QR codes in the support/warranty card (P16)
  - pytesseract + tesseract-ocr -> OCRs gallery images to spot the
        "Ours vs Their" comparison chart (P18)
If a library/binary is missing, that check silently falls back (or stays blank)
— the crawl still runs end to end.
"""

import time
import json
import re
import os
import sys
import math
import random
import pandas as pd
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ------------------------ USER CONFIG ------------------------
# Paths resolve relative to this script's own folder, so the crawler runs from
# wherever the Hygine project lives (C:, D:, or any drive) without editing code.
# You can still override by setting the HYGINE_DIR environment variable.
import os as _os
_BASE = Path(_os.environ.get("HYGINE_DIR", "") or Path(__file__).resolve().parent)
INPUT_XLSX = _BASE / "Input" / "Crawling input file.xlsx"
OUTPUT_FOLDER = _BASE / "output"
OUTPUT_CSV = OUTPUT_FOLDER / "amazon_products_full.csv"
OUTPUT_XLSX = OUTPUT_FOLDER / "amazon_products_full.xlsx"
CHECKPOINT_FILE = OUTPUT_FOLDER / "checkpoint.json"
HTML_FOLDER = OUTPUT_FOLDER / "html"

SYSTEM_CHROME = ""

OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

# ------------------------ COLUMNS ------------------------
COLUMNS = [
    "ASIN","SKU","Title","Brand",
    "MRP","Selling Price","Deal Price","Buy Box Price",
    "Rating","Rating Count",
    "Bullets","A+ Content","Image URLs",
    "Sold By","Other Sellers",
    "Category Tree","Weight","Dimensions",
    "Tech Details","Product URL","Best Sellers Rank",
    "Manufacturer Contact Information","Packer Contact Information",

    # NEW SHORT LABELS
    "Return Policy",
    "Warranty Policy",

    "Warranty Description","What is in the box?",

    # FIELDS FOR HYGIENE VALIDATOR
    "Colour","Material","Additional Features",
    "Importer Contact Information",
    "Brand Story","Description",
    "Listing Video","Variation Data",

    # NEW (2026 superset additions — genuinely scrapable signals)
    "Image Count","Video Count","A+ Image Count",
    "CS / Support QR / Warranty Image",
    "Ours vs Their Image",
    "What is in the Box Image",
    "Brand Store","Variation Count",

    # STOCK / LISTING STATE (now actually written to output)
    "Availability Text","Stock Status","Listing Status",

    # ASIN INTEGRITY — detect when Amazon redirected to a different variation
    "Crawled ASIN","ASIN Redirect",
]

# ------------------------ CONSTANTS ------------------------
PROFILE_DIR = _BASE / "browser_profile"   # persists Amazon login across runs
REQUIRE_LOGIN = False                        # crawl the logged-in PDP view
NAV_TIMEOUT = 45000
SHORT_WAIT = 0.35
MAX_OLP_OFFERS = 12
MIN_PRICE_THRESHOLD = 30.0
PRICE_RELATIVE_MARGIN = 0.03


# ------------------------ HELPERS ------------------------
def safe_text_el(el):
    try:
        return el.inner_text().strip()
    except:
        return ""

def first_text(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel.replace("xpath:", "")) if sel.startswith("xpath:") else page.query_selector(sel)
            if el:
                t = safe_text_el(el)
                if t:
                    return t
        except:
            continue
    return ""

def join_pipe(items):
    return "|".join([str(i).strip() for i in items if i])

def maybe_json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False)
    except:
        return str(obj)


# ------------------------ PRICE HELPERS ------------------------
def money_to_number(s):
    if not s or not isinstance(s, str):
        return None
    s = s.replace("\u00A0"," ").replace("Rs.","").replace("Rs","").replace("INR","").replace("₹","")
    m = re.findall(r"[\d,]{1,}\.\d+|[\d,]{2,}", s)
    if m:
        candidate = max(m, key=len)
    else:
        nums = re.findall(r"\d+", s)
        if not nums:
            return None
        candidate = max(nums, key=len)
    candidate = candidate.replace(",", "")
    try:
        return float(candidate)
    except:
        digits = re.sub(r"[^\d\.]","", candidate)
        return float(digits) if digits else None

def format_money(n):
    if n is None or n == "":
        return ""
    try:
        nf = float(n)
        return f"₹{int(nf):,}" if nf.is_integer() else f"₹{nf:,.2f}"
    except:
        return str(n)


# ------------------------ STRICT DEAL DETECTION ------------------------
def extract_prices(page):
    # --- MRP ---
    mrp_val = None
    mrp_selectors = [
        "#corePriceDisplay_desktop_feature_div span.a-price[data-a-strike='true'] span.a-offscreen",
        "span.a-price.a-text-price span.a-offscreen",
        ".a-text-strike",
        ".priceBlockStrikePriceString",
        "xpath://*//*[contains(text(),'M.R.P')]/following::span[1]"
    ]
    for sel in mrp_selectors:
        t = first_text(page, [sel])
        if t:
            v = money_to_number(t)
            if v and v >= MIN_PRICE_THRESHOLD:
                mrp_val = v
                break

    # --- SELLING ---
    selling_val = None
    selling_selectors = [
        "#corePriceDisplay_desktop_feature_div span.a-price:not([data-a-strike='true']) span.a-offscreen",
        ".priceToPay .a-offscreen",
        ".a-price .a-offscreen",
        "#priceblock_dealprice",
        "#priceblock_ourprice",
        "#price_inside_buybox"
    ]
    for sel in selling_selectors:
        t = first_text(page, [sel])
        if t:
            v = money_to_number(t)
            if v:
                selling_val = v
                break

    # --- DEAL LABEL DETECTION ---
    deal_label = ""
    try:
        region = ""
        for sel in ["#corePriceDisplay_desktop_feature_div", "#centerCol", "#ppd"]:
            try:
                r = page.query_selector(sel)
                if r:
                    region += " " + r.inner_text().lower()
            except:
                pass

        for kw in ["limited time deal", "limited-time deal", "deal of the day", "lightning deal"]:
            if kw in region:
                deal_label = "Limited time deal"
                break
    except:
        pass

    # --- MRP SANITY GUARD ---
    # The strike-price selectors can latch onto a sibling variant's price or a
    # coupon/reference value that is LOWER than the actual selling price, which
    # is impossible for a real MRP (list price >= selling price). When that
    # happens, the captured MRP is wrong: try to recover the true list price
    # from the explicit "M.R.P.:" label, else drop the bogus MRP rather than
    # emit MRP < Selling that trips the hygiene validator.
    if mrp_val is not None and selling_val is not None and mrp_val < selling_val:
        recovered = None
        try:
            # The labelled "M.R.P.: ₹12,499" value is the authoritative list price.
            for sel in [
                "xpath=//*[contains(translate(text(),'mrp','MRP'),'M.R.P')]/following::span[contains(@class,'a-offscreen')][1]",
                "xpath=//*[contains(text(),'M.R.P')]/following::*[1]",
                "span.a-price.a-text-price span.a-offscreen",
            ]:
                t = first_text(page, [sel])
                if t:
                    v = money_to_number(t)
                    if v and v >= selling_val:
                        recovered = v
                        break
        except:
            pass
        mrp_val = recovered  # None drops the bad MRP; the cell stays blank

    return (
        format_money(mrp_val) if mrp_val else "",
        format_money(selling_val) if selling_val else "",
        deal_label,
        format_money(selling_val) if selling_val else "",
    )


# ------------------------ SHORT RETURN + WARRANTY LABELS ------------------------
def extract_return_and_warranty(page):
    """
    Extract ONLY:
    - Short Return Policy label (e.g., '10 days Replacement', 'Non-Returnable')
    - Short Warranty label (e.g., '1 Year Warranty')
    """
    return_label = ""
    warranty_label = ""

    # --- RETURN POLICY ---
    # Capture the EXACT wording Amazon shows on the return badge (verbatim),
    # e.g. "10 days Returnable", "7 days Replacement", "Non-returnable". The
    # wording matters: "Returnable" (refund) and "Replacement" (same-item swap)
    # are different policies, and Amazon changes this text over time, so we read
    # the literal badge text from the most authoritative element first and keep
    # it as-is rather than normalising it to a fixed word.

    def _normalize(t):
        return re.sub(r"\s+", " ", (t or "")).strip()

    return_label = ""

    # 1) Most authoritative: the dedicated returns badge / policy anchor. These
    #    elements hold ONLY the return wording, so their text is the truth.
    primary_sels = [
        "#RETURNS_POLICY .a-text-bold",
        "#RETURNS_POLICY",
        "#creturns-policy-anchor-text",
        "#productFactsDesktopExpander #RETURNS_POLICY",
        "[data-csa-c-content-id='odf-returns-policy'] .a-text-bold",
    ]
    for sel in primary_sels:
        if return_label:
            break
        try:
            for el in page.query_selector_all(sel):
                t = _normalize(el.inner_text())
                # keep the exact phrase if it actually mentions return/replace
                m = re.search(
                    r"(non[-\s]?returnable|\d+\s*days?\s*(?:returnable|replacement)|returnable|replacement)",
                    t, re.I)
                if m:
                    return_label = _normalize(m.group(0))  # verbatim phrase
                    break
        except:
            continue

    # 2) Next: the delivery/returns icon strip (each icon's bold label is exact).
    if not return_label:
        try:
            for el in page.query_selector_all(
                "#icon-farm-feature_div .a-text-bold, [id^='icon-farm'] .a-text-bold, "
                "#iconFarmContainer .a-text-bold"):
                t = _normalize(el.inner_text())
                m = re.search(
                    r"(non[-\s]?returnable|\d+\s*days?\s*(?:returnable|replacement))",
                    t, re.I)
                if m:
                    return_label = _normalize(m.group(0))
                    break
        except:
            pass

    # 3) Last resort: scoped text search in the buy box / center column. We take
    #    the FIRST concrete day-count phrase so a stray "replacement" mentioned
    #    elsewhere (offers, A+ copy) cannot override the real badge.
    region = ""
    try:
        region = (page.inner_text("#centerCol") or "").lower()
    except:
        try:
            region = (page.inner_text("body") or "").lower()
        except:
            region = ""
    if not return_label:
        for pat in [
            r"\bnon[-\s]?returnable\b",
            r"\b\d+\s*days?\s*returnable\b",
            r"\b\d+\s*days?\s*replacement\b",
            r"\breturnable\b",
            r"\breplacement\b",
        ]:
            m = re.search(pat, region, re.I)
            if m:
                return_label = _normalize(m.group(0))
                break

    # WARRANTY PATTERNS
    warranty_patterns = [
        r"\b\d+\s*year\s*warranty\b",
        r"\b\d+\s*years\s*warranty\b",
        r"\b\d+\s*month\s*warranty\b",
        r"\b\d+\s*months\s*warranty\b",
    ]
    for pat in warranty_patterns:
        m = re.search(pat, region, re.I)
        if m:
            warranty_label = m.group(0).strip()
            break

    return return_label, warranty_label


# ------------------------ IMAGES + BULLETS + A+ CONTENT ------------------------
# Junk patterns: Prime badges, marketing logos, UI sprites, transparent pixels, grey placeholders.
_IMG_JUNK = ("/marketing/", "prime_logo", "/prime/", "grey-pixel", "transparent-pixel",
             "sprite", "/g/01/", "/g/31/", "play-button", "360_icon", "icon-",
             "play-icon", "-play-", "/captcha/")

def _image_id(src):
    """Amazon's stable image key, e.g. .../images/I/51Oyw7ZrF3L.jpg -> 51Oyw7ZrF3L.
    Used to dedupe the SAME physical image served from different CDN hosts
    (m.media-amazon.com vs images-eu.ssl-images-amazon.com) or with different
    size suffixes — counting those twice was inflating the image count."""
    m = re.search(r"/images/i/([a-z0-9_+\-]+?)(?:\._[a-z0-9,_]+_)?\.(?:jpg|jpeg|png|gif|webp)",
                  src.lower())
    return m.group(1) if m else src.lower()

def _clean_img_src(raw):
    if not raw:
        return None
    raw = raw.strip()
    # data-a-dynamic-image is a JSON map {"url":[w,h], ...}; take the largest URL.
    if raw.startswith("{"):
        pairs = re.findall(r'"(https?://[^"]+)"\s*:\s*\[\s*(\d+)', raw)
        if pairs:
            raw = max(pairs, key=lambda p: int(p[1]))[0]
        else:
            m = re.search(r'https?://[^"]+', raw)
            raw = m.group(0) if m else ""
    if not raw:
        return None
    raw = raw.split("?")[0]
    raw = re.sub(r"\._[A-Z0-9,_]+_\.", ".", raw)  # strip thumbnail size suffix
    return raw

def _images_from_json(page):
    """PRIMARY source: read the full gallery from Amazon's image JSON island.

    The visible #altImages thumbnail rail is truncated by the "+N more" overlay
    tile and lazy-loads on hover/click, so scraping the DOM undercounts whenever
    a gallery overflows (e.g. reports 8 when there are really 9). Amazon always
    embeds the COMPLETE gallery in the page's colorImages / imageGalleryData
    JSON regardless of what is rendered, so we parse that first.

    Returns a list of cleaned still-image URLs (video poster frames excluded),
    deduped by stable Amazon image ID, in gallery order. Empty list on failure
    so the DOM scrape can take over."""
    try:
        html = page.content()
    except:
        return []
    imgs, seen = [], set()

    # Each gallery still has a "variant" tag (MAIN, PT01, PT02 ...). The image
    # object also contains a nested {url:[w,h], ...} map, so we cannot match the
    # whole object with a simple [^{}] span. Instead we walk every "variant"
    # occurrence and look BACKWARD for the nearest preceding hiRes/large URL,
    # which belongs to that same gallery entry. Video frames carry
    # "mediaType":"video" near the variant tag — skip those.
    #
    # IMPORTANT: only the FIRST contiguous MAIN -> PT01 -> PT02 ... run is the
    # product's own gallery. Amazon often appends a second image set (a sibling
    # variation's gallery, or A+ assets) that ALSO starts again at "MAIN". Once
    # we see "MAIN" a second time after we've already collected images, we stop
    # — otherwise we overcount (e.g. report 13 when the gallery is really 9).
    started = False
    for vm in re.finditer(r'"variant"\s*:\s*"([A-Za-z0-9_]+)"', html):
        variant = vm.group(1).upper()
        if variant == "MAIN" and started:
            break  # second image set begins; the product gallery is done
        tail = html[vm.start(): vm.end() + 200].lower()
        if '"video"' in tail or 'mediatype":"video' in tail:
            started = True
            continue
        back = html[max(0, vm.start() - 4000): vm.start()]
        m = None
        for mm in re.finditer(r'"(?:hiRes|large)"\s*:\s*"(https://[^"]+)"', back):
            m = mm  # keep the last (nearest preceding) match
        if not m:
            continue
        src = _clean_img_src(m.group(1))
        if not src or "/images/i/" not in src.lower():
            continue
        if any(j in src.lower() for j in _IMG_JUNK):
            continue
        started = True
        iid = _image_id(src)
        if iid not in seen:
            seen.add(iid)
            imgs.append(src)
    if imgs:
        return imgs

    # Fallback within the JSON path: ordered "large" entries on media-amazon.
    for u in re.findall(
        r'"large"\s*:\s*"(https://m\.media-amazon\.com/images/I/[^"]+)"', html
    ):
        src = _clean_img_src(u)
        if not src:
            continue
        iid = _image_id(src)
        if iid not in seen:
            seen.add(iid)
            imgs.append(src)
    return imgs


def scan_gallery_content(page, gallery_urls):
    """Scan the product gallery IMAGES for content that only exists in pixels.

    Several hygiene checks are about what an image SHOWS, not page text:
      - CS / Support QR / Warranty card  -> detect via QR codes (pyzbar)
      - "Ours vs Their" comparison chart -> detect via OCR keywords (tesseract)

    Both pieces of content are baked into the image, so the page HTML has no
    text to match. We fetch the gallery images through the page's own
    (authenticated) request context and inspect them. Best-effort: if a library
    or binary is missing, that signal is simply skipped — the crawl never
    breaks.

    Returns a dict: {"cs_qr": "...", "ours_vs_theirs": "..."} with each value
    being a short status string or "".
    """
    result = {"cs_qr": "", "ours_vs_theirs": "", "whats_in_box_img": ""}
    if not gallery_urls:
        return result

    try:
        from PIL import Image
        import io
    except Exception:
        return result  # without PIL we can't inspect images at all

    try:
        from pyzbar.pyzbar import decode as _qrdecode
        _HAVE_QR = True
    except Exception:
        _HAVE_QR = False

    try:
        import pytesseract
        _HAVE_OCR = True
    except Exception:
        _HAVE_OCR = False

    # Support / comparison / box-contents cards live among the LAST gallery
    # images, so scan the tail first. We OCR each image once and test all cues.
    _OVT_CUES = ("vs others", "see the difference", "ours vs",
                 "vs other", "comparison")
    _BOX_CUES = ("what's in the box", "whats in the box", "what is in the box",
                 "in the box")
    for url in reversed(gallery_urls[-6:]):
        if result["cs_qr"] and result["ours_vs_theirs"] and result["whats_in_box_img"]:
            break  # everything found, stop early
        try:
            resp = page.request.get(url, timeout=8000)
            if not resp.ok:
                continue
            img = Image.open(io.BytesIO(resp.body()))
        except Exception:
            continue

        # --- QR / support card ---
        if _HAVE_QR and not result["cs_qr"]:
            try:
                if _qrdecode(img):
                    result["cs_qr"] = "Present (QR found)"
            except Exception:
                pass

        # --- OCR once, reuse text for comparison + box-contents checks ---
        if _HAVE_OCR and (not result["ours_vs_theirs"] or not result["whats_in_box_img"]):
            try:
                txt = (pytesseract.image_to_string(img) or "").lower()
            except Exception:
                txt = ""

            # Ours vs Their: names a rival column ("others") AND marks rival
            # gaps as "n/a" or uses a "see the difference" header.
            if not result["ours_vs_theirs"]:
                if any(c in txt for c in _OVT_CUES) or ("others" in txt and "n/a" in txt):
                    result["ours_vs_theirs"] = "Present (comparison image)"

            # What's in the Box image: a "...in the box" header. Guard against the
            # comparison-image false hit (those don't carry box-contents wording).
            if not result["whats_in_box_img"]:
                if any(c in txt for c in _BOX_CUES):
                    result["whats_in_box_img"] = "Present (box image)"

    return result


def detect_support_qr_image(page, gallery_urls):
    """Back-compat shim: CS/Support QR via the unified gallery scanner, with a
    soft page-text fallback when no QR is decoded."""
    res = scan_gallery_content(page, gallery_urls)
    if res.get("cs_qr"):
        return res["cs_qr"]
    try:
        body = (page.inner_text("body") or "").lower()
        cues = ("scan to call", "scan to mail", "scan the code", "whatsapp",
                "after sales support", "total care", "service details",
                "customer support qr", "scan for warranty")
        if any(c in body for c in cues):
            return "Present"
    except Exception:
        pass
    return ""


def extract_images(page):
    """Return ONLY the product's own gallery images.

    Source priority:
      0. (NEW) Amazon's colorImages / imageGalleryData JSON island — the only
         source that always holds the COMPLETE gallery, even images hidden
         behind the "+N more" overlay tile. This is the reliable count.
      1. Fallback: scrape the visible #altImages thumbnail rail (can undercount
         when the gallery overflows and lazy-load hasn't fired).

    Bugs previously fixed and still guarded:
      - Old selector swept in related-products / sponsored carousels; we stay
        scoped to the real gallery only.
      - Dedup is by stable Amazon image ID, not full URL, so the same image
        from two CDN hosts / size suffixes is not double-counted.

    Video thumbnails are excluded from the IMAGE list (counted separately as
    Video Count)."""
    # ---- PRIMARY: JSON island (complete gallery, no clicking needed) ----
    json_imgs = _images_from_json(page)
    if json_imgs:
        return json_imgs

    # ---- FALLBACK: DOM thumbnail scrape ----
    imgs = []
    seen_ids = set()
    try:
        # Amazon collapses extra thumbnails behind a "4+" / "See more" tile and
        # lazy-loads thumbnails below the fold. Expand + hover so every gallery
        # <li> is actually in the DOM before we count, otherwise we undercount
        # (e.g. report 7 when the strip really has 9+).
        try:
            # Click the "+N" / "see all images" expander if present.
            for exp_sel in ["#altImages .a-button-text",
                            "li.a-spacing-small.item span:has-text('+')",
                            "#altImages li:last-child"]:
                try:
                    exp = page.query_selector(exp_sel)
                    if exp:
                        txt = (exp.inner_text() or "")
                        if "+" in txt or "more" in txt.lower():
                            exp.click(timeout=1500)
                            page.wait_for_timeout(400)
                            break
                except:
                    continue
            # Hover each thumbnail to force lazy <img> to load.
            for li in page.query_selector_all("#altImages li"):
                try:
                    li.hover(timeout=300)
                except:
                    pass
            page.wait_for_timeout(300)
        except:
            pass

        # Scope strictly to the gallery thumbnail rail + the main hero image.
        # Skip <li> entries that are video thumbnails.
        gallery_imgs = []
        try:
            for li in page.query_selector_all("#altImages li"):
                cls = (li.get_attribute("class") or "").lower()
                if "video" in cls:
                    continue  # video thumb, not a still image
                im = li.query_selector("img")
                if im:
                    gallery_imgs.append(im)
        except:
            pass
        # Main hero image as a fallback / first image.
        for sel in ["#landingImage", "#imgTagWrapperId img", "#main-image #landingImage"]:
            try:
                el = page.query_selector(sel)
                if el:
                    gallery_imgs.append(el)
            except:
                pass

        for el in gallery_imgs:
            src = _clean_img_src(
                el.get_attribute("data-old-hires")
                or el.get_attribute("data-a-dynamic-image")
                or el.get_attribute("src")
                or el.get_attribute("data-src")
            )
            if not src:
                continue
            low = src.lower()
            if "/images/i/" not in low:
                continue
            if any(j in low for j in _IMG_JUNK):
                continue
            iid = _image_id(src)
            if iid not in seen_ids:
                seen_ids.add(iid)
                imgs.append(src)
    except:
        pass
    return imgs


def extract_bullets(page):
    bullets = []
    try:
        for li in page.query_selector_all("#feature-bullets ul li, #feature-bullets li"):
            t = safe_text_el(li)
            if t and not t.lower().startswith("see more"):
                bullets.append(t)
    except:
        pass
    return bullets


def extract_aplus(page):
    blocks = []
    try:
        for el in page.query_selector_all("#aplus_feature_div .a-section, #aplus_feature_div p, .aplus"):
            t = safe_text_el(el)
            if t and len(t) > 3:
                blocks.append(t)
    except:
        pass
    return blocks
# ------------------------ TECH TABLE + BSR ------------------------
def extract_tech_table(page):
    tech = {}

    # Technical Table 1
    try:
        rows = page.query_selector_all("#productDetails_techSpec_section_1 tr")
        for r in rows:
            try:
                k = r.query_selector("th").inner_text().strip()
                v = r.query_selector("td").inner_text().strip()
                tech[k] = v
            except:
                continue
    except:
        pass

    # Technical Table 2
    try:
        rows = page.query_selector_all("#productDetails_detailBullets_sections1 tr")
        for r in rows:
            try:
                k = r.query_selector("th").inner_text().strip()
                v = r.query_selector("td").inner_text().strip()
                tech[k] = v
            except:
                continue
    except:
        pass

    # Technical Table 3 (second tech-spec section, "Additional Information")
    try:
        rows = page.query_selector_all("#productDetails_techSpec_section_2 tr")
        for r in rows:
            try:
                k = r.query_selector("th").inner_text().strip()
                v = r.query_selector("td").inner_text().strip()
                tech.setdefault(k, v)
            except:
                continue
    except:
        pass

    # Product Overview box (key-value table under price: Colour, Material, Brand...)
    try:
        rows = page.query_selector_all("#productOverview_feature_div table tr")
        for r in rows:
            try:
                tds = r.query_selector_all("td")
                if len(tds) >= 2:
                    k = tds[0].inner_text().strip()
                    v = tds[1].inner_text().strip()
                    if k and v:
                        tech.setdefault(k, v)
            except:
                continue
    except:
        pass

    # Generic catch-all: any th/td key-value row anywhere in the product-detail
    # column. Covers the newer "Measurements" / collapsible expander spec blocks
    # (e.g. Item Weight, Product Dimensions, Material, Manufacturer) that don't
    # live in the fixed-id tables above. setdefault → never overwrites a more
    # specific value already captured.
    try:
        rows = page.query_selector_all(
            "#prodDetails tr, #productDetails_feature_div tr, "
            "#detailBullets_feature_div tr, .a-expander-content table tr, "
            "#poExpander table tr, .content-grid-block table tr, table.a-keyvalue tr"
        )
        for r in rows:
            try:
                th = r.query_selector("th")
                td = r.query_selector("td")
                if th and td:
                    k = th.inner_text().strip()
                    v = td.inner_text().strip()
                    if k and v:
                        tech.setdefault(k, v)
                    continue
                tds = r.query_selector_all("td")
                if len(tds) >= 2:
                    k = tds[0].inner_text().strip()
                    v = tds[1].inner_text().strip()
                    if k and v:
                        tech.setdefault(k, v)
            except:
                continue
    except:
        pass

    # Detail Bullets List
    try:
        items = page.query_selector_all("#detailBullets_feature_div li, #detailBullets_feature_div .a-list-item")
        for li in items:
            t = safe_text_el(li)
            if ":" in t:
                k, v = t.split(":", 1)
                tech[k.strip()] = v.strip()
    except:
        pass

    # Clean invisible unicode marks (RLM/LRM) Amazon embeds in keys/values
    tech = {re.sub(r"[\u200e\u200f\u200b]", "", k).strip(): re.sub(r"[\u200e\u200f\u200b]", "", str(v)).strip()
            for k, v in tech.items()}

    return tech


def extract_bsr(page):
    """Extract Best Sellers Rank.

    Root cause of the old miss (~200 of ~270 live products returned empty):
    BSR is most often in a product-details TABLE row —
        <th>Best Sellers Rank</th><td> #45 in Musical Instruments ... </td>
    The old code only scanned <li> bullets and a body regex. In Playwright's
    inner_text the <th> label and the <td> value are separated by several blank
    lines (empty span/ul/li), so the tight regex failed and the <li> selector
    never matched the table form. We now read the th/td table FIRST, then fall
    back to detail-bullet <li>, then a loosened body regex.
    """
    bsr = ""

    # 1) PRIMARY: product-details table rows (th label -> td value).
    try:
        for th in page.query_selector_all(
            "#productDetails_detailBullets_sections1 th, "
            "#productDetails_techSpec_section_1 th, "
            "table.prodDetTable th, #prodDetails th, table th"
        ):
            label = (th.inner_text() or "").strip()
            if "Best Sellers Rank" in label or "Best Seller Rank" in label:
                td = th.evaluate_handle("el => el.nextElementSibling")
                val = ""
                try:
                    val = (td.as_element().inner_text() or "").strip() if td else ""
                except:
                    val = ""
                if val:
                    bsr = "Best Sellers Rank " + " ".join(val.split())
                    break
    except:
        pass

    # 2) Detail-bullet <li> form (older layout).
    if not bsr:
        try:
            for li in page.query_selector_all(
                "#prodDetails li, #detailBullets_feature_div li, "
                "#productDetails_detailBullets_sections1 li"
            ):
                t = safe_text_el(li)
                if "Best Sellers Rank" in t or "Best seller rank" in t or "Best Sellers" in t:
                    bsr = " ".join(t.split()).strip()
                    break
        except:
            pass

    # 3) LAST RESORT: loosened body regex (allows the multi-newline th/td gap).
    if not bsr:
        try:
            body = page.inner_text("body") or ""
            m = re.search(
                r"Best Sellers Rank\s*[:#]?\s*(?:#[\d,]+\s*in\s*[A-Za-z0-9 &\-/()]+)",
                body, re.I | re.S
            )
            if m:
                bsr = " ".join(m.group(0).split()).strip()
        except:
            pass

    return bsr


# ------------------------ SELLER INFO ------------------------
def extract_seller_info(page):
    sold_by = ""
    buybox_seller = ""
    fulfilled_by = ""
    ships_from = ""
    in_stock = None
    delivery_text = ""
    buybox_price_text = ""

    # Sold By Section
    try:
        sold_by = first_text(page, ["#merchant-info"])
        buybox_seller = first_text(page, ["#sellerProfileTriggerId"])
    except:
        pass

    # Ships From
    try:
        ships_from = first_text(page, ["xpath://*[contains(text(),'Ships from')]/following::span[1]"])
    except:
        pass

    # Stock Status — the old code did bool(query_selector("#availability")), but
    # #availability EXISTS on every PDP (in-stock AND out-of-stock), so it was
    # always True. Read the actual text instead.
    availability_text = ""
    in_stock = None
    try:
        av = page.query_selector("#availability")
        if av:
            availability_text = (av.inner_text() or "").strip()
        if not availability_text:
            availability_text = first_text(page, ["#availability span", "#outOfStock",
                                                   "#availability_feature_div"])
        low = availability_text.lower()
        OOS = ("currently unavailable", "out of stock", "unavailable",
               "sold out", "we don't know when", "temporarily out of stock",
               "no featured offers")
        INSTOCK = ("in stock", "only", "left in stock", "available", "in stock soon",
                   "usually dispatched", "ships within")
        if any(k in low for k in OOS):
            in_stock = False
        elif any(k in low for k in INSTOCK):
            in_stock = True
        else:
            # No availability text + no buy button => treat as unavailable.
            has_buy = bool(page.query_selector("#add-to-cart-button, #buy-now-button"))
            in_stock = True if has_buy else (None if not availability_text else True)
    except:
        pass

    # Delivery Message
    try:
        delivery_text = first_text(
            page,
            ["#delivery-message", ".delivery-message", ".a-color-secondary"]
        )
    except:
        pass

    # Buybox Price
    buybox_price_text = first_text(
        page,
        [
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
            "#price_inside_buybox .a-offscreen",
            "xpath://*[@id='buybox']//span[contains(@class,'a-price')]/span[contains(@class,'a-offscreen')]",
        ]
    )

    return (
        sold_by,
        buybox_seller,
        fulfilled_by,
        ships_from,
        in_stock,
        delivery_text,
        buybox_price_text,
        availability_text,
    )


# ------------------------ OLP (Other Sellers) ------------------------
def parse_olp_via_newtab(page, asin, max_offers=MAX_OLP_OFFERS):
    offers = []

    try:
        link = page.query_selector(
            f"a[href*='/gp/offer-listing/'], a[href*='/gp/offer-listing/{asin}']"
        )
        url = link.get_attribute("href") if link else f"https://www.amazon.in/gp/offer-listing/{asin}"

        context = page.context
        new_page = context.new_page()
        new_page.goto(url, timeout=NAV_TIMEOUT)
        time.sleep(1)

        rows = new_page.query_selector_all(".olpOffer, .a-section .a-row, .olpOfferWrap")
        for offer in rows:
            if len(offers) >= max_offers:
                break
            try:
                seller = first_text(
                    new_page,
                    [".olpSellerName", ".a-row .olpSellerName", ".olpSellerName a"]
                )
                price = first_text(new_page, [".olpOfferPrice", ".a-price .a-offscreen"])
                condition = first_text(new_page, [".olpCondition", ".condition"])
                offers.append({"seller": seller, "price": price, "condition": condition})
            except:
                continue

        new_page.close()
    except:
        pass

    return offers
# ------------------------ RANDOM SLEEP ------------------------
def rand_sleep(min_s=0.25, max_s=0.8):
    time.sleep(random.uniform(min_s, max_s))


# ------------------------ SCRAPE SINGLE ASIN ------------------------
def scrape_asin_playwright(page, asin, sku=None, url=None):
    out = {k: "" for k in COLUMNS}
    out["ASIN"] = asin
    out["SKU"] = sku or ""
    # Prefer the EXACT product URL supplied in the input file (it pins the right
    # child variation, e.g. .../dp/ASIN?th=1). Fall back to building one with
    # th=1&psc=1, which asks Amazon for THIS exact child instead of silently
    # redirecting to the variation's default child.
    if url and str(url).strip().lower().startswith("http"):
        url = str(url).strip()
        # make sure psc=1 is present so Amazon locks to this child variation
        if "psc=" not in url:
            url += ("&" if "?" in url else "?") + "psc=1"
    else:
        url = f"https://www.amazon.in/dp/{asin}?th=1&psc=1"

    # Navigate with a retry. We wait for "domcontentloaded" (the HTML + initial
    # DOM) instead of "load" (which also waits for every image / ad / tracker
    # and was timing out at 15s on heavy Amazon pages). One retry handles the
    # occasional slow response so a single hiccup doesn't drop a good ASIN.
    last_err = None
    for attempt in range(2):
        try:
            page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            last_err = None
            break
        except Exception as e:
            last_err = e
            try:
                page.wait_for_timeout(1500)  # brief pause, then retry once
            except Exception:
                pass
    if last_err is not None:
        # Final fallback: try the plain /dp/ASIN URL once before giving up.
        try:
            page.goto(f"https://www.amazon.in/dp/{asin}?th=1&psc=1",
                      timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        except Exception:
            raise last_err

    # Let JS-hydrated regions (breadcrumbs, price, availability) populate.
    # The newer #desktop-breadcrumbs_feature_div loads its links after initial
    # HTML, so a tiny wait prevents an empty Category Tree (false nodding fail).
    try:
        page.wait_for_selector(
            "#wayfinding-breadcrumbs_feature_div a, #desktop-breadcrumbs_feature_div a, #productTitle",
            timeout=4000
        )
    except:
        pass

    # save raw HTML of the PDP for reference / re-parsing later
    try:
        HTML_FOLDER.mkdir(parents=True, exist_ok=True)
        html = page.content()
        (HTML_FOLDER / f"{asin}.html").write_text(html, encoding="utf-8")
    except Exception as e:
        print(f"  ⚠ Could not save HTML for {asin}: {e}")

    # expand collapsed sections if any
    try:
        for el in page.query_selector_all("span.a-expander-prompt, .a-expander-prompt"):
            try:
                el.click(timeout=200)
            except:
                pass
    except:
        pass

    # ---------- ASIN INTEGRITY CHECK ----------
    # Amazon silently redirects a child ASIN to the variation's default child.
    # When that happens the page (title, price, images, category) belongs to a
    # DIFFERENT ASIN than the one we requested, which produces false validator
    # FAILs. Detect it by reading the real ASIN off the page and flag it.
    real_asin = ""
    try:
        el = page.query_selector("input#ASIN, input[name='ASIN']")
        if el:
            real_asin = (el.get_attribute("value") or "").strip()
        if not real_asin:
            can = page.query_selector("link[rel='canonical']")
            if can:
                m = re.search(r"/dp/([A-Z0-9]{10})", can.get_attribute("href") or "")
                real_asin = m.group(1) if m else ""
    except:
        pass
    out["Crawled ASIN"] = real_asin or asin
    out["ASIN Redirect"] = "YES" if (real_asin and real_asin != asin) else ""

    # ---------- TITLE + BRAND ----------
    out["Title"] = first_text(page, ["#productTitle"])
    out["Brand"] = first_text(page, ["#bylineInfo", ".bylineInfo", "#brand"])

    # ---------- PRICE EXTRACTION ----------
    mrp_str, selling_str, deal_str, buybox_str = extract_prices(page)
    out["MRP"] = mrp_str
    out["Selling Price"] = selling_str
    out["Deal Price"] = deal_str
    out["Buy Box Price"] = buybox_str

    # ---------- RATINGS ----------
    raw_rating = first_text(page, ["span[data-hook='rating-out-of-text']", "#acrPopover .a-icon-alt"])
    rating_val = ""
    if raw_rating:
        m = re.search(r"([\d\.]+)", raw_rating)
        if m:
            rating_val = m.group(1)
    out["Rating"] = rating_val

    raw_count = first_text(page, ["#acrCustomerReviewText", "span[data-hook='total-review-count']"])
    out["Rating Count"] = re.sub(r"[^\d]", "", raw_count) if raw_count else ""

    # ---------- CONTENT ----------
    out["Bullets"] = " | ".join(extract_bullets(page))
    out["A+ Content"] = " | ".join(extract_aplus(page))
    out["Image URLs"] = join_pipe(extract_images(page))

    # ---------- SELLER INFO ----------
    sold_by, buybox_seller, fulfilled_by, ships_from, in_stock, delivery_text, buybox_price_text, availability_text = extract_seller_info(page)
    out["Sold By"] = sold_by or buybox_seller

    # ---------- STOCK / LISTING STATE ----------
    out["Availability Text"] = availability_text
    if in_stock is True:
        out["Stock Status"] = "In Stock"
    elif in_stock is False:
        out["Stock Status"] = "Out of Stock"
    else:
        out["Stock Status"] = "Unknown"
    # Listing Status: a PDP that loaded a title but is unavailable is a LIVE but
    # OOS listing; no title at all usually means a dead/suppressed ASIN.
    if not out.get("Title"):
        out["Listing Status"] = "Dead / Suppressed"
    elif in_stock is False:
        out["Listing Status"] = "Live - Out of Stock"
    else:
        out["Listing Status"] = "Live - Active"

    if not out["Buy Box Price"] and buybox_price_text:
        n = money_to_number(buybox_price_text)
        out["Buy Box Price"] = format_money(n) if n else buybox_price_text

    # ---------- OLP OFFERS ----------
    offers = parse_olp_via_newtab(page, asin)
    out["Other Sellers"] = maybe_json(offers)

    # ---------- CATEGORY TREE ----------
    try:
        crumbs = page.query_selector_all(
            "#wayfinding-breadcrumbs_container ul li a, "
            "#wayfinding-breadcrumbs_feature_div li a, "
            "#desktop-breadcrumbs_feature_div a, "          # newer layout id
            "nav.a-breadcrumb a, .a-breadcrumb a"
        )
        parts = []
        seen_c = set()
        for c in crumbs:
            t = safe_text_el(c)
            if t and t not in seen_c and t not in ("›", ">", "/"):
                seen_c.add(t)
                parts.append(t)
        out["Category Tree"] = " > ".join(parts)
    except:
        out["Category Tree"] = ""

    # ---------- TECH TABLE ----------
    tech = extract_tech_table(page)
    out["Tech Details"] = maybe_json(tech)

    # ---------- RETURN + WARRANTY SHORT LABEL EXTRACTION ----------
    try:
        return_label, warranty_label = extract_return_and_warranty(page)
        out["Return Policy"] = return_label
        out["Warranty Policy"] = warranty_label
    except:
        out["Return Policy"] = ""
        out["Warranty Policy"] = ""

    # ---------- WEIGHT + DIMENSIONS + SIZE ----------
    # Pass 1: most specific keys win (Item Weight > Package Weight, Product Dimensions > Package Dimensions)
    for pref in ["item weight", "product dimensions", "item dimensions"]:
        for k, v in tech.items():
            lk = k.lower()
            if pref in lk:
                if "weight" in pref and not out["Weight"]:
                    out["Weight"] = v
                if "dimension" in pref and not out["Dimensions"]:
                    out["Dimensions"] = v
    # Pass 2: any weight/dimension key as fallback (incl. package weight/dims, item display)
    for k, v in tech.items():
        lk = k.lower()
        if "weight" in lk and not out["Weight"]:
            out["Weight"] = v
        if ("dimension" in lk or "item display" in lk or "size" == lk or lk.startswith("size")) and not out["Dimensions"]:
            out["Dimensions"] = v
    # Pass 3: dimensions often hide inside the weight cell ("45 x 90 x 140 cm; 8 kg") and vice versa
    if not out["Dimensions"] and out["Weight"] and " x " in out["Weight"]:
        out["Dimensions"] = out["Weight"]
    if not out["Weight"] and out["Dimensions"]:
        m = re.search(r"([\d.]+\s*(?:kg|g|gram|grams|kilograms?|lb|pounds?))\b", out["Dimensions"], re.I)
        if m:
            out["Weight"] = m.group(1)

    # ---------- BSR ----------
    out["Best Sellers Rank"] = extract_bsr(page)

    # ---------- MANUFACTURER + PACKER ----------
    # Guard: the value must look like contact/address info, not warranty/return
    # blurb. Some PDP layouts put a short brand label AND a long address under
    # keys containing "manufacturer"; prefer the address-like one and never
    # accept warranty/WA-pickup text that bleeds in from adjacent rows.
    def _looks_like_contact(val):
        if not val:
            return False
        if re.search(r"warranty|doorstep|instant resolution|replacement|return", val, re.I):
            return False
        # address-ish signals: pincode, email, "Customer Care", street words, Ph/WA number with address
        return bool(re.search(r"\b\d{6}\b|@|customer care|road|rd\b|street|st\b|nagar|andheri|mumbai|solaris|pvt|private limited|llp|inc\b", val, re.I)) or len(val) > 40

    def _pick_contact(keyword):
        best = ""
        for k, v in tech.items():
            if keyword in k.lower() and v:
                if _looks_like_contact(v):
                    # prefer the longest address-like value
                    if len(v) > len(best):
                        best = v
        # fallback: if nothing address-like, take any non-warranty value
        if not best:
            for k, v in tech.items():
                if keyword in k.lower() and v and not re.search(r"warranty|doorstep|instant resolution", v, re.I):
                    best = v
                    break
        return best

    out["Manufacturer Contact Information"] = _pick_contact("manufacturer")
    out["Packer Contact Information"] = _pick_contact("packer")

    # ---------- WARRANTY DESCRIPTION ----------
    # Only read scoped warranty containers + the tech table. The old broad
    # xpath (//*[contains(.,'Warranty')]) matched the whole <body> and dumped
    # the Amazon footer ("Audible, Shopbop ... ©1996-2026 Amazon"), so it's gone.
    wd = first_text(page, ["#warranty", ".warranty-info", "#warranty_feature_div"])
    if not wd:
        for k, v in tech.items():
            if "warranty" in k.lower() and v:
                wd = v
                break
    # Fall back to the short warranty label (e.g. "1 year warranty") if nothing else.
    if not wd:
        wd = out.get("Warranty Policy", "")
    # Guardrail: never store the footer junk even if a selector leaks it.
    if wd and re.search(r"Conditions of Use|©\s*\d{4}|Amazon\.com, Inc|Audible|Shopbop", wd, re.I):
        wd = out.get("Warranty Policy", "")
    out["Warranty Description"] = wd

    # ---------- WHAT IS IN THE BOX ----------
    box = ""
    # 1) Prefer the dedicated "What's in the box" PDP section if present.
    for sel in ["#witb-content", "#whatsInTheBox_feature_div", "#witb_feature_div",
                "[data-feature-name='witb']", "#postPurchaseContent"]:
        try:
            el = page.query_selector(sel)
            if el:
                t = el.inner_text().strip()
                # strip the heading line
                t = re.sub(r"^\s*what'?s? in the box\??\s*", "", t, flags=re.I).strip()
                if t and len(t) > 3:
                    box = t
                    break
        except:
            pass
    # 2) Fall back to structured tech keys.
    if not box:
        for key in ["Included Components", "What is in the box?", "Package Contents", "In the box", "Included items"]:
            if key in tech and tech[key]:
                box = tech[key]
                break
    # 3) Reject junk: a lone "Installation Guide"/"Manual"/"User Guide" is NOT the box
    #    contents (it's a leaked spec). Real box contents list multiple items.
    if box and re.fullmatch(r"(installation guide|user guide|manual|warranty card)\.?", box.strip(), re.I):
        box = ""
    out["What is in the box?"] = box

    # ---------- IMPORTER ----------
    if not out["Importer Contact Information"]:
        out["Importer Contact Information"] = _pick_contact("importer")

    # ---------- COLOUR + MATERIAL + ADDITIONAL FEATURES ----------
    # Colour: a seller sometimes stuffs the whole variant name into the "Colour"
    # attribute (e.g. "Motorised Desk (1200x600mm) - CarbonFibre") while a clean
    # value sits under "Top Color"/"Color name". Collect all colour-ish keys and
    # prefer the shortest clean one (no dimensions/measurements) so the field
    # holds an actual colour, not a leaked variant spec.
    colour_candidates = []
    for k, v in tech.items():
        lk = k.lower()
        if ("colour" in lk or "color" in lk) and "country" not in lk and v:
            colour_candidates.append(v.strip())
        if "material" in lk and not out["Material"]:
            out["Material"] = v
        if ("special feature" in lk or "additional feature" in lk) and not out["Additional Features"]:
            out["Additional Features"] = v
    if colour_candidates and not out["Colour"]:
        def _clean_colour(c):
            # penalise values that carry measurements / look like a variant string
            bad = bool(re.search(r"\d+\s*(?:x|×)\s*\d+|\d+\s*(?:cm|mm|mtr|m)\b|\(.*\)", c, re.I))
            return (1 if bad else 0, len(c))
        out["Colour"] = sorted(colour_candidates, key=_clean_colour)[0]
        # last resort: strip a trailing "- Colour" out of a variant string
        if re.search(r"\d+\s*(?:x|×)\s*\d+", out["Colour"]):
            m = re.search(r"-\s*([A-Za-z ]+)\.?\s*$", out["Colour"])
            if m:
                out["Colour"] = m.group(1).strip()

    # ---------- DESCRIPTION ----------
    out["Description"] = first_text(
        page,
        ["#productDescription", "#productDescription_feature_div", "#descriptionAndDetails"]
    )
    # Fallback: modern listings replace the plain description with the A+ module,
    # so reuse the already-scraped A+ Content text when the plain field is empty.
    if not out["Description"] and out.get("A+ Content"):
        ap_txt = out["A+ Content"]
        out["Description"] = (ap_txt[:1500] + " …") if len(ap_txt) > 1500 else ap_txt

    # ---------- BRAND STORY ----------
    bs = ""
    try:
        for sel in ["#brandStory_feature_div", ".apm-brand-story-hero",
                    ".apm-brand-story-card", "#aplusBrandStory_feature_div"]:
            el = page.query_selector(sel)
            if el:
                t = (el.inner_text() or "").strip()
                bs = t[:1000] if t else "Present"
                break
    except:
        pass
    out["Brand Story"] = bs

    # ---------- LISTING VIDEO (incl. influencer / community video) ----------
    # Two distinct things the P19 check cares about:
    #   (a) MAIN listing video — the play button in the product image gallery.
    #   (b) INFLUENCER / community video — the "Videos for this product" /
    #       "Related videos" carousel further down the page. That section is
    #       LAZY-LOADED: it is NOT in the DOM until it scrolls into view, so the
    #       old code (which never scrolled there) always missed it. We scroll
    #       the relevant containers into view first, then detect.
    vid = ""
    vid_parts = []
    try:
        # (a) main gallery video
        for sel in ["li.videoThumbnail", "#main-video-container",
                    ".vse-video-thumbnail", "#videoCount",
                    "li[data-csa-c-element-type='video']",
                    "#altImages .videoThumbnail"]:
            if page.query_selector(sel):
                vid_parts.append("Main video")
                break

        # (b) influencer / community video — force the lazy section to load.
        try:
            for sel in ["#vse-related-videos", "#va-related-videos-widget",
                        "#customer-reviews-video", "[data-cel-widget*='video']"]:
                el = page.query_selector(sel)
                if el:
                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                        page.wait_for_timeout(600)
                    except:
                        pass
                    break
            else:
                # No anchor found yet — do a staged scroll to trigger lazy widgets,
                # then re-query (community video often sits near the reviews).
                for frac in (0.5, 0.75, 0.9):
                    try:
                        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight*{frac})")
                        page.wait_for_timeout(400)
                    except:
                        pass
        except:
            pass

        # Now detect a REAL influencer/community video section (rendered, not JS).
        infl = False
        for sel in ["#vse-related-videos .vse-video-thumbnail",
                    "#vse-related-videos li", "#va-related-videos-widget",
                    "div.vse-video-card", "[data-video-url]"]:
            try:
                if page.query_selector(sel):
                    infl = True
                    break
            except:
                continue
        if not infl:
            # heading-based confirmation ("Videos for this product")
            try:
                for hd in page.query_selector_all("h2, h3"):
                    t = (hd.inner_text() or "").lower()
                    if ("videos for this product" in t or "related videos" in t
                            or "videos related to" in t):
                        # only count if a video element actually sits under it
                        infl = True
                        break
            except:
                pass
        if infl:
            vid_parts.append("Influencer/community video")
    except:
        pass

    vid = " + ".join(vid_parts) if vid_parts else ""
    out["Listing Video"] = vid

    # ---------- VARIATION DATA ----------
    # A+C: capture the REAL variant names (twister button text) and the theme
    # label, not just a generic "Variations present" string. The validator
    # shows whatever lands in "Variation Data" for both the Variation (by ASIN)
    # and Variation Name Theme checks.
    var_parts = []
    variant_names = []

    # 0) PRIMARY: parse Amazon's variation JSON islands. Modern PDPs use the
    #    "inline-twister" widget (no #twisterContainer), so the DOM selectors
    #    below silently miss everything and we fall back to a useless
    #    "Variations present". These JSON blobs are always in the page source:
    #      - dimensionValuesDisplayData: { ASIN: [name], ... }  -> by-ASIN map
    #      - variationValues: { theme: [name, name, ...] }      -> theme + names
    #    This is exactly what the "Variation (by ASIN)" and "Variation Name
    #    Theme" checks need.
    try:
        page_html = page.content()
    except:
        page_html = ""

    try:
        # ASIN -> variant name map
        m = re.search(r'"dimensionValuesDisplayData"\s*:\s*(\{.*?\})\s*[,}]', page_html)
        asin_map = {}
        if m:
            try:
                asin_map = json.loads(m.group(1))
            except:
                # tolerant parse of "ASIN":["name"] pairs
                for am in re.finditer(r'"(B0[A-Z0-9]{8})"\s*:\s*\[\s*"([^"]+)"', m.group(1)):
                    asin_map[am.group(1)] = [am.group(2)]

        # theme label (e.g. "model", "color_name", "size_name") + ordered value
        # list. Amazon uses internal keys; map them to the readable basis the
        # "Variation Name Theme (Model/Type/Colour)" check expects.
        _THEME_LABELS = {
            "model": "Model", "model_name": "Model",
            "color_name": "Colour", "color": "Colour",
            "size_name": "Size", "size": "Size",
            "style_name": "Style", "style": "Style",
            "set_name": "Set / Type", "item_type_name": "Type",
            "flavor_name": "Flavour", "pattern_name": "Pattern",
        }
        theme = ""
        theme_raw = ""
        mv = re.search(r'"variationValues"\s*:\s*(\{.*?\})\s*[,}]', page_html)
        if mv:
            try:
                vv = json.loads(mv.group(1))
                if vv:
                    theme_raw = list(vv.keys())[0]
                    theme = _THEME_LABELS.get(theme_raw.lower(), theme_raw.replace("_", " ").title())
                    for v in vv.get(theme_raw, []):
                        nm = " ".join(str(v).split())
                        if nm and nm not in variant_names:
                            variant_names.append(nm)
            except:
                pass

        # Theme goes FIRST so the "Variation Name Theme" check reads the basis
        # (Model / Colour / Size / Type) up front.
        if theme:
            var_parts.append(f"Theme: {theme}")

        # If we got the by-ASIN map, render it explicitly: "ASIN = name" pairs.
        if asin_map:
            pairs = []
            for asin, names in asin_map.items():
                nm = names[0] if isinstance(names, list) and names else str(names)
                nm = " ".join(str(nm).split())
                pairs.append(f"{asin} = {nm}")
                if nm and nm not in variant_names:
                    variant_names.append(nm)
            var_parts.append(f"By ASIN ({len(pairs)}): " + "; ".join(pairs[:40]))

        if variant_names and not asin_map:
            var_parts.append(
                f"Variants ({len(variant_names)}): " + ", ".join(variant_names[:40])
            )

        if var_parts:
            out["Variation Data"] = " | ".join(var_parts)
            out["Variation Count"] = str(len(variant_names)) if variant_names else "present"
    except:
        pass

    # 1)-3) FALLBACK: legacy DOM twister scrape (only if JSON found nothing).
    if not out.get("Variation Data"):
      try:
        # 1) Per-dimension label + selected value + option count (e.g. "Size: M (5 options)")
        dims = page.query_selector_all("#twister .a-row, #twister_feature_div .a-section > .a-row")
        for d in dims:
            try:
                label = d.query_selector("label, .a-form-label")
                sel_val = d.query_selector(".selection, .a-dropdown-prompt")
                lt = (label.inner_text().strip().rstrip(":") if label else "")
                vt = (sel_val.inner_text().strip() if sel_val else "")
                opts = d.query_selector_all("li")
                n = len(opts)
                if lt or vt or n:
                    part = lt or "Variation"
                    if vt:
                        part += f": {vt}"
                    if n:
                        part += f" ({n} options)"
                    var_parts.append(part)
            except:
                continue

        # 2) The actual variant button/swatch NAMES — this is what the auditor
        #    needs to see (e.g. "Steam Cleaner - SC-01", "Accessory Kit for SC-04").
        #    Amazon renders each child as an <li> with title/alt text or inner text.
        name_sels = (
            "#twister li[title], #twister_feature_div li[title], "
            "#twisterContainer li[title], "
            "#twister .swatchAvailable, #twister .swatchSelect, "
            "#twister img[alt], #twister_feature_div img[alt]"
        )
        seen = set()
        for el in page.query_selector_all(name_sels):
            try:
                nm = (el.get_attribute("title") or el.get_attribute("alt") or el.inner_text() or "").strip()
                nm = " ".join(nm.split())  # collapse whitespace/newlines
                # skip junk: empty, pure numbers, "select", price-only strings
                if not nm or nm.lower() in ("select", "click to select") or len(nm) < 2:
                    continue
                if nm not in seen:
                    seen.add(nm)
                    variant_names.append(nm)
            except:
                continue

        # 3) "Set name:" / theme label shown above the swatches, if present.
        theme = ""
        try:
            tset = page.query_selector("#twisterPlusWWDevice .a-text-bold, .twisterTextDiv, #variation_set_name")
            if tset:
                theme = " ".join((tset.inner_text() or "").split()).strip()
        except:
            pass

        # Assemble: dimension summary first, then the concrete variant names.
        if variant_names:
            names_join = ", ".join(variant_names[:40])  # cap to avoid runaway rows
            var_parts.append(f"Variants ({len(variant_names)}): {names_join}")
        if theme:
            var_parts.append(f"Theme: {theme}")

        if not var_parts and page.query_selector("#twister, #twister_feature_div li"):
            var_parts.append("Variations present")
      except:
        pass
      out["Variation Data"] = " | ".join(var_parts)
      if var_parts and not out.get("Variation Count"):
        out["Variation Count"] = str(len(variant_names)) if variant_names else "present"

    # ---------- NEW SUPERSET SIGNALS (2026) ----------
    # Image count (reuse already-scraped Image URLs to stay consistent)
    try:
        imgs = [u for u in (out.get("Image URLs") or "").split("|") if u.strip()]
        out["Image Count"] = str(len(imgs))
    except Exception:
        out["Image Count"] = ""

    # Video count — count distinct video thumbnails on the PDP
    try:
        vc = 0
        for sel in ["#altImages li.videoThumbnail", "li.videoBlockIngress",
                    "li[data-csa-c-element-type='video']", ".vse-video-thumbnail"]:
            els = page.query_selector_all(sel)
            if els:
                vc = max(vc, len(els))
        out["Video Count"] = str(vc) if vc else ("1" if out.get("Listing Video") else "0")
    except Exception:
        out["Video Count"] = ""

    # A+ image count — REAL content images inside the A+ module only.
    # Old code did len(#aplus img) which counted 130+ items: spacer pixels,
    # lazy-load placeholders, sprites, and images bleeding in from outside the
    # module. A+ images are served from /images/S/aplus-media-library-service-media/
    # (NOT /images/I/ like the gallery), and each appears twice (placeholder +
    # real), so we dedup by the media UUID in the path.
    try:
        ap_container = page.query_selector("#aplus_feature_div") or page.query_selector("#aplus")
        ap_ids = set()
        if ap_container:
            for im in ap_container.query_selector_all("img"):
                src = (im.get_attribute("data-src")
                       or im.get_attribute("src")
                       or "")
                src = (src or "").split("?")[0].lower()
                if not src:
                    continue
                if any(j in src for j in _IMG_JUNK):
                    continue
                # Accept both A+ media and ordinary product images inside the module
                m = re.search(r"aplus-media-library-service-media/([a-f0-9\-]+)", src)
                if m:
                    ap_ids.add(m.group(1))
                elif "/images/i/" in src:
                    ap_ids.add(_image_id(src))
        out["A+ Image Count"] = str(len(ap_ids))
    except Exception:
        out["A+ Image Count"] = "0"

    # CS / Support QR / Warranty image AND Ours-vs-Their comparison image —
    # both are content baked into gallery images (not page text). One scan of
    # the gallery serves both checks, fetching each image only once.
    try:
        gallery_urls = [u for u in (out.get("Image URLs") or "").split("|") if u.strip()]
        scan = scan_gallery_content(page, gallery_urls)
        out["CS / Support QR / Warranty Image"] = scan.get("cs_qr", "")
        out["Ours vs Their Image"] = scan.get("ours_vs_theirs", "")
        out["What is in the Box Image"] = scan.get("whats_in_box_img", "")
        # soft text fallback for the support card if QR wasn't decoded
        if not out["CS / Support QR / Warranty Image"]:
            body = (page.inner_text("body") or "").lower()
            cues = ("scan to call", "scan to mail", "scan the code", "whatsapp",
                    "after sales support", "total care", "service details")
            if any(c in body for c in cues):
                out["CS / Support QR / Warranty Image"] = "Present"
    except Exception:
        out["CS / Support QR / Warranty Image"] = ""
        out["Ours vs Their Image"] = ""
        out["What is in the Box Image"] = ""

    # Brand Store — detect the "Visit the X Store" byline link, separate from Brand Story module
    bstore = ""
    try:
        for sel in ["#bylineInfo[href*='/stores/']", "a#brand[href*='/stores/']",
                    "a[href*='/stores/']", "#bylineInfo_feature_div a[href*='stores']"]:
            el = page.query_selector(sel)
            if el:
                href = (el.get_attribute("href") or "")
                if "/stores/" in href or "store" in (el.inner_text() or "").lower():
                    bstore = "Present"
                    break
    except Exception:
        pass
    out["Brand Store"] = bstore

    # Variation count — number of variation options detected on the twister
    try:
        m = re.findall(r"\((\d+)\s*options\)", out.get("Variation Data") or "")
        out["Variation Count"] = str(max((int(x) for x in m), default=0)) if m else (
            "present" if out.get("Variation Data") else "0")
    except Exception:
        out["Variation Count"] = ""

    out["Product URL"] = f"https://www.amazon.in/dp/{asin}"

    rand_sleep()
    return out


# ------------------------ CHECKPOINTING ------------------------
def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_checkpoint(data):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ------------------------ MAIN ------------------------
def main():
    print("Amazon.in Deep Crawler (Playwright) — FINAL")

    if not INPUT_XLSX.exists():
        print("Input XLSX missing:", INPUT_XLSX)
        return

    # Read ONLY Sheet1 of the input file. Expected columns: ASIN, status, URL,
    # Brand. The URL column holds the exact product URL to crawl (pins the right
    # variation); Brand comes straight from the input so we don't rely on the
    # byline. status/other columns are ignored here.
    df = pd.read_excel(INPUT_XLSX, sheet_name="Sheet1")
    df = df.rename(columns=lambda x: str(x).strip())

    if "ASIN" not in df.columns:
        print("Input Sheet1 must contain an ASIN column")
        return

    rows = df.to_dict(orient="records")

    checkpoint = load_checkpoint()
    processed = set(checkpoint.get("done_asins", []))
    results = checkpoint.get("results", [])

    with sync_playwright() as p:
        # Persistent context keeps your Amazon login (cookies) on disk in
        # PROFILE_DIR, so the crawler sees the SAME logged-in PDP you see.
        _launch_kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="en-IN",
            viewport={"width": 1366, "height": 900},
        )
        if SYSTEM_CHROME:
            _launch_kwargs["executable_path"] = SYSTEM_CHROME
        context = p.chromium.launch_persistent_context(**_launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()

        # One-time manual login. If not logged in, pause and let the user sign in
        # by hand in the opened window; the session is then saved for future runs.
        if REQUIRE_LOGIN:
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
                        break

        total = len(rows)
        idx = 0

        for r in rows:
            idx += 1
            asin = str(r.get("ASIN")).strip()
            sku = str(r.get("SKU") or r.get("sku") or "")
            in_url = str(r.get("URL") or r.get("url") or "").strip()
            in_brand = str(r.get("Brand") or r.get("brand") or "").strip()

            if not asin:
                continue

            if asin in processed:
                print(f"[{idx}/{total}] Skipping {asin} (already done)")
                continue

            try:
                print(f"[{idx}/{total}] Scraping {asin} (SKU: {sku}) ...")

                out = scrape_asin_playwright(page, asin, sku, url=in_url)
                # Use the brand supplied in the input (clean) when present;
                # otherwise keep whatever the page byline gave us.
                if in_brand:
                    out["Brand"] = in_brand
                results.append(out)
                processed.add(asin)

                save_checkpoint({"done_asins": list(processed), "results": results})

                df_out = pd.DataFrame(results).reindex(columns=COLUMNS)
                df_out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
                df_out.to_excel(OUTPUT_XLSX, index=False)

                print(f"  ✓ Completed {asin}")

            except Exception as e:
                print(f"  ❌ Error scraping {asin}: {e}")
                save_checkpoint({"done_asins": list(processed), "results": results})
                continue

        context.close()

    # ---- Persist to database (history + latest), best-effort ----
    try:
        import hygiene_db
        hygiene_db.init_db()
        run_label = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        n = hygiene_db.save_rows(results, crawl_run=run_label)
        hygiene_db.upsert_latest(results, crawl_run=run_label)
        print(f"DB  : {hygiene_db.backend_name()} — saved {n} rows (run {run_label})")
    except Exception as e:
        print(f"DB  : skipped ({e}) — CSV/XLSX still written")

    print("\n✔ DONE")
    print("CSV :", OUTPUT_CSV)
    print("XLSX:", OUTPUT_XLSX)
    print("Checkpoint:", CHECKPOINT_FILE)


if __name__ == "__main__":
    main()
