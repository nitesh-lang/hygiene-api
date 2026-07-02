import{useState,useEffect,useCallback,useMemo,useRef}from"react";
import*as XLSX from"xlsx";
import Papa from"papaparse";

/* AMAZON LISTING HYGIENE TOOL v3.11 — 45 Checks · 2026 All-Brand Superset · Backend-from-input · Rating<4 flag · Corrections DB */

if(!document.getElementById("hyg-fonts")){const l=document.createElement("link");l.id="hyg-fonts";l.href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap";l.rel="stylesheet";document.head.appendChild(l);}

const LS="hyg7";
function ldLS(){try{const s=localStorage.getItem(LS);return s?JSON.parse(s):null;}catch{return null;}}
function svLS(d){try{localStorage.setItem(LS,JSON.stringify(d));return{ok:true};}catch(e){return{ok:false,err:(e&&e.name)||"Error"};}}

/* ─────────────────────────────────────────────────────────────────────────
   SHARED BACKEND (Render API + Postgres)
   The validator still keeps its own localStorage copy of everything (offline
   safety), but "Mark Done" now ALSO writes to the shared database so the whole
   team sees the same done-list and nobody re-validates the same ASIN.
   ───────────────────────────────────────────────────────────────────────── */
const API="https://hygiene-api.onrender.com";

// POST a completed validation to the shared DB. Fire-and-forget: never blocks
// the UI, and a network failure leaves the local copy intact.
async function apiMarkDone({asin,validated_by,check_results,notes,brand}){
  try{
    const r=await fetch(`${API}/validate`,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({asin,validated_by:validated_by||"",check_results:check_results||{},notes:notes||"",brand:brand||""}),
    });
    return r.ok;
  }catch(e){return false;}
}

// GET the set of ASINs already validated by anyone, so we can show them as done.
async function apiFetchDone(){
  try{
    const r=await fetch(`${API}/done`);
    if(!r.ok)return null;
    return await r.json(); // array of asins
  }catch(e){return null;}
}


function ss(v){if(v==null)return"";const s=String(v).trim();return["nan","none","nat","undefined","null","#n/a","n/a","#ref!","#value!"].includes(s.toLowerCase())?"":s;}
function nm(s){return ss(s).toLowerCase().replace(/\s+/g," ").trim();}
function strip(s){return ss(s).replace(/[^a-zA-Z0-9]/g,"").toLowerCase();}
// Collapse phone numbers so "79961 35111" == "7996135111". Returns array of digit-runs (len>=8, last 10 digits).
function phoneList(s){return [...new Set((ss(s).match(/[\d][\d\s]{6,}\d/g)||[]).map(x=>x.replace(/\D/g,"")).filter(d=>d.length>=8).map(d=>d.slice(-10)))];}
function phonesOverlap(a,b){const pa=phoneList(a),pb=phoneList(b);if(!pa.length||!pb.length)return false;return pa.some(x=>pb.includes(x));}
// Loose text equality: exact-normalized, OR shared phone number + some address overlap (handles spacing/format/extra-number differences in contact blocks).
function textEqLoose(a,b){const na=nm(a),nb=nm(b);if(na===nb)return true;if(phonesOverlap(a,b)&&simRatio(a,b)>=0.35)return true;return false;}
// Token-overlap similarity 0..1 (Jaccard on words >2 chars). Used for fuzzy text matches.
function simRatio(a,b){
  const ta=new Set(nm(a).split(/[^a-z0-9]+/).filter(w=>w.length>2));
  const tb=new Set(nm(b).split(/[^a-z0-9]+/).filter(w=>w.length>2));
  if(!ta.size&&!tb.size)return 1;
  if(!ta.size||!tb.size)return 0;
  let inter=0;ta.forEach(w=>{if(tb.has(w))inter++;});
  return inter/(ta.size+tb.size-inter);
}
// Normalize category trees so > and › and spacing don't cause false fails.
function normNod(s){return ss(s).replace(/[›>\u203A]/g,">").split(">").map(x=>nm(x)).filter(Boolean).join(">");}

// Warranty: treat a bare digit in the input as "N year". So input "1" matches crawl "1 year warranty".
function warrantyYears(s){
  const t=nm(s);
  // explicit "N year(s)" / "N yr"
  let m=t.match(/(\d+)\s*(year|yr)/);
  if(m)return parseInt(m[1]);
  // explicit months → convert to years when a whole-year multiple (12mo=1yr, 24mo=2yr),
  // otherwise keep as fractional years so 6mo != 1yr.
  m=t.match(/(\d+)\s*month/);
  if(m){const mo=parseInt(m[1]);return mo/12;}
  // bare number alone (input sheet often just has "1","2","3")
  m=t.match(/^\s*(\d+)\s*$/);
  if(m)return parseInt(m[1]);
  return null;
}
// Return policy: returnable and replaceable are DIFFERENT. Both type and day-count must match.
function returnKind(s){const t=nm(s);const repl=/replace/.test(t);const ret=/return/.test(t);const days=(t.match(/(\d+)\s*day/)||[])[1]||"";return{repl,ret,days};}
// Ratings band by range: 4.5+ excellent, 4.0–4.4 good, 3.0–3.9 ok, 2.0–2.9 mixed, <2 poor.
function ratingBand(s){const m=ss(s).match(/(\d+(?:\.\d+)?)/);if(!m)return"";const v=parseFloat(m[1]);if(isNaN(v))return"";if(v>=4.5)return"excellent";if(v>=4.0)return"good";if(v>=3.0)return"ok";if(v>=2.0)return"mixed";return"poor";}
// Resolve a rating value (number OR word) to its band word, for matching both sides.
function ratingToBand(s){const t=nm(s);if(["excellent","good","ok","mixed","poor"].includes(t))return t;return ratingBand(s);}
function sn(v){const s=ss(v).replace(/[₹,Rs.\s‎]/g,"");const m=s.match(/[\d]+\.?\d*/);return m?parseFloat(m[0]):null;}
// Pull every number out of a string for unit-agnostic comparison.
// "8 x 8 x 4 Centimeters" -> [8,8,4]; "8*8*4" -> [8,8,4]; "270 Grams" -> [270]; "270" -> [270]
function numSeq(s){const m=ss(s).match(/\d+\.?\d*/g);return m?m.map(Number):[];}
function numSeqEq(a,b){const x=numSeq(a),y=numSeq(b);if(!x.length||!y.length)return false;if(x.length!==y.length)return false;return x.every((n,i)=>Math.abs(n-y[i])<0.01);}
// Weight in grams: "4 Kilograms"/"4 kg"=4000, "4000 g"/"4000"=4000, so 4kg matches 4000g.
function toGrams(s){const t=ss(s).toLowerCase();const m=t.match(/(\d+\.?\d*)/);if(!m)return null;const v=parseFloat(m[1]);if(/\bkg\b|kilo/.test(t))return v*1000;if(/\bmg\b/.test(t))return v/1000;return v;/* g or bare number */}
function weightEq(a,b){const x=toGrams(a),y=toGrams(b);if(x==null||y==null)return false;
  // accept either same grams OR same raw number (in case both are in the same unit already)
  if(Math.abs(x-y)<1)return true;const na=numSeq(a)[0],nb=numSeq(b)[0];return na!=null&&nb!=null&&Math.abs(na-nb)<0.01;}
