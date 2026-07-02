import{useState,useEffect,useCallback,useMemo,useRef}from"react";
import*as XLSX from"xlsx";
import Papa from"papaparse";

/* AMAZON LISTING HYGIENE TOOL v3.1 — 34 Checks · 21 Priority Groups · Corrections DB */

if(!document.getElementById("hyg-fonts")){const l=document.createElement("link");l.id="hyg-fonts";l.href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap";l.rel="stylesheet";document.head.appendChild(l);}

const LS="hyg7";
function ldLS(){try{const s=localStorage.getItem(LS);return s?JSON.parse(s):null;}catch{return null;}}
function svLS(d){try{localStorage.setItem(LS,JSON.stringify(d));}catch{}}

function ss(v){if(v==null)return"";const s=String(v).trim();return["nan","none","nat","undefined","null","#n/a","n/a","#ref!","#value!"].includes(s.toLowerCase())?"":s;}
function nm(s){return ss(s).toLowerCase().replace(/\s+/g," ").trim();}
function strip(s){return ss(s).replace(/[^a-zA-Z0-9]/g,"").toLowerCase();}
function sn(v){const s=ss(v).replace(/[₹,Rs.\s‎]/g,"");const m=s.match(/[\d]+\.?\d*/);return m?parseFloat(m[0]):null;}
function isY(v){const n=nm(v);return["y","yes","correct","updated","true"].includes(n);}
function isN(v){const n=nm(v);return!n||["n","no","n0","incorrect","false","0"].includes(n);}
function pipeC(s){const v=ss(s);return v?v.split("|").filter(x=>x.trim()).length:0;}
function cleanB(b){return ss(b).replace(/^Visit the\s+/i,"").replace(/\s+Store$/i,"").replace(/^Brand:\s*/i,"").trim();}
const EXCL=new Set(["tonor","coleshome","n"]);

// ═══ INPUT SHEET PARSER ═══
function parseInput(wb){
  const rows=[];
  const order=[...wb.SheetNames].sort((a,b)=>{
    if(a.toLowerCase()==="format")return-1;if(b.toLowerCase()==="format")return 1;
    if(a.toLowerCase().includes("format"))return-1;if(b.toLowerCase().includes("format"))return 1;return 0;
  });
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
    for(let i=hR+1;i<raw.length;i++){
      const asin=ss(raw[i]?.[ai]).toUpperCase().trim();
      if(!asin||asin.length<5)continue;
      const obj={_asin:asin,_sheet:sn};
      for(let j=0;j<headers.length;j++){
        const h=headers[j];
        if(h){const key=nm(h);const val=ss(raw[i]?.[j]);if(val)obj[key]=val;}
      }
      rows.push(obj);
    }
    if(rows.length>0)break;
  }
  return rows;
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

// ═══ 34 CHECKS ═══
const CK=[
  {id:"nodding",name:"Nodding",group:"P1 · Noding",p:1},
  {id:"title",name:"Title",group:"P2 · Title Loading",p:2},
  {id:"bullets_avail",name:"Bullets Available",group:"P3 · Bullet Points",p:3},
  {id:"bullets_kw",name:"Benefits & Keywords",group:"P3 · Bullet Points",p:3},
  {id:"bullets_box",name:"Box in Bullets",group:"P3 · Bullet Points",p:3},
  {id:"warranty",name:"Warranty",group:"P3 · Bullet Points",p:3},
  {id:"box_contents",name:"Box Contents",group:"P4 · What's in the Box",p:4},
  {id:"brand_story",name:"Brand Story",group:"P5 · Brand Story",p:5},
  {id:"brand_store",name:"Brand Store",group:"P5 · Brand Story",p:5},
  {id:"mail_qr",name:"Mail/QR",group:"P5 · Brand Story",p:5},
  {id:"colour",name:"Colour",group:"P6 · Color",p:6},
  {id:"weight",name:"Weight",group:"P7 · Product Weight",p:7},
  {id:"dimensions",name:"Dimensions",group:"P8 · Dimensions",p:8},
  {id:"material",name:"Material",group:"P9 · Material",p:9},
  {id:"addl_features",name:"Addl Features",group:"P10 · Additional Features",p:10},
  {id:"manufacturer",name:"Manufacturer",group:"P10 · Additional Features",p:10},
  {id:"packer",name:"Packer",group:"P10 · Additional Features",p:10},
  {id:"importer",name:"Importer",group:"P10 · Additional Features",p:10},
  {id:"backend_kw",name:"Backend Keywords",group:"P11 · Backend Search Terms",p:11},
  {id:"return_policy",name:"Return Policy",group:"P12 · Return Policy",p:12},
  {id:"fee_category",name:"Fee Category",group:"P13 · Referral Category",p:13},
  {id:"variation",name:"Variation",group:"P14 · Variation",p:14},
  {id:"images_5",name:"Images ≥ 5",group:"P15 · Images",p:15},
  {id:"feature_img",name:"Feature Images",group:"P15 · Images",p:15},
  {id:"lifestyle_img",name:"Lifestyle Images",group:"P15 · Images",p:15},
  {id:"cs_image",name:"CS Image",group:"P16 · Customer Support Image",p:16},
  {id:"box_image",name:"Our Box Image",group:"P17 · Box Image",p:17},
  {id:"ours_vs_their",name:"Ours vs Their",group:"P18 · Ours vs Their Image",p:18},
  {id:"listing_video",name:"Listing Video",group:"P19 · Listing Video",p:19},
  {id:"nce",name:"NCE (SP > ₹1500)",group:"P20 · Y/M",p:20},
  {id:"no_cost_emi",name:"No Cost EMI",group:"P20 · Y/M",p:20},
  {id:"ratings_reviews",name:"Ratings & Reviews",group:"P20 · Y/M",p:20},
  {id:"aplus",name:"A+ Content",group:"P21 · A+ Y/M",p:21},
  {id:"description",name:"Description",group:"P21 · A+ Y/M",p:21},
];

// ═══ RE-DECIDE STATUS for a single check ═══
function reDecide(id, crawlVal, inputVal, mode){
  if(id==="variation"||id==="no_cost_emi")return"REVIEW";
  const c=ss(crawlVal), inp=ss(inputVal);
  if(!c&&!inp)return"REVIEW";
  if(!c||!inp)return"REVIEW";
  switch(mode){
    case"nodding":return strip(c)===strip(inp)?"PASS":"FAIL";
    case"title":return nm(c).slice(0,40)===nm(inp).slice(0,40)?"PASS":"FAIL";
    case"yn":{
      if(isY(inp))return c.length>0?"PASS":"FAIL";
      if(isN(inp))return c.length===0?"PASS":"REVIEW";
      const cn=nm(c),hn=nm(inp);
      return(cn===hn||cn.includes(hn.slice(0,25))||hn.includes(cn.slice(0,25)))?"PASS":"FAIL";
    }
    case"text":{const cn=nm(c),hn=nm(inp);if(cn===hn)return"PASS";return(cn.includes(hn.slice(0,25))||hn.includes(cn.slice(0,25)))?"PASS":"FAIL";}
    case"img5":return(parseInt(c)||0)>=5?"PASS":"FAIL";
    case"nce":return"REVIEW";
    default:return nm(c)===nm(inp)?"PASS":"FAIL";
  }
}

// Check mode lookup
const CHECK_MODES={nodding:"nodding",title:"title",bullets_avail:"yn",bullets_kw:"yn",bullets_box:"yn",warranty:"text",box_contents:"text",brand_story:"yn",brand_store:"yn",mail_qr:"yn",colour:"yn",weight:"yn",dimensions:"yn",material:"yn",addl_features:"yn",manufacturer:"yn",packer:"yn",importer:"yn",backend_kw:"yn",return_policy:"text",fee_category:"yn",variation:"text",images_5:"img5",feature_img:"yn",lifestyle_img:"yn",cs_image:"yn",box_image:"yn",ours_vs_their:"yn",listing_video:"yn",nce:"nce",no_cost_emi:"yn",ratings_reviews:"yn",aplus:"yn",description:"yn"};

// ═══ VALIDATION ═══
function validate(cr,ir){
  const R=[];
  const cv=k=>ss(cr[k]||"");
  const iv=(...p)=>gv(ir,...p);

  function decide(id,c,inp,mode){
    if(id==="variation"||id==="no_cost_emi")return"REVIEW";
    if(!c&&!inp)return"REVIEW";
    if(!c||!inp)return"REVIEW";
    switch(mode){
      case"nodding":return strip(c)===strip(inp)?"PASS":"FAIL";
      case"title":return nm(c).slice(0,40)===nm(inp).slice(0,40)?"PASS":"FAIL";
      case"yn":{
        if(isY(inp))return c.length>0?"PASS":"FAIL";
        if(isN(inp))return c.length===0?"PASS":"REVIEW";
        const cn=nm(c),hn=nm(inp);
        return(cn===hn||cn.includes(hn.slice(0,25))||hn.includes(cn.slice(0,25)))?"PASS":"FAIL";
      }
      case"text":{const cn=nm(c),hn=nm(inp);if(cn===hn)return"PASS";return(cn.includes(hn.slice(0,25))||hn.includes(cn.slice(0,25)))?"PASS":"FAIL";}
      case"img5":return(parseInt(c)||0)>=5?"PASS":"FAIL";
      case"nce":{const sp=sn(cv("Selling Price"));if(isY(inp))return(sp&&sp>1500)?"PASS":"FAIL";if(isN(inp))return(sp&&sp<=1500)?"PASS":"FAIL";return"REVIEW";}
      default:return nm(c)===nm(inp)?"PASS":"FAIL";
    }
  }
  function add(id,c,inp,mode="yn"){const a=ss(c),b=ss(inp);R.push({id,crawlVal:a,inputVal:b,origInputVal:b,status:decide(id,a,b,mode)});}

  add("nodding",cv("Category Tree"),iv("correct nodding","nodding on pdp"),"nodding");
  add("title",cv("Title"),iv("title name"),"title");
  const bul=cv("Bullets");
  add("bullets_avail",bul?"Present":"",iv("bullet points available"),"yn");
  add("bullets_kw",bul?"Present":"",iv("bullets highlight benefit","bullets highlight benefits"),"yn");
  add("bullets_box",bul?"Present":"",iv("what is in the box in bullet"),"yn");
  add("warranty",cv("Warranty Policy")||cv("Warranty Description"),iv("correct warranty","warranty  on panel","warranty on panel"),"text");
  add("box_contents",cv("What is in the box?"),iv("what's in the box (box","what's in the box"),"text");
  add("brand_story",cv("Brand Story"),iv("product added in brand story","brand story"),"yn");
  add("brand_store","",iv("product added in brand store","brand store"),"yn");
  add("mail_qr","",iv("mail id","cs wa qr","qr code"),"yn");
  add("colour",cv("Colour"),iv("colour","color"),"yn");
  add("weight",cv("Weight"),iv("iteam weight","item weight"),"yn");
  add("dimensions",cv("Dimensions"),iv("dimensions"),"yn");
  add("material",cv("Material"),iv("material"),"yn");
  add("addl_features",cv("Additional Features"),iv("additional feature"),"yn");
  add("manufacturer",cv("Manufacturer Contact Information"),iv("mfg detail","manufacturer"),"yn");
  add("packer",cv("Packer Contact Information"),iv("packer detail","packer"),"yn");
  add("importer",cv("Importer Contact Information"),iv("importer detail","importer"),"yn");
  add("backend_kw","",iv("backend search term","search terms (y"),"yn");
  add("return_policy",cv("Return Policy"),iv("correct return policy","return policy on page"),"text");
  add("fee_category","",iv("correct fee category","fee category in backend"),"yn");
  add("variation",cv("Variation Data"),iv("correct variation","variation on pdp"),"text");
  const ic=pipeC(cv("Image URLs"));
  add("images_5",String(ic),iv("images - minimum"),"img5");
  add("feature_img",ic>0?"Present":"",iv("feature image"),"yn");
  add("lifestyle_img",ic>0?"Present":"",iv("lifestyle image"),"yn");
  add("cs_image","",iv("customer support image"),"yn");
  add("box_image","",iv("what is in the box image"),"yn");
  add("ours_vs_their","",iv("ours vs their"),"yn");
  add("listing_video",cv("Listing Video"),iv("listing video"),"yn");
  add("nce",cv("Selling Price"),iv("nce (sp","nce"),"nce");
  add("no_cost_emi","","","yn");
  const rat=cv("Rating")&&cv("Rating Count")?`${cv("Rating")} ★ (${cv("Rating Count")})`:"";
  add("ratings_reviews",rat,iv("ratings","rating"),"yn");
  add("aplus",cv("A+ Content")?"Present":"",iv("a+ (y","a+ y"),"yn");
  add("description",cv("Description"),iv("description / a+","description/a+"),"yn");
  return R;
}

// ═══ UI ═══
const T={bg:"#F0F4F8",hd:"#0F172A",ac:"#0EA5E9",card:"#FFF",bd:"#E2E8F0",t1:"#0F172A",t2:"#64748B",
  pass:"#10B981",passBg:"#ECFDF5",passB:"#BBF7D0",fail:"#EF4444",failBg:"#FEF2F2",failB:"#FECACA",
  rev:"#F59E0B",revBg:"#FFFBEB",revB:"#FDE68A",actBg:"#F0F9FF"};

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
          <button onClick={startEdit} style={{background:"none",border:"none",cursor:"pointer",fontSize:13,padding:"0 2px",color:T.t2,lineHeight:1}} title="Edit input value">✏️</button>
        </div>
        {editing?(
          <div style={{display:"flex",gap:4}}>
            <input ref={inputRef} value={editVal} onChange={e=>setEditVal(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")confirmEdit();if(e.key==="Escape"){setEditing(false);}}}
              style={{flex:1,fontSize:12,fontFamily:"'JetBrains Mono'",padding:"4px 6px",borderRadius:4,border:`1.5px solid ${T.ac}`,outline:"none",background:"#FFF",boxSizing:"border-box"}}/>
            <button onClick={confirmEdit} style={{background:T.ac,color:"#FFF",border:"none",borderRadius:4,padding:"4px 8px",fontSize:11,fontWeight:700,cursor:"pointer"}}>✓</button>
            <button onClick={()=>setEditing(false)} style={{background:"#F1F5F9",color:T.t2,border:"none",borderRadius:4,padding:"4px 8px",fontSize:11,cursor:"pointer"}}>✗</button>
          </div>
        ):(
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
export default function App(){
  const[screen,setScreen]=useState("upload");
  const[crawlData,setCrawlData]=useState([]);
  const[inputData,setInputData]=useState([]);
  const[products,setProducts]=useState([]);
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

  useEffect(()=>{const d=ldLS();if(d){if(d.asinSt)setAsinSt(d.asinSt);if(d.validator)setValidator(d.validator);if(d.curBrand)setCurBrand(d.curBrand);if(d.corrections)setCorrections(d.corrections);}},[]);
  useEffect(()=>{if(Object.keys(asinSt).length>0)svLS({asinSt,validator,curBrand,curIdx,corrections});},[asinSt,validator,curBrand,curIdx,corrections]);

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

  const onCrawl=useCallback((e)=>{
    const file=e.target.files[0];if(!file)return;
    setCrawlName(file.name);
    const ext=file.name.split(".").pop().toLowerCase();
    const reader=new FileReader();
    if(ext==="csv"){
      reader.onload=ev=>{const rows=parseCrawl(ev.target.result);setCrawlData(rows);addLog(`✅ Crawl: ${rows.length} ASINs from ${file.name}`);};
      reader.readAsText(file);
    }else{
      reader.onload=ev=>{const wb=XLSX.read(ev.target.result,{type:"array"});const csv=XLSX.utils.sheet_to_csv(wb.Sheets[wb.SheetNames[0]]);const rows=parseCrawl(csv);setCrawlData(rows);addLog(`✅ Crawl: ${rows.length} ASINs from ${file.name}`);};
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
          setInputData(prev=>{const ex=new Set(prev.map(r=>r._asin));return[...prev,...rows.filter(r=>!ex.has(r._asin))];});
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
        const score=checks.length?Math.round(pass/checks.length*100):0;
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

  const brands=useMemo(()=>{const m={};products.forEach(p=>{if(!m[p.brand])m[p.brand]={total:0,done:0,withInput:0,pass:0,fail:0,rev:0};m[p.brand].total++;if(p.hasInput)m[p.brand].withInput++;m[p.brand].pass+=p.pass;m[p.brand].fail+=p.fail;m[p.brand].rev+=p.review;if(asinSt[p.asin]?.done)m[p.brand].done++;});return m;},[products,asinSt]);
  const filtered=useMemo(()=>{let l=curBrand==="ALL"?products:products.filter(p=>p.brand===curBrand);if(hideDone)l=l.filter(p=>!asinSt[p.asin]?.done);return l;},[products,curBrand,hideDone,asinSt]);
  const cur=filtered[curIdx]||null;
  const getA=a=>asinSt[a]||{decisions:{},comments:{},verified:{},done:false,notes:""};
  const setA=(a,fn)=>setAsinSt(p=>({...p,[a]:fn(p[a]||{decisions:{},comments:{},verified:{},done:false,notes:""})}));
  const goN=()=>setCurIdx(i=>Math.min(i+1,filtered.length-1));
  const goP=()=>setCurIdx(i=>Math.max(i-1,0));

  useEffect(()=>{if(screen!=="validate"||!cur)return;const h=e=>{if(e.target.tagName==="INPUT"||e.target.tagName==="TEXTAREA")return;if(e.key==="ArrowRight"){goN();e.preventDefault();}else if(e.key==="ArrowLeft"){goP();e.preventDefault();}else if(["y","n","s"].includes(e.key.toLowerCase())){const val=e.key.toLowerCase()==="y"?"Yes":e.key.toLowerCase()==="n"?"No":"Not Sure";const a=getA(cur.asin);const pend=cur.checks.find(c=>c.status==="REVIEW"&&(!a.decisions[c.id]||a.decisions[c.id]==="Not Sure"));if(pend)setA(cur.asin,s=>({...s,decisions:{...s.decisions,[pend.id]:val}}));}else if(e.key.toLowerCase()==="d")setA(cur.asin,s=>({...s,done:true}));};window.addEventListener("keydown",h);return()=>window.removeEventListener("keydown",h);},[screen,cur,curIdx,filtered]);

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
    products.forEach((p,i)=>{const a=getA(p.asin);const row={Sr:i+1,Brand:p.brand,ASIN:p.asin,Title:p.title?.slice(0,80),Score:p.score+"%",Pass:p.pass,Fail:p.fail,Review:p.review,Reviewed:a.done?"Yes":"No"};
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
  const thumb=cur?.imgUrls?.split("|")[0]?.trim()||"";

  const corrCount=Object.keys(corrections.global).length+Object.values(corrections.byAsin).reduce((s,o)=>s+Object.keys(o).length,0);

  // ═══ UPLOAD SCREEN ═══
  if(screen==="upload")return(
    <div style={{minHeight:"100vh",background:"linear-gradient(135deg,#0F172A 0%,#1E293B 50%,#0F172A 100%)",display:"flex",alignItems:"center",justifyContent:"center",fontFamily:"'DM Sans',sans-serif",padding:20}}>
      <div style={{maxWidth:640,width:"100%"}}>
        <div style={{textAlign:"center",marginBottom:40}}>
          <div style={{fontSize:36,fontWeight:800,fontFamily:"'Outfit'",background:"linear-gradient(135deg,#0EA5E9,#38BDF8,#7DD3FC)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent",marginBottom:8}}>Hygiene Tool v3.1</div>
          <div style={{color:"#94A3B8",fontSize:14}}>Amazon Listing Validation · 34 Checks · 21 Priority Groups</div>
          <div style={{color:"#475569",fontSize:11,marginTop:8}}>PASS = both sides match · FAIL = both mismatch · REVIEW = one/both empty</div>
          {corrCount>0&&<div style={{color:"#A78BFA",fontSize:12,marginTop:8,fontWeight:600}}>📦 {corrCount} corrections in database — will auto-apply on validate</div>}
        </div>
        <div style={{marginBottom:24,textAlign:"center"}}>
          <input value={validator} onChange={e=>setValidator(e.target.value)} placeholder="Your Name (Validator)" style={{background:"#1E293B",border:"1px solid #334155",borderRadius:8,padding:"10px 16px",color:"#F1F5F9",fontSize:14,width:280,textAlign:"center",outline:"none",fontFamily:"'DM Sans'"}}/>
        </div>
        <div style={{display:"grid",gap:16}}>
          <div onClick={()=>crRef.current?.click()} style={{background:"#1E293B",borderRadius:12,border:`2px dashed ${crawlData.length>0?"#10B981":"#334155"}`,padding:"28px 24px",cursor:"pointer",textAlign:"center"}}>
            <input ref={crRef} type="file" accept=".csv,.xlsx,.xls" onChange={onCrawl} style={{display:"none"}}/>
            <div style={{fontSize:28,marginBottom:8}}>{crawlData.length>0?"✅":"📊"}</div>
            <div style={{color:"#F1F5F9",fontWeight:600,fontSize:15}}>Crawl Data {crawlData.length>0?`(${crawlData.length} ASINs)`:""}</div>
            <div style={{color:"#64748B",fontSize:12}}>{crawlName||"amazon_products_full.csv"}</div>
          </div>
          <div onClick={()=>inRef.current?.click()} style={{background:"#1E293B",borderRadius:12,border:`2px dashed ${inputData.length>0?"#10B981":"#334155"}`,padding:"28px 24px",cursor:"pointer",textAlign:"center"}}>
            <input ref={inRef} type="file" accept=".xlsx,.xls,.json" multiple onChange={onInputFile} style={{display:"none"}}/>
            <div style={{fontSize:28,marginBottom:8}}>{inputData.length>0?"✅":"📋"}</div>
            <div style={{color:"#F1F5F9",fontWeight:600,fontSize:15}}>Input Sheets {inputData.length>0?`(${inputData.length} rows)`:""}</div>
            <div style={{color:"#64748B",fontSize:12}}>{inputNames.length>0?inputNames.join(", "):"Click multiple times to add sheets · JSON backup loads corrections"}</div>
          </div>
        </div>
        {log&&<pre style={{marginTop:16,padding:12,background:"#1E293B",border:"1px solid #334155",borderRadius:8,color:"#94A3B8",fontSize:10,fontFamily:"'JetBrains Mono'",whiteSpace:"pre-wrap",maxHeight:200,overflow:"auto"}}>{log}</pre>}
        <div style={{textAlign:"center",marginTop:28}}>
          <button onClick={onValidate} disabled={!crawlData.length} style={{background:crawlData.length>0?"linear-gradient(135deg,#0EA5E9,#0284C7)":"#334155",color:"#FFF",border:"none",borderRadius:10,padding:"14px 40px",fontSize:16,fontWeight:700,cursor:crawlData.length>0?"pointer":"not-allowed",fontFamily:"'Outfit'",letterSpacing:.5,boxShadow:crawlData.length>0?"0 4px 20px rgba(14,165,233,.3)":"none"}}>
            Validate {crawlData.length} ASINs →
          </button>
        </div>
        <div style={{textAlign:"center",marginTop:16,color:"#475569",fontSize:11}}>Y/N/S = decide · ←→ navigate · D = mark done · ✏️ edit input values</div>
      </div>
    </div>
  );

  // ═══ VALIDATION SCREEN ═══
  return(
    <div style={{minHeight:"100vh",background:T.bg,fontFamily:"'DM Sans',sans-serif",display:"flex",flexDirection:"column"}}>
      {corrModal&&<CorrectionModal checkName={corrModal.checkName} asin={corrModal.asin} origVal={corrModal.origVal} newVal={corrModal.newVal} onApply={applyCorrection} onCancel={()=>setCorrModal(null)}/>}
      <header style={{background:T.hd,padding:"0 20px",height:52,display:"flex",alignItems:"center",gap:16,boxShadow:"0 2px 8px rgba(0,0,0,.2)",position:"sticky",top:0,zIndex:100}}>
        <span style={{fontFamily:"'Outfit'",fontWeight:800,fontSize:18,background:"linear-gradient(135deg,#0EA5E9,#38BDF8)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>Hygiene v3.1</span>
        <span style={{color:"#64748B",fontSize:12}}>{ov.done}/{ov.total} ({ov.pct}%)</span>
        {corrCount>0&&<span style={{color:"#A78BFA",fontSize:10,fontWeight:600,background:"#1E1B4B",padding:"2px 8px",borderRadius:4}}>📦 {corrCount} fixes</span>}
        <div style={{flex:1}}/>
        <input value={validator} onChange={e=>setValidator(e.target.value)} placeholder="Validator" style={{background:"#1E293B",border:"1px solid #334155",borderRadius:6,padding:"4px 10px",color:"#E2E8F0",fontSize:12,width:120,outline:"none",fontFamily:"'DM Sans'"}}/>
        <button onClick={backup} style={{background:"none",border:"1px solid #334155",borderRadius:6,padding:"4px 12px",color:"#94A3B8",cursor:"pointer",fontSize:12}} title="Backup JSON (includes corrections)">💾</button>
        <button onClick={exportCorrections} style={{background:"none",border:"1px solid #7C3AED",borderRadius:6,padding:"4px 12px",color:"#A78BFA",cursor:"pointer",fontSize:12,fontWeight:600}} title="Export corrections DB as Excel">📦</button>
        <button onClick={doExport} style={{background:"linear-gradient(135deg,#F59E0B,#EF4444)",border:"none",borderRadius:6,padding:"4px 14px",color:"#FFF",cursor:"pointer",fontSize:12,fontWeight:600}}>↓ Export</button>
        <button onClick={()=>setScreen("upload")} style={{background:"none",border:"1px solid #334155",borderRadius:6,padding:"4px 12px",color:"#94A3B8",cursor:"pointer",fontSize:12}}>←</button>
      </header>
      <div style={{display:"flex",flex:1,overflow:"hidden"}}>
        <aside style={{width:220,background:"#FFF",borderRight:`1px solid ${T.bd}`,padding:"16px 0",overflowY:"auto",flexShrink:0}}>
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
        <main style={{flex:1,overflowY:"auto",padding:20}}>
          {!cur?<div style={{textAlign:"center",padding:60,color:T.t2}}>No products.</div>:(<>
            <div style={{background:T.card,borderRadius:14,border:`1px solid ${T.bd}`,padding:20,marginBottom:16,boxShadow:"0 1px 4px rgba(0,0,0,.04)"}}>
              <div style={{display:"flex",gap:16,alignItems:"flex-start"}}>
                {thumb&&<div style={{width:80,height:80,borderRadius:8,overflow:"hidden",flexShrink:0,background:"#F1F5F9",border:"1px solid #E2E8F0"}}><img src={thumb} alt="" style={{width:"100%",height:"100%",objectFit:"contain"}} onError={e=>{e.target.style.display="none"}}/></div>}
                <div style={{flex:1,minWidth:0}}>
                  <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4,flexWrap:"wrap"}}>
                    <a href={cur.productUrl||`https://www.amazon.in/dp/${cur.asin}`} target="_blank" rel="noopener" style={{fontFamily:"'JetBrains Mono'",fontSize:13,fontWeight:600,color:T.ac,textDecoration:"none"}}>{cur.asin}</a>
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
                <div style={{flex:1}}/>
                <label style={{fontSize:11,color:T.t2,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}><input type="checkbox" checked={hideDone} onChange={e=>setHideDone(e.target.checked)} style={{accentColor:T.ac}}/>Hide done</label>
                <button onClick={()=>setA(cur.asin,s=>({...s,done:!s.done}))} style={{background:as?.done?T.pass:"#F1F5F9",color:as?.done?"#FFF":T.t1,border:"none",borderRadius:6,padding:"6px 14px",cursor:"pointer",fontSize:12,fontWeight:600,transition:"all .2s"}}>{as?.done?"✓ Done":"Mark Done"}</button>
              </div>
              <input value={as?.notes||""} onChange={e=>setA(cur.asin,s=>({...s,notes:e.target.value}))} placeholder="Notes for this ASIN..." style={{width:"100%",marginTop:10,padding:"6px 10px",borderRadius:6,border:`1px solid ${T.bd}`,fontSize:12,outline:"none",fontFamily:"'DM Sans'",boxSizing:"border-box"}}/>
            </div>
            <div style={{display:"flex",gap:8,marginBottom:14,alignItems:"center",flexWrap:"wrap"}}>
              {[{k:"all",l:"All",i:""},{k:"review",l:"Review",i:"⚠"},{k:"failed",l:"Failed",i:"✗"},{k:"passed",l:"Passed",i:"✓"}].map(f=>(
                <button key={f.k} onClick={()=>setFilter(f.k)} style={{padding:"5px 14px",borderRadius:6,fontSize:12,fontWeight:600,cursor:"pointer",border:filter===f.k?`1.5px solid ${T.ac}`:`1.5px solid ${T.bd}`,background:filter===f.k?"#F0F9FF":"transparent",color:filter===f.k?T.ac:T.t2,fontFamily:"'DM Sans'"}}>{f.i} {f.l}</button>
              ))}
              <div style={{flex:1}}/>
              <span style={{fontSize:10,color:T.t2,fontFamily:"'JetBrains Mono'"}}>Y/N/S · ←→ · D · ✏️ edit</span>
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
          </>)}
        </main>
      </div>
    </div>
  );
}