// Dimensions: convert each number to mm using a single trailing unit, compare as a set.
function toMM(val,unit){if(/\bcm\b|centimet/.test(unit))return val*10;if(/\bm\b|meter/.test(unit)&&!/\bmm\b/.test(unit))return val*1000;if(/\bmm\b|millimet/.test(unit))return val;if(/\binch|"\b|in\b/.test(unit))return val*25.4;return val;}
function dimsEq(a,b){
  const na=numSeq(a),nb=numSeq(b);if(!na.length||!nb.length||na.length!==nb.length)return false;
  const ua=ss(a).toLowerCase(),ub=ss(b).toLowerCase();
  // Convert to mm then compare as a SORTED set — Amazon lists dimensions as
  // D x W x H while the input sheet often uses W x D x H (or L x W x H), so the
  // SAME box fails a position-by-position compare. Sorting makes order irrelevant.
  const ma=na.map(n=>toMM(n,ua)).sort((x,y)=>x-y),mb=nb.map(n=>toMM(n,ub)).sort((x,y)=>x-y);
  if(ma.every((n,i)=>Math.abs(n-mb[i])<1))return true;
  // also accept raw-number match (same unit on both sides), order-independent
  const ra=[...na].sort((x,y)=>x-y),rb=[...nb].sort((x,y)=>x-y);
  return ra.every((n,i)=>Math.abs(n-rb[i])<0.01);}
function isY(v){const n=nm(v);return["y","yes","correct","updated","true"].includes(n);}
function isN(v){const n=nm(v);return["n","no","n0","incorrect","false","0"].includes(n);}
// Contact/address match: addresses are formatted differently on the PDP vs the
// input sheet, so whole-string similarity is unreliable. Instead match on stable
// ANCHORS — 6-digit pincode, email, and the last 6 digits of any phone number.
// If the crawl value carries at least one of the input's anchors, it's the same
// entity (PASS). If crawl has content but none of the anchors, it's a possible
// mismatch (REVIEW, not a hard FAIL — a human confirms).
function contactAnchors(s){const t=ss(s).toLowerCase();const out=new Set();
  (t.match(/\b\d{6}\b/g)||[]).forEach(x=>out.add(x));
  (t.match(/[\w.+-]+@[\w.-]+/g)||[]).forEach(x=>out.add(x));
  (t.match(/\d{5}/g)||[]).forEach(x=>out.add(x)); // 5-digit phone tail
  return out;}
function contactEq(crawl,input){const c=ss(crawl),i=ss(input);
  if(!c&&!i)return"REVIEW";if(c&&!i)return"PASS";if(!c&&i)return"FAIL";
  const ca=contactAnchors(c),ia=contactAnchors(i);
  if(ia.size===0)return simRatio(nm(c),nm(i))>=0.5?"PASS":"REVIEW";
  for(const a of ia){if(ca.has(a))return"PASS";}
  return"REVIEW";}
function pipeC(s){const v=ss(s);return v?v.split("|").filter(x=>x.trim()).length:0;}
function cleanB(b){return ss(b).replace(/^Visit the\s+/i,"").replace(/\s+Store$/i,"").replace(/^Brand:\s*/i,"").trim();}
const EXCL=new Set(["tonor","coleshome","n"]);

// Title format check: expects Brand_Model-Name_Product-type_Specs — i.e. starts with brand,
// contains the model code, and uses pipe/spec separators. Heuristic, not strict.
function titleFormatOk(title,brand,model){
  const t=ss(title);if(!t)return false;
  const tn=nm(t);
  const b=nm(cleanB(brand));
  const m=nm(model).replace(/\s+/g,"");
  const startsBrand=b?tn.startsWith(b.split(" ")[0]):true;
  const hasModel=m?strip(t).includes(strip(model)):true;
  const hasSpecs=/[|\u2502]/.test(t)||/\d/.test(t); // pipe-separated spec blocks or numeric specs
  return startsBrand&&hasModel&&hasSpecs;
}

// ═══ INPUT SHEET PARSER ═══
function parseInput(wb){
  const order=[...wb.SheetNames].sort((a,b)=>{
    if(a.toLowerCase()==="format")return-1;if(b.toLowerCase()==="format")return 1;
    if(a.toLowerCase().includes("format"))return-1;if(b.toLowerCase().includes("format"))return 1;return 0;
  });
  let best=null; // {rows, fillScore}
  for(const sn of order){
    const ws=wb.Sheets[sn];if(!ws)continue;
    const raw=XLSX.utils.sheet_to_json(ws,{header:1,defval:""});
    if(raw.length<3)continue;
    let hR=-1;
    for(let i=0;i<Math.min(6,raw.length);i++){
      const up=raw[i].map(v=>ss(v).toUpperCase());
      if(up.includes("ASIN")&&(up.includes("SKU")||up.join(" ").includes("TITLE")||up.join(" ").includes("NODDING"))){hR=i;break;}
    }
    if(hR===-1)continue;
    const headers=raw[hR].map(v=>ss(v));
    const ai=headers.findIndex(h=>h.toUpperCase()==="ASIN");
    if(ai===-1)continue;
    const rows=[];let filled=0;
    for(let i=hR+1;i<raw.length;i++){
      const asin=ss(raw[i]?.[ai]).toUpperCase().trim();
      if(!asin||asin.length<5)continue;
      const obj={_asin:asin,_sheet:sn};
      for(let j=0;j<headers.length;j++){
        const h=headers[j];
        if(h){const key=nm(h);const val=ss(raw[i]?.[j]);if(val){obj[key]=val;filled++;}}
      }
      rows.push(obj);
    }
    if(rows.length>0){
      // fillScore = avg populated reference cells per ASIN row → favors the well-aligned sheet.
      const fillScore=filled/rows.length;
      if(!best||fillScore>best.fillScore)best={rows,fillScore};
    }
  }
  return best?best.rows:[];
}

function gv(ir,...pats){
  if(!ir)return"";
  const keys=Object.keys(ir);
  for(const pat of pats){
    const p=pat.toLowerCase();
    const k1=keys.find(k=>k.startsWith(p));if(k1&&ir[k1])return ss(ir[k1]);
    const k2=keys.find(k=>k.includes(p));if(k2&&ir[k2])return ss(ir[k2]);
  }
  return"";
}

function parseCrawl(text){return Papa.parse(text,{header:true,skipEmptyLines:true}).data.filter(r=>ss(r.ASIN).length>=5);}

function boxFromBullets(bullets){
  const text=ss(bullets);
  if(!text)return"";
  const parts=text.split(/\n|•|\u2022|\r|(?=\bWhat\s+is\s+In\s+the\s+Box\b)|(?=\bWhat'?s\s+in\s+the\s+Box\b)/i).map(x=>ss(x)).filter(Boolean);
  const hit=parts.find(x=>/what\s+is\s+in\s+the\s+box|what'?s\s+in\s+the\s+box|box\s+contents/i.test(x));
  return hit||(/what\s+is\s+in\s+the\s+box|what'?s\s+in\s+the\s+box/i.test(text)?"Present":"");
}

// ═══ 45 CHECKS (2026 superset — all-brand attributes) ═══
const CK=[
  {id:"asin_active_1p",name:"1P ASIN Active (Y/N) (manual)",group:"P0 · ASIN Active",p:0},
  {id:"asin_active",name:"3P ASIN Active (Y/N)",group:"P0 · ASIN Active",p:0},
  {id:"nodding",name:"Nodding",group:"P1 · Noding",p:1},
  {id:"title",name:"Title",group:"P2 · Title Loading",p:2},
  {id:"bullets_avail",name:"Bullets Available",group:"P3 · Bullet Points",p:3},
  {id:"bullets_kw",name:"Bullets: Highlight Benefits & Include Keywords",group:"P3 · Bullet Points",p:3},
  {id:"bullets_box",name:"What is In the Box (manual)",group:"P3 · Bullet Points",p:3},
  {id:"warranty",name:"Warranty",group:"P3 · Bullet Points",p:3},
  {id:"warranty_bullet",name:"Warranty on Bullet Point (manual)",group:"P3 · Bullet Points",p:3},
  {id:"warranty_desc",name:"Warranty Description",group:"P4 · Warranty Description",p:4},
  {id:"brand_story",name:"Brand Story (manual)",group:"P5 · Brand Story",p:5},
  {id:"brand_store",name:"Brand Store",group:"P5 · Brand Story",p:5},
  {id:"cs_wa_qr_story",name:"CS WA QR Code in Brand Story (manual)",group:"P5 · Brand Story",p:5},
  {id:"colour",name:"Colour",group:"P6 · Color",p:6},
  {id:"weight",name:"Weight",group:"P7 · Product Weight",p:7},
  {id:"dimensions",name:"Dimensions",group:"P8 · Dimensions",p:8},
  {id:"material",name:"Material",group:"P9 · Material",p:9},
  {id:"addl_features",name:"Addl Features",group:"P10 · Additional Features",p:10},
  {id:"manufacturer",name:"Manufacturer",group:"P10 · Additional Features",p:10},
  {id:"packer",name:"Packer (manual)",group:"P10 · Additional Features",p:10},
  {id:"importer",name:"Importer (manual)",group:"P10 · Additional Features",p:10},
  {id:"backend_kw",name:"Backend Keywords",group:"P11 · Backend Search Terms",p:11},
  {id:"return_policy",name:"Return Policy (returnable ≠ replaceable)",group:"P12 · Return Policy",p:12},
  {id:"fee_category",name:"Fee Category",group:"P13 · Referral Category",p:13},
  {id:"ref_fees",name:"Ref Fees (backend)",group:"P13 · Referral Category",p:13},
  {id:"variation",name:"Variation (by ASIN)",group:"P14 · Variation",p:14},
  {id:"variation_theme",name:"Variation Name Theme (Model/Type/Colour) (manual)",group:"P14 · Variation",p:14},
  {id:"images_5",name:"Images ≥ 5",group:"P15 · Images",p:15},
  {id:"feature_img",name:"Feature Images",group:"P15 · Images",p:15},
  {id:"lifestyle_img",name:"Lifestyle Images",group:"P15 · Images",p:15},
  {id:"cs_image",name:"CS / Support QR / Warranty Image (manual)",group:"P16 · Customer Support Image",p:16},
  {id:"box_image",name:"What is in the Box Image (manual)",group:"P16 · Customer Support Image",p:16},
  {id:"box_contents",name:"Box Contents (What's in the box)",group:"P16b · Box Contents",p:16},
  {id:"ours_vs_their",name:"Ours vs Their (manual)",group:"P18 · Ours vs Their Image",p:18},
  {id:"listing_video",name:"Listing Video (incl. Influencer Video) (manual)",group:"P19 · Listing Video",p:19},
  {id:"nce",name:"NCE (SP > ₹1500)",group:"P20 · Y/M",p:20},
  {id:"ratings_reviews",name:"Ratings",group:"P20 · Y/M",p:20},
  {id:"reviews",name:"Reviews (count)",group:"P20 · Y/M",p:20},
  {id:"aplus",name:"A+ Content (manual)",group:"P21 · A+ Y/M",p:21},
  {id:"description",name:"Description (manual)",group:"P21 · A+ Y/M",p:21},
  {id:"comp_remarks",name:"Competitor Remarks — Nodding / Fee (manual)",group:"P22 · Competitor",p:22},
  {id:"comp_crosscheck",name:"Cross-check Main Competitors (manual)",group:"P22 · Competitor",p:22},
  {id:"comp_policy",name:"Policy-change Competitor ASINs (manual)",group:"P22 · Competitor",p:22},
];

// Checks that are ALWAYS manual — they show as REVIEW for a human to decide, never auto-pass/fail.
const MANUAL_CHECKS=new Set(["bullets_box","warranty_bullet","brand_story","packer","importer","cs_image","ours_vs_their","listing_video","aplus","description","variation","asin_active_1p","cs_wa_qr_story","variation_theme","box_image","comp_remarks","comp_crosscheck","comp_policy","lifestyle_img"]);

// ═══ RE-DECIDE STATUS for a single check ═══
function reDecide(id, crawlVal, inputVal, mode){
  if(MANUAL_CHECKS.has(id))return"REVIEW";
  const c=ss(crawlVal), inp=ss(inputVal);
  // Backend-only fields (fee category, search terms): crawler can't see backend, status comes from input sheet alone.
  if(mode==="backend"){if(!inp)return"REVIEW";if(isY(inp))return"PASS";if(isN(inp))return"FAIL";return"REVIEW";}
  // Ratings with blank input: <4.0 needs attention (FAIL), ≥4.0 healthy (PASS).
  if(mode==="rating"&&c&&!inp){const m=c.match(/(\d+(?:\.\d+)?)/);const v=m?parseFloat(m[1]):NaN;return isNaN(v)?"REVIEW":(v>=4?"PASS":"FAIL");}
  // Reviews: based on crawled review count alone (>0 = PASS).
  if(mode==="reviews"){const n=parseInt(ss(c).replace(/[^0-9]/g,""))||0;return n>0?"PASS":"FAIL";}
  // Title format & 3P-active: input-driven Y/N where applicable.
  if(mode==="titlefmt"){if(isY(inp))return"PASS";if(isN(inp))return"FAIL";return c?"PASS":"REVIEW";}
  if(mode==="active"){if(isY(inp))return c?"PASS":"FAIL";if(isN(inp))return c?"REVIEW":"PASS";return c?"PASS":"REVIEW";}
  if(mode==="contact")return contactEq(c,inp);
  if(!c&&!inp)return"REVIEW";
  // Crawl has a value but reference is blank: for descriptive attributes there's nothing to contradict, so PASS (not a forced human decision).
  const CRAWL_ONLY_PASS=new Set(["yn","text","weight","dims","num","img5"]);
  if(c&&!inp)return CRAWL_ONLY_PASS.has(mode)?"PASS":"REVIEW";
  if(!c||!inp)return"REVIEW";
  switch(mode){
    case"nodding":return normNod(c)===normNod(inp)?"PASS":"FAIL";
    case"title":{const cn=nm(c),hn=nm(inp);if(cn===hn)return"PASS";return simRatio(cn,hn)>=0.85?"PASS":"FAIL";}
    case"warranty":{const cy=warrantyYears(c),hy=warrantyYears(inp);if(cy!==null&&hy!==null)return cy===hy?"PASS":"FAIL";const cn=nm(c),hn=nm(inp);return(cn===hn||simRatio(cn,hn)>=0.6)?"PASS":"FAIL";}
    case"return":{const a=returnKind(c),b=returnKind(inp);if(a.repl!==b.repl||a.ret!==b.ret)return"FAIL";if(a.days&&b.days&&a.days!==b.days)return"FAIL";return"PASS";}
    case"rating":{const rv=parseFloat((ss(c).match(/(\d+(?:\.\d+)?)/)||[])[1]);if(!isNaN(rv)&&rv<4)return"FAIL";const cb=ratingToBand(c),hb=ratingToBand(inp);if(cb&&hb)return cb===hb?"PASS":"FAIL";return cb?"PASS":"REVIEW";}
    case"yn":{
      if(isY(inp))return c.length>0?"PASS":"FAIL";
      if(isN(inp))return c.length===0?"PASS":"REVIEW";
      const cn=nm(c),hn=nm(inp);
      return(cn===hn||textEqLoose(c,inp)||simRatio(cn,hn)>=0.6)?"PASS":"FAIL";
    }
    case"text":{const cn=nm(c),hn=nm(inp);if(cn===hn)return"PASS";if(textEqLoose(c,inp))return"PASS";return simRatio(cn,hn)>=0.6?"PASS":"FAIL";}
    case"num":return numSeqEq(c,inp)?"PASS":"FAIL";
      case"weight":return weightEq(c,inp)?"PASS":"FAIL";
      case"dims":return dimsEq(c,inp)?"PASS":"FAIL";
    case"img5":return(parseInt(c)||0)>=5?"PASS":"FAIL";
    case"nce":return"REVIEW";
    default:return nm(c)===nm(inp)?"PASS":"FAIL";
  }
}

// Check mode lookup
const CHECK_MODES={asin_active_1p:"yn",asin_active:"active",nodding:"nodding",title:"title",title_format:"titlefmt",bullets_avail:"yn",bullets_kw:"yn",bullets_box:"yn",warranty:"warranty",warranty_bullet:"yn",warranty_desc:"text",brand_story:"yn",brand_store:"yn",cs_wa_qr_story:"yn",colour:"yn",weight:"weight",dimensions:"dims",material:"yn",addl_features:"yn",manufacturer:"contact",packer:"yn",importer:"yn",backend_kw:"backend",return_policy:"return",fee_category:"backend",ref_fees:"backend",variation:"yn",variation_theme:"yn",images_5:"img5",feature_img:"yn",lifestyle_img:"yn",cs_image:"yn",box_image:"yn",box_contents:"yn",ours_vs_their:"yn",listing_video:"yn",nce:"nce",ratings_reviews:"rating",reviews:"reviews",aplus:"yn",description:"yn",comp_remarks:"yn",comp_crosscheck:"yn",comp_policy:"yn"};

// ═══ VALIDATION ═══
function validate(cr,ir){
  const R=[];
  const cv=k=>ss(cr[k]||"");
  const iv=(...p)=>gv(ir,...p);

  function decide(id,c,inp,mode){
    if(MANUAL_CHECKS.has(id))return"REVIEW";
    // Backend-only fields: status from input sheet alone (Y/Correct = PASS, N/Incorrect = FAIL).
    if(mode==="backend"){if(!inp)return"REVIEW";if(isY(inp))return"PASS";if(isN(inp))return"FAIL";return"REVIEW";}
    // Ratings with blank input: <4.0 = FAIL (push reviews), ≥4.0 = PASS.
    if(mode==="rating"&&c&&!inp){const m=c.match(/(\d+(?:\.\d+)?)/);const v=m?parseFloat(m[1]):NaN;return isNaN(v)?"REVIEW":(v>=4?"PASS":"FAIL");}
    // Reviews: pass when crawled review count > 0.
    if(mode==="reviews"){const n=parseInt(ss(c).replace(/[^0-9]/g,""))||0;return n>0?"PASS":"FAIL";}
    // Title format: input Y/N if provided, else PASS when a crawled title exists.
    if(mode==="titlefmt"){if(isY(inp))return"PASS";if(isN(inp))return"FAIL";return c?"PASS":"REVIEW";}
    // 3P ASIN active: crawled live PDP confirms active; reconcile with input Y/N.
    if(mode==="active"){if(isY(inp))return c?"PASS":"FAIL";if(isN(inp))return c?"REVIEW":"PASS";return c?"PASS":"REVIEW";}
    if(mode==="contact")return contactEq(c,inp);
    if(!c&&!inp)return"REVIEW";
    const CRAWL_ONLY_PASS2=new Set(["yn","text","weight","dims","num","img5"]);
    if(c&&!inp)return CRAWL_ONLY_PASS2.has(mode)?"PASS":"REVIEW";
    if(!c||!inp)return"REVIEW";
    switch(mode){
      case"nodding":return normNod(c)===normNod(inp)?"PASS":"FAIL";
      case"title":{const cn=nm(c),hn=nm(inp);if(cn===hn)return"PASS";return simRatio(cn,hn)>=0.85?"PASS":"FAIL";}
      case"warranty":{const cy=warrantyYears(c),hy=warrantyYears(inp);if(cy!==null&&hy!==null)return cy===hy?"PASS":"FAIL";const cn=nm(c),hn=nm(inp);return(cn===hn||simRatio(cn,hn)>=0.6)?"PASS":"FAIL";}
      case"return":{const a=returnKind(c),b=returnKind(inp);if(a.repl!==b.repl||a.ret!==b.ret)return"FAIL";if(a.days&&b.days&&a.days!==b.days)return"FAIL";return"PASS";}
      case"rating":{const rv=parseFloat((ss(c).match(/(\d+(?:\.\d+)?)/)||[])[1]);if(!isNaN(rv)&&rv<4)return"FAIL";const cb=ratingToBand(c),hb=ratingToBand(inp);if(cb&&hb)return cb===hb?"PASS":"FAIL";return cb?"PASS":"REVIEW";}
      case"yn":{
        if(isY(inp))return c.length>0?"PASS":"FAIL";
        if(isN(inp))return c.length===0?"PASS":"REVIEW";
        const cn=nm(c),hn=nm(inp);
        return(cn===hn||textEqLoose(c,inp)||simRatio(cn,hn)>=0.6)?"PASS":"FAIL";
      }
      case"text":{const cn=nm(c),hn=nm(inp);if(cn===hn)return"PASS";if(textEqLoose(c,inp))return"PASS";return simRatio(cn,hn)>=0.6?"PASS":"FAIL";}
      case"num":return numSeqEq(c,inp)?"PASS":"FAIL";
      case"weight":return weightEq(c,inp)?"PASS":"FAIL";
      case"dims":return dimsEq(c,inp)?"PASS":"FAIL";
      case"img5":return(parseInt(c)||0)>=5?"PASS":"FAIL";
      case"nce":{const sp=sn(cv("Selling Price"));if(isY(inp))return(sp&&sp>1500)?"PASS":"FAIL";if(isN(inp))return(sp&&sp<=1500)?"PASS":"FAIL";return"REVIEW";}
      default:return nm(c)===nm(inp)?"PASS":"FAIL";
    }
  }
  function add(id,c,inp,mode="yn"){const a=ss(c),b=ss(inp);R.push({id,crawlVal:a,inputVal:b,origInputVal:b,status:decide(id,a,b,mode)});}

  add("asin_active_1p","",iv("1 p asin active","1p asin active"),"yn");
  // 3P active now uses the crawler's real Stock Status / Listing Status columns
  // (falls back to Title/Sold-By presence for older crawl files). A redirect to
  // a different variation ASIN is surfaced so the row isn't silently trusted.
  const stockStatus=cv("Stock Status"), listingStatus=cv("Listing Status");
  const redirectFlag=ss(cv("ASIN Redirect")).toUpperCase()==="YES";
  const crawledAsin=cv("Crawled ASIN");
  let activeVal="";
  if(listingStatus) activeVal=listingStatus;
  else if(stockStatus) activeVal=stockStatus;
  else if(cv("Title")||cv("Sold By")) activeVal="Live";
  if(redirectFlag&&crawledAsin) activeVal=`⚠ REDIRECTED to ${crawledAsin} — ${activeVal||"data unreliable"}`;
  // Out-of-stock or dead listings should not silently PASS as active.
  const activeForDecide=(/out of stock|dead|suppressed|unavailable/i.test(activeVal))?"":activeVal;
  add("asin_active",redirectFlag?"":activeForDecide,iv("asin active(yes","asin active"),"active");
  add("nodding",cv("Category Tree"),iv("correct nodding","nodding on pdp"),"nodding");
  add("title",cv("Title"),iv("title name"),"title");
  const bul=cv("Bullets");
  add("bullets_avail",bul||"",iv("bullet points available"),"yn");
  add("bullets_kw",bul||"",iv("bullets highlight benefit","bullets highlight benefits"),"yn");
  add("bullets_box",cv("What is in the box?")||boxFromBullets(bul),iv("what is in the box in bullet","what is in the box"),"yn");
  add("warranty",cv("Warranty Policy")||cv("Warranty Description"),iv("correct warranty","warranty  on panel","warranty on panel"),"warranty");
  add("warranty_bullet",/warrant/i.test(bul)?"Present in bullets":"Not in bullets",iv("warranty on bullet point","warranty on bullet points"),"yn");
  add("warranty_desc",cv("Warranty Description")||cv("Warranty Policy"),iv("warranty  on panel","warranty on panel","warranty description"),"text");
  add("brand_story",cv("Brand Story"),iv("product added in brand story","brand story"),"yn");
  add("brand_store",cv("Brand Store")||(cv("Brand Story")?"Present (story only)":""),iv("product added in brand store","brand store","from the brand section"),"yn");
  add("cs_wa_qr_story","",iv("cs wa qr code in brand story","cs wa qr"),"yn");
  add("colour",cv("Colour"),iv("colour","color"),"yn");
  add("weight",cv("Weight"),iv("iteam weight","item weight"),"weight");
  add("dimensions",cv("Dimensions"),iv("dimensions"),"dims");
  {const _mat=cv("Material");R.push({id:"material",crawlVal:_mat?_mat:"Not present",inputVal:ss(iv("material")),origInputVal:ss(iv("material")),status:_mat?decide("material",_mat,ss(iv("material")),"yn"):"FAIL"});}
  add("addl_features",cv("Additional Features"),iv("additional feature"),"yn");
  add("manufacturer",cv("Manufacturer Contact Information"),iv("mfg detail","manufacturer"),"contact");
  add("packer",cv("Packer Contact Information"),iv("packer detail","packer"),"yn");
  add("importer",cv("Importer Contact Information"),iv("importer detail","importer"),"yn");
  add("backend_kw","",iv("backend search term","search terms (y"),"backend");
  add("return_policy",cv("Return Policy"),iv("correct return policy","return policy on page"),"return");
  add("fee_category","",iv("fee category in backend","correct fee category"),"backend");
  add("ref_fees","",iv("ref fees in the backend","ref fees in the backedn","correct ref fees"),"backend");
  add("variation",cv("Variation Data")||(cv("Variation Count")&&cv("Variation Count")!=="0"?`${cv("Variation Count")} variations`:""),iv("correct variation","variation on pdp","variation"),"yn");
  add("variation_theme",cv("Variation Data"),iv("variation name basis on theme","variation name theme"),"yn");
  const ic=pipeC(cv("Image URLs"))||parseInt(cv("Image Count"))||0;
  add("images_5",String(ic),iv("images - minimum"),"img5");
  add("feature_img",ic>0?`${ic} images on PDP`:"",iv("feature image"),"yn");
  add("lifestyle_img",ic>0?`${ic} gallery images — confirm lifestyle manually`:"",iv("lifestyle image"),"yn");
  add("cs_image","",iv("customer support image","support qr image","warranty image"),"yn");
  add("box_image","",iv("what is in the box image","what's in the box image"),"yn");
  add("box_contents",cv("What is in the box?"),iv("what's in the box (box contents)","box contents","what's in the box"),"yn");
  add("ours_vs_their","",iv("ours vs their"),"yn");
  add("listing_video",cv("Listing Video")?`Present${cv("Video Count")&&cv("Video Count")!=="0"?` · ${cv("Video Count")} video(s)`:""}`:"",iv("listing video","influencer video"),"yn");
  add("nce",cv("Selling Price"),iv("nce (sp","nce"),"nce");
  const rat=cv("Rating");
  const ratBand=ratingBand(rat);
  const ratInp=iv("ratings","rating");
  const ratInpBand=ratingToBand(ratInp);
  // Show "value → band" on both sides so the validator sees the word mapping.
  const ratCrawlDisplay=rat?(ratBand?`${rat} → ${ratBand}`:rat):"";
  const ratInpDisplay=ratInp?(ratInpBand&&!/excellent|good|ok|mixed|poor/i.test(ratInp)?`${ratInp} → ${ratInpBand}`:ratInp):ratCrawlDisplay; // mirror crawl when input blank
  add("ratings_reviews",ratCrawlDisplay,ratInpDisplay,"rating");
  const revCrawl=cv("Rating Count")?`${cv("Rating Count")} reviews`:"";
  add("reviews",revCrawl,iv("review")||revCrawl,"reviews"); // mirror crawl when input blank
  add("aplus",cv("A+ Content")?`Present${cv("A+ Image Count")&&cv("A+ Image Count")!=="0"?` · ${cv("A+ Image Count")} imgs`:""}`:"",iv("a+ (y","a+ y"),"yn");
  add("description",cv("Description"),iv("description / a+","description/a+"),"yn");
  // Competitor-research columns — always manual review, sourced from input sheet notes.
  add("comp_remarks","",iv("other competitor remarks for nodding","competitor remarks"),"yn");
  add("comp_crosscheck","",iv("cross check other main competitors","cross check competitors"),"yn");
  add("comp_policy","",iv("policy changes competitor asin","policy changes competitor"),"yn");
  return R;
}

// ═══ UI ═══
const T={bg:"#F3F7F6",hd:"#0B1F1E",ac:"#0F766E",card:"#FFF",bd:"#D8E5E1",t1:"#10201F",t2:"#667873",
  pass:"#059669",passBg:"#ECFDF5",passB:"#A7F3D0",fail:"#DC2626",failBg:"#FEF2F2",failB:"#FECACA",
  rev:"#D97706",revBg:"#FFFBEB",revB:"#FDE68A",actBg:"#EAF7F4"};

function Badge({status}){
  const c=status==="PASS"?[T.passBg,T.pass,T.passB]:status==="FAIL"?[T.failBg,T.fail,T.failB]:[T.revBg,T.rev,T.revB];
  return<span style={{display:"inline-block",padding:"2px 10px",borderRadius:20,fontSize:11,fontWeight:700,fontFamily:"'DM Sans'",letterSpacing:.5,background:c[0],color:c[1],border:`1px solid ${c[2]}`}}>{status}</span>;
}

function ScoreRing({pct,size=56}){
  const r=(size-8)/2,circ=2*Math.PI*r,off=circ-(pct/100)*circ;
  const col=pct>=70?T.pass:pct>=40?T.rev:T.fail;
  return<svg width={size} height={size} style={{transform:"rotate(-90deg)"}}>
    <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="#E2E8F0" strokeWidth={4}/>
    <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={col} strokeWidth={4} strokeDasharray={circ} strokeDashoffset={off} strokeLinecap="round" style={{transition:"stroke-dashoffset .5s"}}/>
    <text x={size/2} y={size/2} textAnchor="middle" dominantBaseline="central" style={{transform:"rotate(90deg)",transformOrigin:"center",fontSize:13,fontWeight:700,fontFamily:"'Outfit'",fill:col}}>{pct}%</text>
  </svg>;
}

// ═══ CORRECTION MODAL ═══
function CorrectionModal({checkName, asin, origVal, newVal, onApply, onCancel}){
  return(
    <div style={{position:"fixed",top:0,left:0,right:0,bottom:0,background:"rgba(0,0,0,.5)",zIndex:9999,display:"flex",alignItems:"center",justifyContent:"center",fontFamily:"'DM Sans'"}} onClick={onCancel}>
      <div onClick={e=>e.stopPropagation()} style={{background:"#FFF",borderRadius:14,padding:24,maxWidth:480,width:"90%",boxShadow:"0 20px 60px rgba(0,0,0,.2)"}}>
        <div style={{fontSize:16,fontWeight:700,color:T.t1,marginBottom:4,fontFamily:"'Outfit'"}}>Save Correction</div>
        <div style={{fontSize:12,color:T.t2,marginBottom:16}}>
          <b>{checkName}</b> for <span style={{fontFamily:"'JetBrains Mono'",color:T.ac}}>{asin}</span>
        </div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginBottom:16}}>
          <div style={{background:"#FEF2F2",borderRadius:6,padding:"8px 10px",border:"1px solid #FECACA"}}>
            <div style={{fontSize:9,fontWeight:700,color:T.fail,marginBottom:4,textTransform:"uppercase"}}>Old Value</div>
            <div style={{fontSize:12,fontFamily:"'JetBrains Mono'",color:T.t1,wordBreak:"break-word"}}>{origVal||"— empty —"}</div>
          </div>
          <div style={{background:T.passBg,borderRadius:6,padding:"8px 10px",border:`1px solid ${T.passB}`}}>
            <div style={{fontSize:9,fontWeight:700,color:T.pass,marginBottom:4,textTransform:"uppercase"}}>New Value</div>
            <div style={{fontSize:12,fontFamily:"'JetBrains Mono'",color:T.t1,wordBreak:"break-word"}}>{newVal||"— empty —"}</div>
          </div>
        </div>
        <div style={{fontSize:12,fontWeight:600,color:T.t1,marginBottom:10}}>Apply this correction to:</div>
        <div style={{display:"flex",gap:10,flexWrap:"wrap"}}>
          <button onClick={()=>onApply("asin")} style={{flex:1,padding:"10px 16px",borderRadius:8,border:`2px solid ${T.ac}`,background:"#F0F9FF",color:T.ac,fontSize:13,fontWeight:700,cursor:"pointer",fontFamily:"'DM Sans'"}}>
            🎯 This ASIN Only
          </button>
          <button onClick={()=>onApply("global")} style={{flex:1,padding:"10px 16px",borderRadius:8,border:"2px solid #8B5CF6",background:"#F5F3FF",color:"#8B5CF6",fontSize:13,fontWeight:700,cursor:"pointer",fontFamily:"'DM Sans'"}}>
            🌐 All ASINs (same check)
          </button>
        </div>
        <button onClick={onCancel} style={{width:"100%",marginTop:10,padding:"8px",borderRadius:6,border:`1px solid ${T.bd}`,background:"transparent",color:T.t2,fontSize:12,cursor:"pointer",fontFamily:"'DM Sans'"}}>Cancel</button>
      </div>
    </div>
  );
}

// ═══ CHECK ROW (with editable input) ═══
function CheckRow({check,def,decision,comment,verified,onDecide,onComment,onVerify,correctedVal,onEditInput}){
  const[showCmt,setShowCmt]=useState(false);
  const[editing,setEditing]=useState(false);
  const[editVal,setEditVal]=useState("");
  const inputRef=useRef(null);

  const displayInputVal=correctedVal!==undefined?correctedVal:check.inputVal;
  const isCorrected=correctedVal!==undefined&&correctedVal!==check.origInputVal;

  // Recalculate status if corrected
  const mode=CHECK_MODES[def.id]||"yn";
  const effectiveStatus=isCorrected?reDecide(def.id,check.crawlVal,displayInputVal,mode):check.status;

  const autoLabel=effectiveStatus==="PASS"?"Auto → Yes":effectiveStatus==="FAIL"?"Auto → No":"Auto → Not Sure";
  const bc=effectiveStatus==="FAIL"?T.fail:effectiveStatus==="REVIEW"?T.rev:T.pass;

  const startEdit=()=>{setEditVal(displayInputVal);setEditing(true);setTimeout(()=>inputRef.current?.focus(),50);};
  const confirmEdit=()=>{
    setEditing(false);
    if(editVal!==displayInputVal){onEditInput(def.id,def.name,check.origInputVal,editVal);}
  };

  return<div style={{background:T.card,borderRadius:10,border:`1px solid ${T.bd}`,padding:"12px 16px",marginBottom:8,borderLeft:`3px solid ${bc}`}}>
    <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:8}}>
      <input type="checkbox" checked={!!verified} onChange={()=>onVerify(!verified)} style={{width:16,height:16,accentColor:T.ac,cursor:"pointer"}}/>
      <span style={{fontFamily:"'DM Sans'",fontWeight:600,fontSize:13,color:T.t1,flex:1}}>{def.name}</span>
      {isCorrected&&<span style={{fontSize:9,fontWeight:700,color:"#8B5CF6",background:"#F5F3FF",padding:"1px 6px",borderRadius:4,border:"1px solid #DDD6FE"}}>CORRECTED</span>}
      <span style={{fontFamily:"'JetBrains Mono'",fontSize:10,color:T.t2,background:"#F1F5F9",padding:"2px 8px",borderRadius:4}}>{def.group.split("·")[0].trim()}</span>
      <Badge status={effectiveStatus}/>
    </div>
    <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginBottom:8}}>
      <div style={{background:"#F8FAFC",borderRadius:6,padding:"8px 10px",border:"1px solid #E2E8F0"}}>
        <div style={{fontSize:9,fontWeight:700,color:T.t2,marginBottom:4,textTransform:"uppercase",fontFamily:"'DM Sans'",letterSpacing:.8}}>CRAWL (AMAZON PDP)</div>
        <div style={{fontSize:12,fontFamily:"'JetBrains Mono'",color:T.t1,wordBreak:"break-word",maxHeight:60,overflow:"auto",lineHeight:1.4,background:effectiveStatus==="FAIL"&&check.crawlVal?"#FEF2F2":"transparent",padding:effectiveStatus==="FAIL"&&check.crawlVal?"2px 4px":0,borderRadius:3}}>
          {check.crawlVal||<span style={{color:"#CBD5E1",fontStyle:"italic"}}>— empty —</span>}
        </div>
      </div>
      <div style={{background:isCorrected?"#F5F3FF":"#F8FAFC",borderRadius:6,padding:"8px 10px",border:isCorrected?"1px solid #DDD6FE":"1px solid #E2E8F0",position:"relative"}}>
        <div style={{display:"flex",alignItems:"center",gap:4,marginBottom:4}}>
          <div style={{fontSize:9,fontWeight:700,color:isCorrected?"#8B5CF6":T.t2,textTransform:"uppercase",fontFamily:"'DM Sans'",letterSpacing:.8,flex:1}}>
            INPUT (REFERENCE){isCorrected?" ✎":""}
          </div>
        </div>
        {(
          <div style={{fontSize:12,fontFamily:"'JetBrains Mono'",color:T.t1,wordBreak:"break-word",maxHeight:60,overflow:"auto",lineHeight:1.4,background:effectiveStatus==="FAIL"&&displayInputVal?"#FEF2F2":"transparent",padding:effectiveStatus==="FAIL"&&displayInputVal?"2px 4px":0,borderRadius:3}}>
            {displayInputVal||<span style={{color:"#CBD5E1",fontStyle:"italic"}}>— empty —</span>}
          </div>
        )}
        {isCorrected&&!editing&&<div style={{fontSize:9,color:"#A78BFA",marginTop:2,fontStyle:"italic"}}>was: {check.origInputVal||"empty"}</div>}
      </div>
    </div>
    <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
      {autoLabel&&<span style={{fontSize:11,fontWeight:600,fontFamily:"'DM Sans'",color:effectiveStatus==="PASS"?T.pass:effectiveStatus==="FAIL"?T.fail:T.rev}}>{autoLabel}</span>}
      {["Yes","No","Not Sure"].map(opt=>{
        const a=decision===opt;const c=opt==="Yes"?T.pass:opt==="No"?T.fail:T.rev;
        return<button key={opt} onClick={()=>onDecide(opt)} style={{padding:"4px 14px",borderRadius:6,fontSize:11,fontWeight:600,cursor:"pointer",fontFamily:"'DM Sans'",border:`1.5px solid ${a?c:"#E2E8F0"}`,background:a?c+"18":"transparent",color:a?c:T.t2,transition:"all .15s"}}>{opt}</button>;
      })}
      <button onClick={()=>setShowCmt(!showCmt)} style={{marginLeft:"auto",background:"none",border:comment?`1.5px solid ${T.ac}`:"1.5px solid #E2E8F0",borderRadius:6,padding:"4px 10px",cursor:"pointer",fontSize:11,color:comment?T.ac:T.t2,fontFamily:"'DM Sans'"}}>💬{comment?" ✓":""}</button>
    </div>
    {showCmt&&<input value={comment||""} onChange={e=>onComment(e.target.value)} placeholder="Add comment..." style={{width:"100%",marginTop:8,padding:"6px 10px",borderRadius:6,border:"1px solid #E2E8F0",fontSize:12,fontFamily:"'DM Sans'",outline:"none",boxSizing:"border-box"}}/>}
  </div>;
}

// ═══ MAIN APP ═══
async function apiFetchProducts(){
  try{const r=await fetch(`${API}/products`);if(!r.ok)return null;return await r.json();}catch{return null;}
}
async function apiFetchInput(){
  try{const r=await fetch(`${API}/input`);if(!r.ok)return null;return await r.json();}catch{return null;}
}
// Rebuild a SheetJS workbook from the backend /input payload so we can reuse the
// EXACT same parseInput() pipeline a manual upload uses (identical _asin keys,
// nm() column normalization, sheet picking). This guarantees checks compute the
// same whether data came from upload or backend.
function inputPayloadToWorkbook(payload){
  if(!payload||!Array.isArray(payload.columns)||!Array.isArray(payload.rows))return null;
  const cols=payload.columns;
  const aoa=[cols, ...payload.rows.map(row=>cols.map(c=>row[c]!==undefined&&row[c]!==null?row[c]:""))];
  const ws=XLSX.utils.aoa_to_sheet(aoa);
  const sheetName=payload.sheet_name||"Format";
  return {SheetNames:[sheetName], Sheets:{[sheetName]:ws}};
}

export default function App(){
  const[screen,setScreen]=useState("upload");
  const[crawlData,setCrawlData]=useState([]);
  const[inputData,setInputData]=useState([]);
  const[products,setProducts]=useState([]);
  const[autoLoading,setAutoLoading]=useState(true);
  const[autoLoadErr,setAutoLoadErr]=useState("");
  const[curBrand,setCurBrand]=useState("ALL");
  const[curIdx,setCurIdx]=useState(0);
  const[filter,setFilter]=useState("all");
  const[validator,setValidator]=useState("");
  const[hideDone,setHideDone]=useState(false);
  const[expanded,setExpanded]=useState({});
  const[asinSt,setAsinSt]=useState({});
  const[inputNames,setInputNames]=useState([]);
  const[crawlName,setCrawlName]=useState("");
  const[log,setLog]=useState("");
  // ═══ CORRECTIONS DB ═══
  // byAsin: { [asin]: { [checkId]: correctedValue } }
  // global: { [checkId]: correctedValue }  — applies to ALL ASINs for that check
  const[corrections,setCorrections]=useState({byAsin:{},global:{}});
  const[corrModal,setCorrModal]=useState(null); // {asin,checkId,checkName,origVal,newVal}

  const crRef=useRef(null),inRef=useRef(null);
  const addLog=(m)=>setLog(p=>p+(p?"\n":"")+m);

  // ═══ LOGIN / USERS (shared cloud storage) ═══
  const SEED_USERS={
    "Hazique Khalique":{pw:"Hazique@123",admin:true},
    "Naresh More":{pw:"Naresh@123",admin:false},
    "Nitesh Sharma":{pw:"Nitesh@123",admin:false},
    "Kanwal Jeet":{pw:"Kanwal@123",admin:false},
    "Sagar Maharana":{pw:"Sagar@123",admin:false},
    "Unmesha Tawde":{pw:"Unmesha@123",admin:false},
    "Sagar Sakapl":{pw:"Sagar@123",admin:false},
  };
  const[authUser,setAuthUser]=useState(null);     // logged-in full name
  const[users,setUsers]=useState(SEED_USERS);     // {name:{pw,admin}}
  const[loginName,setLoginName]=useState("");
  const[loginPw,setLoginPw]=useState("");
  const[loginErr,setLoginErr]=useState("");
  const[showAddUser,setShowAddUser]=useState(false);
  const[newU,setNewU]=useState({name:"",pw:""});
  // Load users from localStorage (merge with seeds so the 3 always exist). Works on Render.
  useEffect(()=>{try{const s=localStorage.getItem("hv_users");if(s){const saved=JSON.parse(s);setUsers({...SEED_USERS,...saved});}}catch{}},[]);
  // Remember the session locally so a refresh keeps you logged in.
  useEffect(()=>{try{const s=localStorage.getItem("hv_session");if(s)setAuthUser(s);}catch{}},[]);
  const saveUsers=(u)=>{try{const extra={};Object.keys(u).forEach(k=>{if(!SEED_USERS[k])extra[k]=u[k];});localStorage.setItem("hv_users",JSON.stringify(extra));}catch{}};
  const doLogin=()=>{
    const name=loginName.trim();
    const key=Object.keys(users).find(k=>k.toLowerCase()===name.toLowerCase());
    const u=key?users[key]:null;
    if(u&&u.pw===loginPw){setAuthUser(key);setValidator(name);try{localStorage.setItem("hv_session",name);}catch{}setLoginErr("");setLoginName("");setLoginPw("");}
    else setLoginErr("Incorrect name or password.");
  };
  const doLogout=()=>{setAuthUser(null);try{localStorage.removeItem("hv_session");}catch{}};
  const addUser=async()=>{
    const n=newU.name.trim().replace(/\s+/g," ");
    if(!n){alert("Enter a name.");return;}
    if(!newU.pw||newU.pw.length<4){alert("Password must be at least 4 characters.");return;}
    if(Object.keys(users).some(k=>k.toLowerCase()===n.toLowerCase())){alert(`"${n}" already exists.`);return;}
    const next={...users,[n]:{pw:newU.pw,admin:false}};
    setUsers(next);saveUsers(next);setNewU({name:"",pw:""});setShowAddUser(false);
  };
  const removeUser=async(name)=>{
    if(SEED_USERS[name]){alert("Built-in users can't be removed.");return;}
    if(!window.confirm(`Remove user "${name}"?`))return;
    const next={...users};delete next[name];setUsers(next);saveUsers(next);
  };
  const isAdmin=authUser&&users[authUser]?.admin;


  useEffect(()=>{const d=ldLS();if(d){if(d.asinSt)setAsinSt(d.asinSt);if(d.validator)setValidator(d.validator);if(d.curBrand)setCurBrand(d.curBrand);if(d.corrections)setCorrections(d.corrections);}},[]);

  // Pull the shared done-list from the backend and mark those ASINs done locally
  // too, so a product validated by ANY teammate shows as done for everyone.
  useEffect(()=>{
    let cancelled=false;
    apiFetchDone().then(done=>{
      if(cancelled||!Array.isArray(done)||done.length===0)return;
      setAsinSt(prev=>{
        const next={...prev};
        done.forEach(asin=>{
          const ex=next[asin]||{decisions:{},comments:{},verified:{},done:false,notes:"",by:""};
          if(!ex.done)next[asin]={...ex,done:true};
        });
        return next;
      });
    });
    return()=>{cancelled=true;};
  },[]);

  // Debounced auto-save so typing isn't slowed by a full JSON.stringify + localStorage write on every keystroke,
  // and so QuotaExceededError actually reaches the user instead of being swallowed (which would silently lose
  // multi-day progress on refresh/close).
  const saveErrRef=useRef(false);
  useEffect(()=>{
    if(Object.keys(asinSt).length===0)return;
    const payload={asinSt,validator,curBrand,curIdx,corrections};
    const t=setTimeout(()=>{
      const res=svLS(payload);
      if(!res.ok){
        if(!saveErrRef.current){
          saveErrRef.current=true;
          addLog(`⚠️ Auto-save FAILED (${res.err}) — browser storage is full. EXPORT YOUR WORK NOW or it will be lost on refresh/close.`);
          try{window.alert("Auto-save failed — browser storage is full.\n\nYour work is still in this tab, but it will be LOST if you close or refresh the browser.\n\nClick Export now to save your progress to a file.");}catch{}
        }
      }else if(saveErrRef.current){
        saveErrRef.current=false;
        addLog("✅ Auto-save recovered.");
      }
    },500);
    // Last-chance flush on tab close / refresh so debounce-in-flight changes aren't lost.
    const flush=()=>{svLS(payload);};
    window.addEventListener("beforeunload",flush);
    return()=>{clearTimeout(t);window.removeEventListener("beforeunload",flush);};
  },[asinSt,validator,curBrand,curIdx,corrections]);

  // Get corrected input value for a check
  const getCorrectedVal=(asin,checkId,origVal)=>{
    // ASIN-specific correction takes priority
    if(corrections.byAsin[asin]?.[checkId]!==undefined)return corrections.byAsin[asin][checkId];
    // Global correction for this check
    if(corrections.global[checkId]!==undefined)return corrections.global[checkId];
    return undefined; // no correction
  };

  // Apply correction
  const applyCorrection=(scope)=>{
    if(!corrModal)return;
    const{asin,checkId,newVal}=corrModal;
    setCorrections(prev=>{
      const next={byAsin:{...prev.byAsin},global:{...prev.global}};
      if(scope==="asin"){
        next.byAsin[asin]={...(next.byAsin[asin]||{}), [checkId]:newVal};
      }else{
        next.global[checkId]=newVal;
      }
      return next;
    });
    setCorrModal(null);
  };

  // PHASE 2: auto-load crawl + input from the backend on startup so no user has
  // to upload files. Falls back to the manual upload screen if the backend has
  // no data or is unreachable.
  useEffect(()=>{
    let cancelled=false;
    (async()=>{
      setAutoLoading(true);setAutoLoadErr("");
      const [prodRows,inputPayload]=await Promise.all([apiFetchProducts(),apiFetchInput()]);
      if(cancelled)return;
      if(!Array.isArray(prodRows)||prodRows.length===0){
        // No backend products -> show manual upload screen.
        setAutoLoadErr("no-backend-products");
        setAutoLoading(false);
        return;
      }
      // Crawl rows from /products already match parseCrawl() output shape (CSV row
      // objects keyed by original headers like ASIN, Brand, Title...). Keep only
      // rows with a usable ASIN, same as parseCrawl's filter.
      const crawl=prodRows.filter(r=>ss(r.ASIN).length>=5);
      setCrawlData(crawl);
      setCrawlName("backend: /products");
      // Input: rebuild a workbook and run the SAME parseInput() pipeline.
      let inp=[];
      if(inputPayload&&Array.isArray(inputPayload.rows)&&inputPayload.rows.length){
        const wb=inputPayloadToWorkbook(inputPayload);
        if(wb){try{inp=parseInput(wb);}catch{inp=[];}}
      }
      setInputData(inp);
      setInputNames(inputPayload&&inputPayload.sheet_name?[`backend: ${inputPayload.sheet_name}`]:[]);
      addLog(`✅ Loaded from backend — crawl: ${crawl.length} ASINs, input: ${inp.length} rows`);
      setAutoLoading(false);
    })();
    return()=>{cancelled=true;};
  },[]);

  const onCrawl=useCallback((e)=>{
    const file=e.target.files[0];if(!file)return;
    setCrawlName(file.name);
    const ext=file.name.split(".").pop().toLowerCase();
    const reader=new FileReader();
    if(ext==="csv"){
      reader.onload=ev=>{const rows=parseCrawl(ev.target.result);setCrawlData(rows);addLog(`✅ Crawl: ${rows.length} ASINs from ${file.name}`);};
      reader.readAsText(file);
    }else{
      reader.onload=ev=>{
        const wb=XLSX.read(ev.target.result,{type:"array"});
        // Read every sheet in the workbook — previously only SheetNames[0] was read, silently dropping
        // all other sheets (common in brand/category-partitioned crawl exports).
        const perSheet=[];
        const all=[];
        wb.SheetNames.forEach(name=>{
          const csv=XLSX.utils.sheet_to_csv(wb.Sheets[name]);
          const rows=parseCrawl(csv);
          perSheet.push(`${name}:${rows.length}`);
          all.push(...rows);
        });
        const seen=new Set();
        const rows=all.filter(r=>{const a=ss(r.ASIN).toUpperCase();if(!a||seen.has(a))return false;seen.add(a);return true;});
        const dropped=all.length-rows.length;
        setCrawlData(rows);
        const sheetTag=wb.SheetNames.length>1?` [${perSheet.join(", ")}]`:"";
        const dupTag=dropped>0?` · ${dropped} duplicate ASIN${dropped>1?"s":""} skipped`:"";
        addLog(`✅ Crawl: ${rows.length} ASINs from ${file.name}${sheetTag}${dupTag}`);
      };
      reader.readAsArrayBuffer(file);
    }
  },[]);

  const onInputFile=useCallback((e)=>{
    const files=Array.from(e.target.files);if(!files.length)return;
    const jf=files.find(f=>f.name.endsWith(".json"));
    if(jf){const r=new FileReader();r.onload=ev=>{try{const b=JSON.parse(ev.target.result);if(b.asinSt){setAsinSt(b.asinSt);addLog("✅ Backup restored!");}if(b.corrections){setCorrections(b.corrections);addLog(`✅ Corrections DB loaded: ${Object.keys(b.corrections.byAsin||{}).length} ASIN-level, ${Object.keys(b.corrections.global||{}).length} global`);}}catch{addLog("❌ Invalid backup");}};r.readAsText(jf);return;}
    const xls=files.filter(f=>!f.name.endsWith(".json"));
    setInputNames(p=>[...p,...xls.map(f=>f.name)]);
    xls.forEach(file=>{
      const reader=new FileReader();
      reader.onload=ev=>{
        const wb=XLSX.read(ev.target.result,{type:"array"});
        const rows=parseInput(wb);
        if(rows.length>0){
          const sample=rows[0];
          const dataKeys=Object.keys(sample).filter(k=>!k.startsWith("_")&&sample[k]);
          addLog(`✅ ${file.name}: ${rows.length} ASINs, sheet="${sample._sheet}", ${dataKeys.length} cols`);
          // Newer wins: when an upload contains an ASIN already loaded, replace the existing row instead
          // of silently dropping the new one. Re-uploading a corrected sheet previously had no effect.
          let logged=false;
          setInputData(prev=>{
            const map=new Map();
            prev.forEach(r=>{if(r._asin)map.set(r._asin,r);});
            let added=0,updated=0;
            rows.forEach(r=>{if(!r._asin)return;if(map.has(r._asin))updated++;else added++;map.set(r._asin,r);});
            if(!logged){logged=true;Promise.resolve().then(()=>{if(added||updated)addLog(`   ↻ ${added} added · ${updated} updated (newer wins)`);});}
            return Array.from(map.values());
          });
        }else{
          addLog(`❌ ${file.name}: 0 rows! Sheets: ${wb.SheetNames.join(", ")}`);
        }
      };
      reader.readAsArrayBuffer(file);
    });
  },[]);

  const onValidate=useCallback(()=>{
    if(!crawlData.length){alert("Upload crawl data first");return;}
    const cI={};crawlData.forEach(r=>{const a=ss(r.ASIN).toUpperCase();if(a)cI[a]=r;});
    const iI={};inputData.forEach(r=>{if(r._asin)iI[r._asin]=r;});
    const overlap=Object.keys(cI).filter(a=>iI[a]).length;
    addLog(`\n═══ VALIDATE ═══\nCrawl: ${Object.keys(cI).length} | Input: ${Object.keys(iI).length} | Matched: ${overlap}`);
    const prods=Object.keys(cI).filter(a=>{const b=cleanB(ss(cI[a]?.Brand));return!EXCL.has(b.toLowerCase())&&b.length>1;})
      .map(asin=>{
        const cr=cI[asin],ir=iI[asin]||null;
        const brand=cleanB(ss(cr.Brand));
        const checks=validate(cr,ir);
        const pass=checks.filter(c=>c.status==="PASS").length;
        const fail=checks.filter(c=>c.status==="FAIL").length;
        const review=checks.filter(c=>c.status==="REVIEW").length;
        const score=(pass+fail)?Math.round(pass/(pass+fail)*100):0;
        return{asin,brand,title:ss(cr.Title),price:ss(cr["Selling Price"]),mrp:ss(cr.MRP),rating:ss(cr.Rating),ratingCount:ss(cr["Rating Count"]),imgUrls:ss(cr["Image URLs"]),productUrl:ss(cr["Product URL"]),hasInput:!!ir,checks,pass,fail,review,score,imgCount:pipeC(ss(cr["Image URLs"]))};
      }).sort((a,b)=>a.score-b.score);
    setAsinSt(prev=>{
      const next={...prev};
      prods.forEach(p=>{
        const existing=next[p.asin]||{decisions:{},comments:{},verified:{},done:false,notes:""};
        const decisions={...existing.decisions};
        const verified={...existing.verified};
        p.checks.forEach(ck=>{
          if(!decisions[ck.id]){
            // Check if there's a correction — use corrected status
            const cv=getCorrectedVal(p.asin,ck.id,ck.inputVal);
            const effectiveStatus=cv!==undefined?reDecide(ck.id,ck.crawlVal,cv,CHECK_MODES[ck.id]||"yn"):ck.status;
            if(effectiveStatus==="PASS"){decisions[ck.id]="Yes";verified[ck.id]=true;}
            else if(effectiveStatus==="FAIL"){decisions[ck.id]="No";}
            else{decisions[ck.id]="Not Sure";}
          }
        });
        next[p.asin]={...existing,decisions,verified};
      });
      return next;
    });
    setProducts(prods);setCurIdx(0);setScreen("validate");
  },[crawlData,inputData,corrections]);

  // PHASE 2: once backend data has loaded into crawlData, automatically build the
  // validation list and jump to the validate screen — no manual button click.
  const autoRanRef=useRef(false);
  useEffect(()=>{
    if(autoRanRef.current)return;
    if(autoLoading)return;
    if(autoLoadErr)return;            // backend had no data -> stay on upload screen
    if(!crawlData.length)return;      // nothing to validate
    autoRanRef.current=true;
    onValidate();
  },[autoLoading,autoLoadErr,crawlData,onValidate]);


  const brands=useMemo(()=>{const m={};products.forEach(p=>{if(!m[p.brand])m[p.brand]={total:0,done:0,withInput:0,pass:0,fail:0,rev:0};m[p.brand].total++;if(p.hasInput)m[p.brand].withInput++;m[p.brand].pass+=p.pass;m[p.brand].fail+=p.fail;m[p.brand].rev+=p.review;if(asinSt[p.asin]?.done)m[p.brand].done++;});return m;},[products,asinSt]);
  const filtered=useMemo(()=>{let l=curBrand==="ALL"?products:products.filter(p=>p.brand===curBrand);if(hideDone)l=l.filter(p=>!asinSt[p.asin]?.done);return l;},[products,curBrand,hideDone,asinSt]);
  const cur=filtered[curIdx]||null;
  const getA=a=>asinSt[a]||{decisions:{},comments:{},verified:{},done:false,notes:"",by:""};
  const setA=(a,fn)=>setAsinSt(p=>{const prev=p[a]||{decisions:{},comments:{},verified:{},done:false,notes:"",by:""};const upd=fn(prev);return{...p,[a]:{...upd,by:authUser||upd.by||""}};});
  const goN=()=>setCurIdx(i=>Math.min(i+1,filtered.length-1));
  const goP=()=>setCurIdx(i=>Math.max(i-1,0));
  // Build the full check-results record for an ASIN and push it to the shared
  // DB. Called whenever an ASIN is marked done. Uses the logged-in user's name.
  const pushDone=(p)=>{
    if(!p)return;
    const a=getA(p.asin);
    const check_results={};
    (p.checks||[]).forEach(ck=>{
      const d=a.decisions[ck.id];
      check_results[ck.id]=(d&&d!=="Not Sure")?d:ck.status;
    });
    apiMarkDone({
      asin:p.asin,
      validated_by:authUser||validator||a.by||"",
      check_results,
      notes:a.notes||"",
      brand:p.brand||"",
    });
  };

  // Save & Next: mark current ASIN done (progress auto-saves already) then jump to next.
  const saveAndNext=()=>{
    if(!cur)return;
    setA(cur.asin,s=>({...s,done:true}));
    pushDone(cur);
    setCurIdx(i=>Math.min(i+1,filtered.length-1));
    window.scrollTo({top:0,behavior:"smooth"});
  };
  // Search by ASIN: jump to the matching ASIN in the current filtered list.
  const[asinSearch,setAsinSearch]=useState("");
  const jumpToAsin=(q)=>{
    const term=ss(q).toUpperCase().trim();
    if(!term)return;
    const i=filtered.findIndex(p=>ss(p.asin).toUpperCase().includes(term));
    if(i>=0){setCurIdx(i);window.scrollTo({top:0,behavior:"smooth"});}
    else alert(`ASIN "${q}" not found in the ${curBrand==="ALL"?"":curBrand+" "}list.`);
  };
  // Mark done — but warn if checks are still undecided. Toggling OFF is always allowed.
  const toggleDone=()=>{
    if(!cur)return;
    const a=getA(cur.asin);
    if(!a.done){
      const left=cur.checks.filter(ck=>{const d=a.decisions[ck.id];return !d||d==="Not Sure";}).length;
      if(left>0&&!window.confirm(`${left} check${left>1?"s are":" is"} still "Not Sure". Mark this ASIN done anyway?`))return;
      pushDone(cur); // marking done → save to shared DB for the whole team
    }
    setA(cur.asin,s=>({...s,done:!s.done}));
  };

  // Build a mailto: draft listing every FAILED check for the current ASIN (auto-FAIL or marked "No").
  const mailFailures=()=>{
    if(!cur)return;
    const a=getA(cur.asin);
    const nameOf=id=>(CK.find(c=>c.id===id)||{}).name||id;
    const fails=cur.checks.filter(ck=>{
      const dec=a.decisions[ck.id];
      if(dec==="No")return true;
      if(dec==="Yes"||dec==="Not Sure")return false;
      return ck.status==="FAIL";
    });
    if(fails.length===0){window.alert("No failed checks on this ASIN.");return;}
    const lines=fails.map((ck,i)=>{
      const cmt=a.comments[ck.id]?` — note: ${a.comments[ck.id]}`:"";
      return `${i+1}. ${nameOf(ck.id)}\n   Live (Amazon): ${ck.crawlVal||"(empty)"}\n   Should be: ${ck.inputVal||"(empty)"}${cmt}`;
    }).join("\n\n");
    const subject=`Hygiene FAIL — ${cur.asin} (${cur.brand}) — ${fails.length} issue${fails.length>1?"s":""}`;
    const body=`ASIN: ${cur.asin}\nBrand: ${cur.brand}\nTitle: ${cur.title}\nLink: ${cur.productUrl||`https://www.amazon.in/dp/${cur.asin}`}\nValidator: ${validator||"-"}\n\nFAILED CHECKS (${fails.length}):\n\n${lines}\n\n— Sent from Hygiene Validator`;
    window.location.href=`mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  };

  // Keydown listener reads live state from a ref so navigation+decision keystrokes always hit the current ASIN.
  // (Previously the handler closed over cur/asinSt/filtered and relied on re-registering via deps, which both
  //  thrashed the listener on every Y/N press and left a small race window after navigation where a quick
  //  next keystroke could land on the previous ASIN.)
  const kbRef=useRef({});
  useEffect(()=>{kbRef.current={cur,filtered,asinSt,authUser};});
  useEffect(()=>{
    if(screen!=="validate")return;
    const blank={decisions:{},comments:{},verified:{},done:false,notes:"",by:""};
    const h=e=>{
      if(e.target.tagName==="INPUT"||e.target.tagName==="TEXTAREA")return;
      const L=kbRef.current;if(!L||!L.cur)return;
      const {cur,filtered,asinSt,authUser}=L;
      if(e.key==="ArrowRight"){setCurIdx(i=>Math.min(i+1,filtered.length-1));e.preventDefault();return;}
      if(e.key==="ArrowLeft"){setCurIdx(i=>Math.max(i-1,0));e.preventDefault();return;}
      const k=e.key.toLowerCase();
      if(k==="y"||k==="n"||k==="s"){
        const val=k==="y"?"Yes":k==="n"?"No":"Not Sure";
        const a=asinSt[cur.asin]||blank;
        const pend=cur.checks.find(c=>c.status==="REVIEW"&&(!a.decisions[c.id]||a.decisions[c.id]==="Not Sure"));
        if(pend)setAsinSt(p=>{const prev=p[cur.asin]||blank;return{...p,[cur.asin]:{...prev,decisions:{...prev.decisions,[pend.id]:val},by:authUser||prev.by||""}};});
      }else if(k==="d"){
        const a=asinSt[cur.asin]||blank;
        if(!a.done){
          const left=cur.checks.filter(ck=>{const d=a.decisions[ck.id];return !d||d==="Not Sure";}).length;
          if(left>0&&!window.confirm(`${left} check${left>1?"s are":" is"} still "Not Sure". Mark this ASIN done anyway?`))return;
        }
        setAsinSt(p=>{const prev=p[cur.asin]||blank;return{...p,[cur.asin]:{...prev,done:!prev.done,by:authUser||prev.by||""}};});
      }
    };
    window.addEventListener("keydown",h);
    return()=>window.removeEventListener("keydown",h);
  },[screen]);

  const dc=useMemo(()=>Object.values(asinSt).filter(s=>s.done).length,[asinSt]);
  const lb=useRef(0);
  useEffect(()=>{if(dc>0&&dc%10===0&&dc!==lb.current){lb.current=dc;backup();}},[dc]);

  const backup=()=>{const d=JSON.stringify({asinSt,validator,corrections,timestamp:new Date().toISOString()});const b=new Blob([d],{type:"application/json"});const u=URL.createObjectURL(b);const a=document.createElement("a");a.href=u;a.download=`hygiene_backup_${new Date().toISOString().slice(0,10)}.json`;a.click();URL.revokeObjectURL(u);};

  const exportCorrections=()=>{
    const rows=[];
    // Global corrections
    Object.entries(corrections.global).forEach(([checkId,newVal])=>{
      const def=CK.find(c=>c.id===checkId);
      rows.push({Scope:"ALL ASINs",ASIN:"—",Check:def?.name||checkId,Group:def?.group||"",CorrectedValue:newVal,Timestamp:new Date().toISOString()});
    });
    // ASIN-specific corrections
    Object.entries(corrections.byAsin).forEach(([asin,checks])=>{
      Object.entries(checks).forEach(([checkId,newVal])=>{
        const def=CK.find(c=>c.id===checkId);
        rows.push({Scope:"ASIN-specific",ASIN:asin,Check:def?.name||checkId,Group:def?.group||"",CorrectedValue:newVal,Timestamp:new Date().toISOString()});
      });
    });
    if(!rows.length){alert("No corrections to export.");return;}
    const ws=XLSX.utils.json_to_sheet(rows);const wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,ws,"Corrections");XLSX.writeFile(wb,`corrections_db_${new Date().toISOString().slice(0,10)}.xlsx`);
  };

  const doExport=()=>{
    const rows=[];
    products.forEach((p,i)=>{const a=getA(p.asin);const row={Sr:i+1,Brand:p.brand,ASIN:p.asin,Title:p.title?.slice(0,80),Score:p.score+"%",Pass:p.pass,Fail:p.fail,Review:p.review,Reviewed:a.done?"Yes":"No","Validated By":a.by||"-"};
      p.checks.forEach(ck=>{const d=CK.find(x=>x.id===ck.id);const n=d?.name||ck.id;
        const cv=getCorrectedVal(p.asin,ck.id,ck.inputVal);
        const corrected=cv!==undefined&&cv!==ck.origInputVal;
        row[`Auto_${n}`]=ck.status;
        row[`Corrected_${n}`]=corrected?cv:"";
        row[`CorrectedStatus_${n}`]=corrected?reDecide(ck.id,ck.crawlVal,cv,CHECK_MODES[ck.id]||"yn"):"";
        row[`Manual_${n}`]=a.decisions[ck.id]||"—";row[`Comment_${n}`]=a.comments[ck.id]||"";});
      row.Notes=a.notes||"";rows.push(row);});
    const ws=XLSX.utils.json_to_sheet(rows);const wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,ws,"Hygiene Report");XLSX.writeFile(wb,`hygiene_export_${new Date().toISOString().slice(0,10)}.xlsx`);
  };

  // Read the crawl value for a given check id off the product's checks.
  function crawlOf(p,id){const ck=p.checks.find(c=>c.id===id);return ck?ss(ck.crawlVal):"";}
  function inputOf(p,id){const ck=p.checks.find(c=>c.id===id);return ck?ss(ck.inputVal):"";}
  function decOf(p,id){const a=getA(p.asin);const m=a.decisions[id];if(m&&m!=="Not Sure")return m;const ck=p.checks.find(c=>c.id===id);return ck?ck.status:"";}
  const doExportSheet=()=>{
    // [header, fn] using crawl values (source of truth from live PDP).
    const COLS=[
      ["Brand",p=>p.brand],["SKU",p=>inputOf(p,"title")&&""||""],["ASIN",p=>p.asin],
      ["Model Name",p=>inputOf(p,"title_format")],
      ["Correct Nodding for reference",p=>crawlOf(p,"nodding")],
      ["Title Name",p=>crawlOf(p,"title")],
      ["NCE (SP > 1500)",p=>crawlOf(p,"nce")],
      ["Ratings",p=>crawlOf(p,"ratings_reviews")],["Review ",p=>crawlOf(p,"reviews")],
      ["Images - minimum 5",p=>crawlOf(p,"images_5")],
      ["Feature images",p=>crawlOf(p,"feature_img")],
      ["Lifestyle images present or not - also can refer to other competitors",p=>crawlOf(p,"lifestyle_img")],
      ["Customer Support Image",p=>crawlOf(p,"cs_image")],
      ["What is in the box image",p=>crawlOf(p,"box_image")],
      ["Ours vs Their Image",p=>crawlOf(p,"ours_vs_their")],
      ["Listing video (Y/N)",p=>crawlOf(p,"listing_video")],
      ["Influencer Video",()=>""],
      ["Correct Variations",p=>crawlOf(p,"variation")],
      ["Variation on PDP",p=>crawlOf(p,"variation")],
      ["Bullet points available - Yes or No(if any changes required can highlight here)",p=>crawlOf(p,"bullets_avail")?"Yes":"No"],
      ["Bullets highlight benefits & include keywords",p=>crawlOf(p,"bullets_kw")],
      ["What is in the Box In bullet point",p=>crawlOf(p,"bullets_box")],
      ["Correct Warranty",p=>crawlOf(p,"warranty")],
      ["Warranty  on panel",p=>crawlOf(p,"warranty_desc")],
      ["correct and incorrect ",p=>decOf(p,"warranty")],
      ["Product added in brand store",p=>crawlOf(p,"brand_store")?"Yes":"No"],
      ["Product added in brand Story",p=>crawlOf(p,"brand_story")?"Yes":"No"],
      ["CS WA Qr Code in brand Story",p=>crawlOf(p,"cs_wa_qr_story")],
      ["A+ (Y/N)",p=>crawlOf(p,"aplus")?"Yes":"No"],
      ["Description / A+ Content",p=>crawlOf(p,"description")],
      ["Colour",p=>crawlOf(p,"colour")],["Material ",p=>crawlOf(p,"material")],
      ["What's in the box (Box Contents)",p=>crawlOf(p,"box_contents")],
      ["Iteam Weight",p=>crawlOf(p,"weight")],["Dimensions",p=>crawlOf(p,"dimensions")],
      ["Mfg details",p=>crawlOf(p,"manufacturer")],
      ["Packer details",p=>crawlOf(p,"packer")],
      ["Importer details",p=>crawlOf(p,"importer")],
      ["Additional Features",p=>crawlOf(p,"addl_features")],
      ["Backend search terms",p=>inputOf(p,"backend_kw")],
      ["Correct Return Policy",p=>crawlOf(p,"return_policy")],
      ["Return policy on page",p=>crawlOf(p,"return_policy")],
      ["Correct Fee Category",p=>inputOf(p,"fee_category")],
      ["Fee category in backend (Correct/Incorrect)",p=>decOf(p,"fee_category")],
      ["Correct Ref fees",p=>inputOf(p,"ref_fees")],
      ["Ref fees in the backend (Correct/Incorrect)",p=>decOf(p,"ref_fees")],
      ["Other competitor remarks for Nodding, Fee category",()=>""],
      ["Cross check other main competitors if any changes (Done)",()=>""],
      ["Policy changes competitor asin's",()=>""],
    ];
    const data=products.map(p=>{const o={};COLS.forEach(([h,fn])=>{try{o[h]=fn(p);}catch{o[h]="";}});return o;});
    const ws=XLSX.utils.json_to_sheet(data,{header:COLS.map(c=>c[0])});
    const wb=XLSX.utils.book_new();XLSX.utils.book_append_sheet(wb,ws,"Format");
    XLSX.writeFile(wb,`Hygiene_Completed_Sheet_${new Date().toISOString().slice(0,10)}.xlsx`);
  };

  const grouped=useMemo(()=>{if(!cur)return[];const g={};cur.checks.forEach(ck=>{const d=CK.find(x=>x.id===ck.id);if(!d)return;if(!g[d.group])g[d.group]=[];g[d.group].push({check:ck,def:d});});if(filter!=="all"){const t=filter==="review"?"REVIEW":filter==="failed"?"FAIL":"PASS";Object.keys(g).forEach(k=>{
    g[k]=g[k].filter(i=>{
      // Use corrected status for filtering
      const cv=getCorrectedVal(cur.asin,i.def.id,i.check.origInputVal);
      const es=cv!==undefined?reDecide(i.def.id,i.check.crawlVal,cv,CHECK_MODES[i.def.id]||"yn"):i.check.status;
      return es===t;
    });
    if(!g[k].length)delete g[k];
  });}return Object.entries(g).sort((a,b)=>(a[1][0]?.def.p||99)-(b[1][0]?.def.p||99));},[cur,filter,corrections]);

  const ov=useMemo(()=>{const d=Object.values(asinSt).filter(s=>s.done).length;return{total:products.length,done:d,pct:products.length?Math.round(d/products.length*100):0};},[products,asinSt]);
  const as=cur?getA(cur.asin):null;
  // How many checks on the current ASIN are still undecided (Not Sure / blank).
  const pendingCount=useMemo(()=>{
    if(!cur||!as)return 0;
    return cur.checks.filter(ck=>{const d=as.decisions[ck.id];return !d||d==="Not Sure";}).length;
  },[cur,as]);
  const thumb=cur?.imgUrls?.split("|")[0]?.trim()||"";

  const corrCount=Object.keys(corrections.global).length+Object.values(corrections.byAsin).reduce((s,o)=>s+Object.keys(o).length,0);

  // ═══ UPLOAD SCREEN ═══
  if(!authUser)return(
    <div style={{minHeight:"100vh",background:T.hd,display:"flex",alignItems:"center",justifyContent:"center",fontFamily:"system-ui,sans-serif"}}>
      <div style={{background:T.card,borderRadius:16,padding:"40px 36px",width:340,boxShadow:"0 20px 60px rgba(0,0,0,.4)"}}>
        <div style={{fontSize:22,fontWeight:800,color:T.ac,marginBottom:4}}>Hygiene Validator</div>
        <div style={{fontSize:13,color:T.t2,marginBottom:24}}>Sign in to continue</div>
        <label style={{fontSize:12,fontWeight:600,color:T.t2}}>Full Name</label>
        <input value={loginName} onChange={e=>setLoginName(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")doLogin();}} placeholder="e.g. Naresh More" style={{width:"100%",boxSizing:"border-box",margin:"6px 0 16px",padding:"10px 12px",border:`1px solid ${T.bd}`,borderRadius:8,fontSize:14}}/>
        <label style={{fontSize:12,fontWeight:600,color:T.t2}}>Password</label>
        <input type="password" value={loginPw} onChange={e=>setLoginPw(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")doLogin();}} placeholder="••••••••" style={{width:"100%",boxSizing:"border-box",margin:"6px 0 16px",padding:"10px 12px",border:`1px solid ${T.bd}`,borderRadius:8,fontSize:14}}/>
        {loginErr&&<div style={{color:"#DC2626",fontSize:12,marginBottom:12}}>{loginErr}</div>}
        <button onClick={doLogin} style={{width:"100%",background:T.ac,color:"#FFF",border:"none",borderRadius:8,padding:"11px",fontSize:14,fontWeight:700,cursor:"pointer"}}>Sign In</button>
      </div>
    </div>
  );

  if(screen==="upload"&&autoLoading)return(
    <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",background:"#0B1220",color:"#E2E8F0",fontFamily:"'Outfit',system-ui,sans-serif"}}>
      <div style={{textAlign:"center"}}>
        <div style={{fontSize:28,marginBottom:12}}>⏳</div>
        <div style={{fontSize:16,fontWeight:600}}>Loading products from server…</div>
        <div style={{fontSize:12,color:"#8CA6A0",marginTop:6}}>First load can take ~30s while the server wakes up</div>
      </div>
    </div>
  );
  if(screen==="upload")return(
    <div style={{minHeight:"100vh",background:"linear-gradient(135deg,#071A18 0%,#0B2F2A 48%,#172033 100%)",display:"flex",alignItems:"center",justifyContent:"center",fontFamily:"'DM Sans',sans-serif",padding:20}}>
      <div style={{maxWidth:640,width:"100%"}}>
        <div style={{textAlign:"center",marginBottom:40}}>
          <div style={{fontSize:36,fontWeight:800,fontFamily:"'Outfit'",background:"linear-gradient(135deg,#5EEAD4,#FDE68A,#A7F3D0)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent",marginBottom:8}}>Listing Hygiene Validator</div>
          <div style={{color:"#C7DAD5",fontSize:14}}>Amazon PDP Audit · {CK.length} Checks · {new Set(CK.map(c=>c.group)).size} Priority Groups</div>
          <div style={{color:"#7F9E98",fontSize:11,marginTop:8}}>PASS = match confirmed · FAIL = mismatch found · REVIEW = validator decision needed</div>
          {corrCount>0&&<div style={{color:"#A78BFA",fontSize:12,marginTop:8,fontWeight:600}}>📦 {corrCount} corrections in database — will auto-apply on validate</div>}
        </div>
        <div style={{marginBottom:24,textAlign:"center"}}>
          <input value={validator} onChange={e=>setValidator(e.target.value)} placeholder="Validator name" style={{background:"rgba(255,255,255,.08)",border:"1px solid rgba(167,243,208,.28)",borderRadius:8,padding:"10px 16px",color:"#F1F5F9",fontSize:14,width:280,textAlign:"center",outline:"none",fontFamily:"'DM Sans'"}}/>
        </div>
        <div style={{display:"grid",gap:16}}>
          <div onClick={()=>crRef.current?.click()} style={{background:"rgba(255,255,255,.08)",borderRadius:10,border:`2px dashed ${crawlData.length>0?"#34D399":"rgba(203,213,225,.28)"}`,padding:"28px 24px",cursor:"pointer",textAlign:"center",boxShadow:"0 12px 36px rgba(0,0,0,.18)"}}>
            <input ref={crRef} type="file" accept=".csv,.xlsx,.xls" onChange={onCrawl} style={{display:"none"}}/>
            <div style={{fontSize:28,marginBottom:8}}>{crawlData.length>0?"✅":"📊"}</div>
            <div style={{color:"#F1F5F9",fontWeight:600,fontSize:15}}>Crawl Data {crawlData.length>0?`(${crawlData.length} ASINs)`:""}</div>
            <div style={{color:"#8CA6A0",fontSize:12}}>{crawlName||"amazon_products_full.csv"}</div>
          </div>
          <div onClick={()=>inRef.current?.click()} style={{background:"rgba(255,255,255,.08)",borderRadius:10,border:`2px dashed ${inputData.length>0?"#34D399":"rgba(203,213,225,.28)"}`,padding:"28px 24px",cursor:"pointer",textAlign:"center",boxShadow:"0 12px 36px rgba(0,0,0,.18)"}}>
            <input ref={inRef} type="file" accept=".xlsx,.xls,.json" multiple onChange={onInputFile} style={{display:"none"}}/>
            <div style={{fontSize:28,marginBottom:8}}>{inputData.length>0?"✅":"📋"}</div>
            <div style={{color:"#F1F5F9",fontWeight:600,fontSize:15}}>Input Sheets {inputData.length>0?`(${inputData.length} rows)`:""}</div>
            <div style={{color:"#8CA6A0",fontSize:12}}>{inputNames.length>0?inputNames.join(", "):"Click multiple times to add sheets · JSON backup loads corrections"}</div>
          </div>
        </div>
        {log&&<pre style={{marginTop:16,padding:12,background:"#1E293B",border:"1px solid #334155",borderRadius:8,color:"#94A3B8",fontSize:10,fontFamily:"'JetBrains Mono'",whiteSpace:"pre-wrap",maxHeight:200,overflow:"auto"}}>{log}</pre>}
        <div style={{textAlign:"center",marginTop:28}}>
          <button onClick={onValidate} disabled={!crawlData.length} style={{background:crawlData.length>0?"linear-gradient(135deg,#0F766E,#14B8A6)":"#334155",color:"#FFF",border:"none",borderRadius:10,padding:"14px 40px",fontSize:16,fontWeight:700,cursor:crawlData.length>0?"pointer":"not-allowed",fontFamily:"'Outfit'",letterSpacing:.5,boxShadow:crawlData.length>0?"0 10px 28px rgba(20,184,166,.28)":"none"}}>
            Validate {crawlData.length} ASINs →
          </button>
        </div>
        <div style={{textAlign:"center",marginTop:16,color:"#475569",fontSize:11}}>Y/N/S = decide · ←→ navigate · D = mark done</div>
      </div>
    </div>
  );

  // ═══ VALIDATION SCREEN ═══
  return(
    <div style={{minHeight:"100vh",background:T.bg,fontFamily:"'DM Sans',sans-serif",display:"flex",flexDirection:"column"}}>
      {corrModal&&<CorrectionModal checkName={corrModal.checkName} asin={corrModal.asin} origVal={corrModal.origVal} newVal={corrModal.newVal} onApply={applyCorrection} onCancel={()=>setCorrModal(null)}/>}
      {showAddUser&&<div style={{position:"fixed",inset:0,background:"rgba(0,0,0,.5)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:1000}} onClick={()=>setShowAddUser(false)}>
        <div onClick={e=>e.stopPropagation()} style={{background:"#FFF",borderRadius:14,padding:28,width:360}}>
          <div style={{fontSize:18,fontWeight:800,color:T.ac,marginBottom:16}}>Manage Users</div>
          <label style={{fontSize:12,fontWeight:600,color:T.t2}}>Full Name</label>
          <input value={newU.name} onChange={e=>setNewU({...newU,name:e.target.value})} placeholder="e.g. Priya Singh" style={{width:"100%",boxSizing:"border-box",margin:"6px 0 12px",padding:"9px 12px",border:`1px solid ${T.bd}`,borderRadius:8,fontSize:14}}/>
          <label style={{fontSize:12,fontWeight:600,color:T.t2}}>Password</label>
          <input value={newU.pw} onChange={e=>setNewU({...newU,pw:e.target.value})} placeholder="e.g. Priya@123" style={{width:"100%",boxSizing:"border-box",margin:"6px 0 16px",padding:"9px 12px",border:`1px solid ${T.bd}`,borderRadius:8,fontSize:14}}/>
          <button onClick={addUser} style={{width:"100%",background:T.ac,color:"#FFF",border:"none",borderRadius:8,padding:"10px",fontWeight:700,cursor:"pointer",fontSize:14}}>Add User</button>
          <div style={{marginTop:18,maxHeight:160,overflowY:"auto"}}>
            {Object.keys(users).map(n=>(<div key={n} style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"6px 0",borderTop:`1px solid ${T.bd}`,fontSize:13}}>
              <span>{n}{users[n].admin?" (admin)":""}</span>
              {!SEED_USERS[n]&&<button onClick={()=>removeUser(n)} style={{background:"none",border:"none",color:"#DC2626",cursor:"pointer",fontSize:12}}>Remove</button>}
            </div>))}
          </div>
          <button onClick={()=>setShowAddUser(false)} style={{marginTop:14,width:"100%",background:"#F1F5F9",border:"none",borderRadius:8,padding:"9px",cursor:"pointer",fontSize:13,fontWeight:600,color:T.t1}}>Close</button>
        </div>
      </div>}
      <header style={{background:T.hd,padding:"0 20px",height:56,display:"flex",alignItems:"center",gap:16,boxShadow:"0 2px 16px rgba(11,31,30,.22)",position:"sticky",top:0,zIndex:100,borderBottom:"1px solid rgba(94,234,212,.16)"}}>
        <span style={{fontFamily:"'Outfit'",fontWeight:800,fontSize:18,background:"linear-gradient(135deg,#5EEAD4,#FDE68A)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>Hygiene Validator v3.10</span>
        <span style={{color:"#64748B",fontSize:12}}>{ov.done}/{ov.total} ({ov.pct}%)</span>
        {corrCount>0&&<span style={{color:"#A78BFA",fontSize:10,fontWeight:600,background:"#1E1B4B",padding:"2px 8px",borderRadius:4}}>📦 {corrCount} fixes</span>}
        <div style={{flex:1}}/>
        <span style={{color:"#94A3B8",fontSize:12,fontWeight:600}}>👤 {authUser}</span>
        {isAdmin&&<button onClick={()=>setShowAddUser(true)} style={{background:"none",border:"1px solid #334155",borderRadius:6,padding:"4px 10px",color:"#94A3B8",cursor:"pointer",fontSize:12}} title="Add a user">+ User</button>}
        <button onClick={backup} style={{background:"none",border:"1px solid #334155",borderRadius:6,padding:"4px 12px",color:"#94A3B8",cursor:"pointer",fontSize:12}} title="Backup JSON (includes corrections)">💾</button>
        <button onClick={exportCorrections} style={{background:"none",border:"1px solid #7C3AED",borderRadius:6,padding:"4px 12px",color:"#A78BFA",cursor:"pointer",fontSize:12,fontWeight:600}} title="Export corrections DB as Excel">📦</button>
        <button onClick={doExportSheet} style={{background:"linear-gradient(135deg,#1D4ED8,#0F766E)",border:"none",borderRadius:6,padding:"4px 14px",color:"#FFF",cursor:"pointer",fontSize:12,fontWeight:600}} title="Export in your team's input-sheet format">↓ Sheet</button>
        <button onClick={doExport} style={{background:"linear-gradient(135deg,#0F766E,#D97706)",border:"none",borderRadius:6,padding:"4px 14px",color:"#FFF",cursor:"pointer",fontSize:12,fontWeight:600}}>↓ Export</button>
        <button onClick={doLogout} style={{background:"none",border:"1px solid #334155",borderRadius:6,padding:"4px 10px",color:"#94A3B8",cursor:"pointer",fontSize:12}} title="Log out">Logout</button>
        <button onClick={()=>setScreen("upload")} style={{background:"none",border:"1px solid #334155",borderRadius:6,padding:"4px 12px",color:"#94A3B8",cursor:"pointer",fontSize:12}}>←</button>
      </header>
      <div style={{display:"flex",flex:1,overflow:"hidden"}}>
        <aside style={{width:226,background:"#FBFEFD",borderRight:`1px solid ${T.bd}`,padding:"16px 0",overflowY:"auto",flexShrink:0}}>
          <div onClick={()=>{setCurBrand("ALL");setCurIdx(0);}} style={{padding:"8px 16px",cursor:"pointer",fontSize:13,fontWeight:curBrand==="ALL"?700:500,background:curBrand==="ALL"?T.actBg:"transparent",borderLeft:curBrand==="ALL"?`3px solid ${T.ac}`:"3px solid transparent",color:curBrand==="ALL"?T.ac:T.t1}}>ALL ({products.length})</div>
          {Object.entries(brands).map(([name,st])=>(
            <div key={name} onClick={()=>{setCurBrand(name);setCurIdx(0);}} style={{padding:"8px 16px",cursor:"pointer",background:curBrand===name?T.actBg:"transparent",borderLeft:curBrand===name?`3px solid ${T.ac}`:"3px solid transparent"}}>
              <div style={{fontSize:13,fontWeight:curBrand===name?700:500,color:curBrand===name?T.ac:T.t1,marginBottom:4}}>{name}</div>
              <div style={{display:"flex",gap:8,fontSize:10,color:T.t2}}>
                <span style={{color:T.pass}}>✓{st.pass}</span><span style={{color:T.fail}}>✗{st.fail}</span><span style={{color:T.rev}}>?{st.rev}</span>
              </div>
              <div style={{background:"#E2E8F0",height:3,borderRadius:2,marginTop:4}}><div style={{background:T.ac,height:"100%",borderRadius:2,width:`${st.total?Math.round(st.done/st.total*100):0}%`,transition:"width .3s"}}/></div>
              <div style={{fontSize:9,color:T.t2,marginTop:2}}>{st.total-st.done} left · {st.withInput} w/input</div>
            </div>
          ))}
          <div style={{padding:16,borderTop:`1px solid ${T.bd}`,marginTop:12}}>
            <div style={{fontSize:11,fontWeight:700,color:T.t2,marginBottom:8,textTransform:"uppercase",letterSpacing:1}}>Overall</div>
            <div style={{fontSize:22,fontWeight:800,fontFamily:"'Outfit'",color:T.t1}}>{ov.done}<span style={{fontSize:14,color:T.t2}}>/{ov.total}</span></div>
            <div style={{background:"#E2E8F0",height:6,borderRadius:3,marginTop:6}}><div style={{background:`linear-gradient(90deg,${T.ac},#38BDF8)`,height:"100%",borderRadius:3,width:`${ov.pct}%`,transition:"width .4s"}}/></div>
          </div>
        </aside>
        <main style={{flex:1,overflowY:"auto",padding:20,background:"radial-gradient(circle at top right,rgba(20,184,166,.09),transparent 34%), #F3F7F6"}}>
          {!cur?<div style={{textAlign:"center",padding:60,color:T.t2}}>No products.</div>:(<>
            <div style={{background:T.card,borderRadius:10,border:`1px solid ${T.bd}`,padding:20,marginBottom:16,boxShadow:"0 10px 30px rgba(15,118,110,.08)"}}>
              <div style={{display:"flex",gap:16,alignItems:"flex-start"}}>
                {thumb&&<div style={{width:80,height:80,borderRadius:8,overflow:"hidden",flexShrink:0,background:"#F1F5F9",border:"1px solid #E2E8F0"}}><img src={thumb} alt="" style={{width:"100%",height:"100%",objectFit:"contain"}} onError={e=>{e.target.style.display="none"}}/></div>}
                <div style={{flex:1,minWidth:0}}>
                  <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4,flexWrap:"wrap"}}>
                    <a href={cur.productUrl||`https://www.amazon.in/dp/${cur.asin}`} target="_blank" rel="noopener" style={{fontFamily:"'JetBrains Mono'",fontSize:13,fontWeight:600,color:T.ac,textDecoration:"none"}}>{cur.asin}</a>
                    <a href={cur.productUrl||`https://www.amazon.in/dp/${cur.asin}`} target="_blank" rel="noopener" title="Open the live listing on Amazon" style={{display:"inline-flex",alignItems:"center",gap:4,fontSize:11,fontWeight:700,color:T.ac,background:T.acBg||"#ECFDF5",border:`1px solid ${T.ac}`,padding:"2px 9px",borderRadius:6,textDecoration:"none",cursor:"pointer"}}>↗ Open on Amazon</a>
                    <button onClick={mailFailures} title="Open an email draft listing the failed checks for this ASIN" style={{display:"inline-flex",alignItems:"center",gap:4,fontSize:11,fontWeight:700,color:T.fail,background:"#FEF2F2",border:`1px solid ${T.fail}`,padding:"2px 9px",borderRadius:6,cursor:"pointer"}}>✉ Email Fails</button>
                    <span style={{fontSize:12,color:T.t2,fontWeight:600}}>{cur.brand}</span>
                    {!cur.hasInput&&<span style={{background:"#FEF2F2",color:"#EF4444",fontSize:10,fontWeight:700,padding:"1px 8px",borderRadius:4}}>NO INPUT</span>}
                    {as?.done&&<span style={{background:T.passBg,color:T.pass,fontSize:10,fontWeight:700,padding:"1px 8px",borderRadius:4}}>✓ DONE</span>}
                  </div>
                  <div style={{fontSize:14,fontWeight:600,color:T.t1,marginBottom:6,overflow:"hidden",textOverflow:"ellipsis",display:"-webkit-box",WebkitLineClamp:2,WebkitBoxOrient:"vertical",lineHeight:1.4}}>{cur.title||"No title"}</div>
                  <div style={{display:"flex",gap:16,fontSize:12,color:T.t2,flexWrap:"wrap"}}>
                    {cur.price&&<span><span style={{fontWeight:700,color:T.t1}}>₹{cur.price}</span>{cur.mrp&&cur.mrp!==cur.price&&<span style={{textDecoration:"line-through",marginLeft:4}}>₹{cur.mrp}</span>}</span>}
                    {cur.rating&&<span>{cur.rating} ★ ({cur.ratingCount})</span>}
                    <span>{cur.imgCount} images</span>
                  </div>
                </div>
                <div style={{textAlign:"center",flexShrink:0}}>
                  <ScoreRing pct={cur.score}/>
                  <div style={{fontSize:10,color:T.t2,marginTop:4}}><span style={{color:T.pass}}>{cur.pass}P</span>{" "}<span style={{color:T.fail}}>{cur.fail}F</span>{" "}<span style={{color:T.rev}}>{cur.review}R</span></div>
                </div>
              </div>
              <div style={{display:"flex",alignItems:"center",gap:10,marginTop:14,borderTop:`1px solid ${T.bd}`,paddingTop:12}}>
                <button onClick={goP} disabled={curIdx===0} style={{background:"#F1F5F9",border:"none",borderRadius:6,padding:"6px 14px",cursor:curIdx===0?"not-allowed":"pointer",fontSize:13,fontWeight:600,color:T.t1}}>← Prev</button>
                <span style={{fontSize:12,color:T.t2}}>{curIdx+1}/{filtered.length}</span>
                <button onClick={goN} disabled={curIdx>=filtered.length-1} style={{background:"#F1F5F9",border:"none",borderRadius:6,padding:"6px 14px",cursor:curIdx>=filtered.length-1?"not-allowed":"pointer",fontSize:13,fontWeight:600,color:T.t1}}>Next →</button>
                <input value={asinSearch} onChange={e=>setAsinSearch(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")jumpToAsin(asinSearch);}} placeholder="Search ASIN…" style={{background:"#FFF",border:`1px solid ${T.bd}`,borderRadius:6,padding:"6px 10px",fontSize:12,width:130,color:T.t1,fontFamily:"'JetBrains Mono',monospace"}}/>
                <button onClick={()=>jumpToAsin(asinSearch)} style={{background:T.ac,border:"none",borderRadius:6,padding:"6px 12px",cursor:"pointer",fontSize:12,fontWeight:600,color:"#FFF"}}>Go</button>
                <div style={{flex:1}}/>
                <label style={{fontSize:11,color:T.t2,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}><input type="checkbox" checked={hideDone} onChange={e=>setHideDone(e.target.checked)} style={{accentColor:T.ac}}/>Hide done</label>
                {pendingCount>0
                  ?<span style={{fontSize:11,fontWeight:700,color:T.rev,background:T.revBg,border:`1px solid ${T.revB}`,padding:"3px 10px",borderRadius:6}}>{pendingCount} left to decide</span>
                  :<span style={{fontSize:11,fontWeight:700,color:T.pass,background:T.passBg,border:`1px solid ${T.passB}`,padding:"3px 10px",borderRadius:6}}>✓ all decided</span>}
                <button onClick={toggleDone} style={{background:as?.done?T.pass:"#F1F5F9",color:as?.done?"#FFF":T.t1,border:"none",borderRadius:6,padding:"6px 14px",cursor:"pointer",fontSize:12,fontWeight:600,transition:"all .2s"}}>{as?.done?"✓ Done":"Mark Done"}</button>
              </div>
              <input value={as?.notes||""} onChange={e=>setA(cur.asin,s=>({...s,notes:e.target.value}))} placeholder="Notes for this ASIN..." style={{width:"100%",marginTop:10,padding:"6px 10px",borderRadius:6,border:`1px solid ${T.bd}`,fontSize:12,outline:"none",fontFamily:"'DM Sans'",boxSizing:"border-box"}}/>
              {as?.by&&<div style={{marginTop:6,fontSize:11,color:T.t2}}>Validated by: <b>{as.by}</b></div>}
            </div>
            <div style={{display:"flex",gap:8,marginBottom:14,alignItems:"center",flexWrap:"wrap",background:"#FBFEFD",border:`1px solid ${T.bd}`,borderRadius:10,padding:10}}>
              {[{k:"all",l:"All",i:""},{k:"review",l:"Review",i:"⚠"},{k:"failed",l:"Failed",i:"✗"},{k:"passed",l:"Passed",i:"✓"}].map(f=>(
                <button key={f.k} onClick={()=>setFilter(f.k)} style={{padding:"5px 14px",borderRadius:6,fontSize:12,fontWeight:600,cursor:"pointer",border:filter===f.k?`1.5px solid ${T.ac}`:`1.5px solid ${T.bd}`,background:filter===f.k?"#F0F9FF":"transparent",color:filter===f.k?T.ac:T.t2,fontFamily:"'DM Sans'"}}>{f.i} {f.l}</button>
              ))}
              <div style={{flex:1}}/>
              <span style={{fontSize:10,color:T.t2,fontFamily:"'JetBrains Mono'"}}>Y/N/S · ←→ · D </span>
            </div>
            {grouped.map(([gN,items])=>{const exp=expanded[gN]!==false;return<div key={gN} style={{marginBottom:12}}>
              <div onClick={()=>setExpanded(p=>({...p,[gN]:!exp}))} style={{display:"flex",alignItems:"center",gap:8,padding:"8px 12px",background:"#F8FAFC",borderRadius:8,cursor:"pointer",marginBottom:exp?8:0,border:`1px solid ${T.bd}`}}>
                <span style={{fontSize:12,color:T.t2}}>{exp?"▾":"▸"}</span>
                <span style={{fontSize:13,fontWeight:700,color:T.t1,fontFamily:"'Outfit'",flex:1}}>{gN}</span>
                <span style={{fontSize:11,color:T.t2}}>{items.filter(i=>i.check.status==="PASS").length}P/{items.filter(i=>i.check.status==="FAIL").length}F/{items.filter(i=>i.check.status==="REVIEW").length}R</span>
              </div>
              {exp&&items.map(({check,def})=>(<CheckRow key={def.id} check={check} def={def}
                decision={as?.decisions[def.id]||""} comment={as?.comments[def.id]||""} verified={as?.verified[def.id]||false}
                correctedVal={getCorrectedVal(cur.asin,def.id,check.origInputVal)}
                onDecide={v=>setA(cur.asin,s=>({...s,decisions:{...s.decisions,[def.id]:v}}))}
                onComment={v=>setA(cur.asin,s=>({...s,comments:{...s.comments,[def.id]:v}}))}
                onVerify={v=>setA(cur.asin,s=>({...s,verified:{...s.verified,[def.id]:v}}))}
                onEditInput={(checkId,checkName,origVal,newVal)=>setCorrModal({asin:cur.asin,checkId,checkName,origVal,newVal})}
              />))}
            </div>;})}
            {grouped.length===0&&<div style={{textAlign:"center",padding:40,color:T.t2,fontSize:14}}>No checks match filter.</div>}
            <div style={{display:"flex",justifyContent:"center",gap:12,padding:"20px 0 40px",borderTop:`1px solid ${T.bd}`,marginTop:16}}>
              <button onClick={goP} disabled={curIdx===0} style={{background:"#F1F5F9",border:"none",borderRadius:8,padding:"10px 20px",cursor:curIdx===0?"not-allowed":"pointer",fontSize:14,fontWeight:600,color:T.t1}}>← Prev</button>
              <button onClick={saveAndNext} disabled={curIdx>=filtered.length-1} style={{background:"linear-gradient(135deg,#0F766E,#D97706)",border:"none",borderRadius:8,padding:"10px 28px",cursor:curIdx>=filtered.length-1?"not-allowed":"pointer",fontSize:14,fontWeight:700,color:"#FFF",opacity:curIdx>=filtered.length-1?0.5:1}}>Save &amp; Next →</button>
            </div>
          </>)}
        </main>
      </div>
    </div>
  );
}
