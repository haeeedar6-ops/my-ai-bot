"""موقع Ryn AI: واجهة دردشة (PWA) فوق نفس عقل البوت. كل جلسة متصفح مستقلة تماماً."""
import os
import hmac
import time
import uuid
import json
import hashlib
import threading
import logging

from flask import request, session, jsonify, Response

log = logging.getLogger("web")
MAX_UPLOAD = 15 * 1024 * 1024
APP_NAME = os.environ.get("APP_NAME", "Ryn AI")

ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#8b5cf6"/><stop offset="1" stop-color="#00ff9d"/>'
    '</linearGradient></defs>'
    '<rect width="192" height="192" rx="40" fill="#070816"/>'
    '<circle cx="96" cy="96" r="60" fill="none" stroke="url(#g)" stroke-width="3"/>'
    '<text x="96" y="120" text-anchor="middle" font-size="80" '
    'font-family="Arial,sans-serif" fill="#00ff9d" font-weight="bold">R</text>'
    '<circle cx="96" cy="58" r="6" fill="#00ff9d"/>'
    '</svg>'
).encode()

MANIFEST = {
    "name": APP_NAME, "short_name": APP_NAME.split()[0], "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#070816", "theme_color": "#070816",
    "lang": "ar", "dir": "rtl",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
}
SW_JS = ("self.addEventListener('install',e=>self.skipWaiting());"
         "self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));")

INDEX_HTML = r'''<!doctype html>
<html lang="ar" dir="rtl" data-theme="violet">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070816">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon.svg">
<title>__APP_NAME__</title>
<style>
:root{--g:#00ff9d;--g-rgb:0,255,157;--red:#ff5d6c;--txt:#eef0ff;--sub:rgba(238,240,255,.55);
 --acc:#8b5cf6;--acc-rgb:139,92,246;--me1:#4c1d95;--me2:#2e1065;--bg1:#070816;--bg2:#0d0a26}
[data-theme=blue]{--acc:#38bdf8;--acc-rgb:56,189,248;--me1:#0c4a6e;--me2:#082f49;--bg1:#060b16;--bg2:#08142a}
[data-theme=orange]{--acc:#fb923c;--acc-rgb:251,146,60;--me1:#7c2d12;--me2:#431407;--bg1:#0d0806;--bg2:#1a0d08}
[data-theme=green]{--acc:#10b981;--acc-rgb:16,185,129;--me1:#064e34;--me2:#042f20;--bg1:#030906;--bg2:#051a10}
*{box-sizing:border-box;margin:0;padding:0;font-family:'Segoe UI',Tahoma,'Noto Sans Arabic',system-ui,sans-serif;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{background:var(--bg1);color:var(--txt);display:flex;justify-content:center;align-items:center;overflow:hidden;
 background-image:radial-gradient(circle at 15% 15%,rgba(var(--acc-rgb),.18),transparent 42%),radial-gradient(circle at 85% 85%,rgba(var(--g-rgb),.12),transparent 42%),linear-gradient(rgba(var(--acc-rgb),.05) 1px,transparent 1px),linear-gradient(90deg,rgba(var(--acc-rgb),.05) 1px,transparent 1px);
 background-size:100% 100%,100% 100%,34px 34px,34px 34px}
button{font-family:inherit;cursor:pointer}
[hidden]{display:none!important}
#app{position:relative;width:100%;height:100vh;height:100dvh;max-width:440px;display:flex;flex-direction:column;overflow:hidden;
 background:linear-gradient(180deg,var(--bg2) 0%,var(--bg1) 70%,var(--bg2) 100%);padding:calc(env(safe-area-inset-top,0px) + 10px) 12px 0}
@media(min-width:600px){#app{height:min(800px,95vh);border-radius:40px;border:2px solid rgba(var(--acc-rgb),.45);padding:18px 14px 0;
 box-shadow:0 25px 60px rgba(0,0,0,.85),0 0 40px rgba(var(--acc-rgb),.25),inset 0 0 24px rgba(var(--acc-rgb),.08)}}
.grid3d{position:absolute;bottom:0;left:-25%;width:150%;height:220px;pointer-events:none;z-index:0;
 background-image:linear-gradient(to right,rgba(var(--acc-rgb),.22) 1px,transparent 1px),linear-gradient(to bottom,rgba(var(--acc-rgb),.22) 1px,transparent 1px);
 background-size:28px 28px;transform:perspective(180px) rotateX(65deg);transform-origin:bottom;
 -webkit-mask-image:linear-gradient(to top,#000 20%,transparent 100%);mask-image:linear-gradient(to top,#000 20%,transparent 100%)}
.top{position:relative;z-index:3;flex:none;display:flex;flex-direction:column;align-items:center;gap:2px}
.mini{position:absolute;top:4px;width:38px;height:38px;border-radius:12px;background:rgba(var(--acc-rgb),.12);border:1.5px solid rgba(var(--acc-rgb),.55);color:var(--txt);font-size:18px;display:flex;align-items:center;justify-content:center}
#histBtn{right:0}#menuBtn{left:0}
.botwrap{width:112px;height:112px;position:relative}
.botwrap::before{content:"";position:absolute;inset:6px;border-radius:50%;border:2px dashed rgba(var(--acc-rgb),.5);animation:spin 18s linear infinite}
.botwrap::after{content:"";position:absolute;inset:0;border-radius:50%;box-shadow:0 0 34px rgba(var(--acc-rgb),.35),inset 0 0 22px rgba(var(--g-rgb),.12);background:radial-gradient(circle,rgba(var(--acc-rgb),.16) 0%,transparent 70%);pointer-events:none}
@keyframes spin{to{transform:rotate(360deg)}}
.robot{position:relative;z-index:1;width:100%;height:100%;overflow:visible;--ex:0px;--ey:0px}
.robot .float{animation:fl 3.4s ease-in-out infinite}
@keyframes fl{0%,100%{transform:translateY(0)}50%{transform:translateY(-3.5px)}}
.robot .eye{transform-box:fill-box;transform-origin:center;animation:blink 5.5s infinite;transition:transform .2s}
@keyframes blink{0%,93%,100%{transform:scaleY(1)}96%{transform:scaleY(.08)}}
.robot #eyes{transform:translate(var(--ex),var(--ey));transition:transform .15s ease-out}
.robot .orb{animation:orb 2.2s ease-in-out infinite;transform-box:fill-box;transform-origin:center}
@keyframes orb{0%,100%{opacity:.55;transform:scale(.9)}50%{opacity:1;transform:scale(1.25)}}
.robot .m{display:none}.robot .m-smile{display:block}
.robot.typing #eyes{transform:translate(0,4px)}
.robot.thinking #eyes{animation:scan 1.1s ease-in-out infinite}
@keyframes scan{0%,100%{transform:translate(-5px,-1px)}50%{transform:translate(5px,-1px)}}
.robot.thinking .m-smile{display:none}.robot.thinking .m-flat{display:block}
.robot.speaking .m-smile{display:none}.robot.speaking .m-open{display:block;transform-box:fill-box;transform-origin:center;animation:talk .22s ease-in-out infinite}
@keyframes talk{0%,100%{transform:scaleY(.35)}50%{transform:scaleY(1)}}
.robot.happy .eye{transform:scaleY(.5);animation:none}
.robot.happy .float{animation:hop .5s ease-out 1}
@keyframes hop{0%{transform:translateY(0)}40%{transform:translateY(-9px)}100%{transform:translateY(0)}}
.robot.error .eye rect{fill:var(--red)}.robot.error .m-smile{display:none}.robot.error .m-sad{display:block}
.robot.error .float{animation:shake .45s linear 2}
@keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-4px)}75%{transform:translateX(4px)}}
#say{font-size:13px;color:var(--g);min-height:20px;text-shadow:0 0 8px rgba(var(--g-rgb),.55);text-align:center;max-width:92%}
#mname{margin-top:2px;font-size:12px;color:var(--txt);border:1px solid rgba(var(--acc-rgb),.55);border-radius:12px;padding:2px 12px;background:rgba(var(--acc-rgb),.14);max-width:70%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;cursor:pointer}
@media(max-height:700px){.botwrap{width:84px;height:84px}}
#menu{position:absolute;top:calc(env(safe-area-inset-top,0px) + 54px);left:12px;background:rgba(10,8,30,.98);border:1.5px solid rgba(var(--acc-rgb),.7);border-radius:14px;padding:6px;z-index:40;min-width:210px;box-shadow:0 0 24px rgba(var(--acc-rgb),.35)}
#menu button,#menu a{display:flex;width:100%;gap:8px;align-items:center;background:none;border:0;color:var(--txt);padding:11px 10px;font-size:14px;border-radius:8px;text-align:right;text-decoration:none}
#menu button:hover,#menu a:hover{background:rgba(var(--acc-rgb),.18)}
.themes{display:flex;gap:10px;justify-content:center;padding:8px 6px 6px;border-top:1px solid rgba(var(--acc-rgb),.25);margin-top:4px}
.themes i{width:26px;height:26px;border-radius:50%;border:2px solid transparent;cursor:pointer;display:block}
.themes i.on{border-color:#fff;box-shadow:0 0 10px currentColor}
main{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:20px;padding:12px 4px 24px;z-index:2;scrollbar-width:none}
main::-webkit-scrollbar{display:none}
main>:first-child{margin-top:auto}
.bubble{position:relative;padding:13px 16px;border-radius:18px;font-size:15px;line-height:1.75;color:var(--txt);overflow-wrap:anywhere;word-break:break-word;animation:fi .25s ease-out;text-align:right}
@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.bot{align-self:flex-start;max-width:94%;background:rgba(18,16,44,.88);border:1.5px solid rgba(var(--g-rgb),.55);box-shadow:0 6px 20px rgba(0,0,0,.55),0 0 14px rgba(var(--g-rgb),.18)}
.bot::after{content:"";position:absolute;bottom:-9px;right:18px;width:14px;height:14px;background:#12102c;border-right:1.5px solid rgba(var(--g-rgb),.55);border-bottom:1.5px solid rgba(var(--g-rgb),.55);transform:rotate(45deg)}
.me{align-self:flex-end;max-width:86%;background:linear-gradient(135deg,var(--me1),var(--me2));border:1.5px solid var(--acc);box-shadow:0 6px 20px rgba(0,0,0,.5),0 0 16px rgba(var(--acc-rgb),.4)}
.me::after{content:"";position:absolute;bottom:-9px;left:18px;width:14px;height:14px;background:var(--me2);border-left:1.5px solid var(--acc);border-bottom:1.5px solid var(--acc);transform:rotate(-45deg)}
.err{border-color:var(--red)!important}.err::after{border-color:var(--red)!important}
.sys{align-self:center;font-size:12px;color:var(--g);opacity:.9;text-shadow:0 0 8px rgba(var(--g-rgb),.5)}
.note{align-self:flex-start;font-size:12px;color:var(--sub);margin:-12px 8px 0}
.acts{align-self:flex-start;margin:-14px 6px 0}
.acts button{background:none;border:0;color:var(--sub);font-size:12px;padding:4px 6px}
.bubble code{background:#07061a;border:1px solid rgba(var(--acc-rgb),.35);border-radius:6px;padding:1px 5px;font-family:Consolas,monospace;font-size:13px;direction:ltr;unicode-bidi:embed;color:#c8ffe6}
.bubble ul{margin:6px 0;padding-inline-start:22px}
.code{margin:10px 0 4px;border:1px solid rgba(var(--acc-rgb),.5);border-radius:12px;overflow:hidden;background:#05041a;direction:ltr;text-align:left}
.ch{display:flex;justify-content:space-between;align-items:center;background:rgba(var(--acc-rgb),.16);padding:6px 10px;font-size:12px;color:var(--g)}
.ch .cp{background:none;border:1px solid rgba(var(--g-rgb),.5);color:var(--g);border-radius:8px;padding:3px 9px;font-size:12px}
.code pre{margin:0;padding:10px;overflow-x:auto;font-size:13px;line-height:1.55}
.code pre code{background:none;border:0;padding:0;white-space:pre;color:#e4ffef}
.pic{display:block;max-width:100%;max-height:230px;border-radius:12px;margin:0 0 8px;border:1px solid rgba(var(--g-rgb),.5);cursor:zoom-in}
.h{font-weight:800;color:var(--g);margin:6px 0 2px;text-shadow:0 0 8px rgba(var(--g-rgb),.4)}
#lb{position:absolute;inset:0;background:rgba(0,0,0,.92);z-index:80;display:flex;align-items:center;justify-content:center;padding:14px}
#lb img{max-width:100%;max-height:100%;border-radius:12px}
.typing{display:inline-flex;gap:5px;padding:4px 2px}
.typing i{width:7px;height:7px;border-radius:50%;background:var(--g);opacity:.3;animation:bn 1s infinite}
.typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}
@keyframes bn{0%,100%{opacity:.25;transform:translateY(0)}50%{opacity:1;transform:translateY(-4px)}}
.hint{margin:auto;text-align:center;color:var(--sub);font-size:14px;line-height:2.1}.hint b{color:var(--g)}
.floating{position:absolute;bottom:92px;right:18px;display:flex;flex-direction:column;align-items:center;gap:32px;z-index:10;transition:.3s ease}
.floating.off{opacity:0;pointer-events:none;transform:translateY(15px) scale(.9)}
.dia{width:54px;height:54px;background:rgba(14,10,40,.94);border:2px solid var(--acc);border-radius:12px;transform:rotate(45deg);display:flex;justify-content:center;align-items:center;cursor:pointer;box-shadow:0 0 18px rgba(var(--acc-rgb),.5),inset 0 0 10px rgba(var(--acc-rgb),.2);transition:.2s;color:var(--txt);padding:0}
.dia:hover,.dia.on{background:rgba(var(--g-rgb),.22);border-color:var(--g);box-shadow:0 0 26px rgba(var(--g-rgb),.7)}
.dia span{transform:rotate(-45deg);font-size:10px;font-weight:700;text-align:center;line-height:1.25}
#sheet{position:absolute;left:10px;right:10px;bottom:92px;max-height:52%;overflow-y:auto;background:#09071c;border:1.5px solid var(--acc);border-radius:18px;padding:8px;z-index:12;box-shadow:0 0 26px rgba(var(--acc-rgb),.4)}
#sheet button{display:block;width:100%;text-align:right;background:none;border:0;color:var(--txt);padding:10px 12px;font-size:14px;border-radius:10px;direction:ltr}
#sheet button b{display:block;font-weight:700}
#sheet button small{display:block;direction:rtl;text-align:right;color:var(--sub);font-size:11.5px;line-height:1.55;margin-top:3px;font-weight:400}
#sheet button.on small,#sheet button:hover small{color:rgba(var(--g-rgb),.8)}
#sheet button:hover,#sheet button.on{background:#0b2a28;color:var(--g)}
#scrim{position:absolute;inset:0;background:rgba(0,0,0,.55);z-index:44}
.drawer{position:absolute;top:0;bottom:0;right:0;width:min(84%,330px);background:rgba(9,7,28,.98);border-left:1.5px solid var(--acc);z-index:45;display:flex;flex-direction:column;padding:calc(env(safe-area-inset-top,0px) + 14px) 12px 14px;transform:translateX(105%);transition:transform .28s ease;box-shadow:-10px 0 40px rgba(0,0,0,.6)}
.drawer.on{transform:none}
.dh{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;color:var(--g);font-size:16px}
.dh button{background:none;border:0;color:var(--txt);font-size:18px}
.dnew{width:100%;padding:11px;border-radius:14px;border:1.5px solid var(--g);background:rgba(var(--g-rgb),.12);color:var(--g);font-weight:700;font-size:15px;margin-bottom:12px}
#dList{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px}
.ci{display:flex;align-items:center;gap:8px;padding:11px 12px;border-radius:14px;border:1.5px solid rgba(var(--acc-rgb),.3);background:rgba(var(--acc-rgb),.07);cursor:pointer}
.ci.on{border-color:var(--g);background:rgba(var(--g-rgb),.1)}
.ci .t{flex:1;min-width:0}.ci .t b{display:block;font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ci .t small{color:var(--sub);font-size:11px}
.ci button{background:none;border:0;color:var(--sub);font-size:15px;padding:4px}
.dfoot{color:var(--sub);font-size:11px;text-align:center;margin-top:10px}
#chip{position:relative;z-index:3;margin:0 4px 8px;padding:8px 12px;background:rgba(14,10,40,.94);border:1.5px solid rgba(var(--g-rgb),.55);border-radius:14px;font-size:13px;display:flex;justify-content:space-between;gap:8px;align-items:center;flex:none}
#chip button{background:none;border:0;color:var(--red);font-size:16px}
footer{position:relative;z-index:5;flex:none;display:flex;align-items:center;gap:12px;padding:6px 2px calc(env(safe-area-inset-bottom,0px) + 14px)}
.dbtn{width:40px;height:40px;background:rgba(14,10,40,.94);border:2px solid var(--acc);border-radius:9px;transform:rotate(45deg);display:flex;justify-content:center;align-items:center;flex:none;box-shadow:0 0 12px rgba(var(--acc-rgb),.5);transition:.2s;margin:0 4px}
.dbtn:hover{background:rgba(var(--acc-rgb),.25)}
.dbtn svg{transform:rotate(-45deg);width:18px;height:18px;stroke:var(--acc);filter:drop-shadow(0 0 4px var(--acc))}
#send{border-color:var(--g);box-shadow:0 0 14px rgba(var(--g-rgb),.55);background:rgba(var(--g-rgb),.12)}
#send svg{stroke:var(--g);filter:drop-shadow(0 0 4px var(--g))}
#cancel{border-color:var(--red);box-shadow:0 0 14px rgba(255,93,108,.55);background:rgba(255,93,108,.12)}
#cancel svg{stroke:var(--red);filter:drop-shadow(0 0 4px var(--red))}
.pill{flex:1;min-width:0;min-height:46px;background:rgba(14,10,40,.92);border:1.5px solid rgba(var(--acc-rgb),.55);border-radius:26px;display:flex;align-items:center;padding:0 12px;box-shadow:inset 0 0 10px rgba(0,0,0,.7),0 0 10px rgba(var(--acc-rgb),.14)}
.pill:focus-within{border-color:var(--g);box-shadow:0 0 14px rgba(var(--g-rgb),.4)}
textarea{flex:1;min-width:0;background:transparent;border:0;outline:0;color:var(--txt);font-size:15px;text-align:right;resize:none;max-height:120px;line-height:1.5;padding:11px 0;font-family:inherit;overflow-y:hidden;scrollbar-width:none}
textarea::-webkit-scrollbar{display:none}
textarea::placeholder{color:rgba(238,240,255,.42)}
.ups{display:flex;align-items:center;gap:8px;margin-right:6px}
.ib{background:transparent;border:0;display:flex;align-items:center;justify-content:center;padding:3px;transition:transform .2s}
.ib:hover{transform:scale(1.15)}
.ib svg{width:20px;height:20px;stroke:var(--g);filter:drop-shadow(0 0 4px rgba(var(--g-rgb),.6))}
.overlay{position:fixed;inset:0;background:var(--bg1);display:flex;align-items:center;justify-content:center;z-index:60;padding:20px;background-image:radial-gradient(circle at 50% 28%,rgba(var(--acc-rgb),.22),transparent 58%)}
.box{width:100%;max-width:340px;background:rgba(14,10,40,.88);border:1.5px solid var(--acc);border-radius:24px;padding:24px;text-align:center;box-shadow:0 0 34px rgba(var(--acc-rgb),.35)}
.box .botwrap{margin:0 auto 8px}
.box h2{color:var(--g);margin-bottom:4px;text-shadow:0 0 10px rgba(var(--g-rgb),.6)}
.box p{color:var(--sub);font-size:13px;margin-bottom:14px}
.box input{width:100%;padding:12px;border-radius:14px;border:1.5px solid rgba(var(--acc-rgb),.55);background:rgba(5,4,20,.9);color:var(--txt);font-size:16px;text-align:center;outline:0}
.box input:focus{border-color:var(--g)}
.box button{width:100%;margin-top:10px;padding:12px;border-radius:14px;border:0;background:var(--g);color:#031a10;font-weight:800;font-size:16px;box-shadow:0 0 16px rgba(var(--g-rgb),.5)}
.social{position:absolute;left:0;right:0;bottom:calc(env(safe-area-inset-bottom,0px) + 20px);display:flex;flex-direction:column;align-items:center;gap:10px}
.soc-title{display:flex;align-items:center;gap:10px;color:var(--sub);font-size:12px;letter-spacing:.5px}
.soc-title i{display:block;width:44px;height:1px;background:linear-gradient(90deg,transparent,rgba(var(--acc-rgb),.8),transparent)}
.ig{display:inline-flex;align-items:center;gap:10px;padding:10px 20px;border-radius:32px;color:#fff;text-decoration:none;font-weight:700;border:2px solid transparent;
 background:linear-gradient(#0c0922,#0c0922) padding-box,linear-gradient(135deg,#feda75,#fa7e1e,#d62976,#962fbf,#4f5bd5) border-box;
 box-shadow:0 0 22px rgba(214,41,118,.42);animation:igp 3.2s ease-in-out infinite;transition:transform .2s}
.ig:active{transform:scale(.96)}
.ig svg{color:#ff77b7}
.ig .igh{direction:ltr;font-size:15px}
.ig em{font-style:normal;font-size:12px;color:#ffc2e0;border-right:1px solid rgba(255,255,255,.25);padding-right:10px}
@keyframes igp{0%,100%{box-shadow:0 0 16px rgba(214,41,118,.35)}50%{box-shadow:0 0 30px rgba(250,126,30,.55)}}
@media(max-height:640px){.social{position:static;margin-top:16px}.overlay{flex-direction:column}}
#lerr{color:var(--red);min-height:18px;margin-top:8px;font-size:13px}
#toast{position:fixed;bottom:110px;left:50%;transform:translateX(-50%);background:rgba(9,7,28,.96);border:1px solid var(--g);color:var(--g);padding:8px 14px;border-radius:20px;font-size:13px;z-index:70;display:none;max-width:90%;text-align:center}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <linearGradient id="hd" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#262a5c"/><stop offset="1" stop-color="#0f1030"/></linearGradient>
  <linearGradient id="vz" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#03040f"/><stop offset="1" stop-color="#0a0c25"/></linearGradient>
</defs></svg>

<template id="botTpl">
<svg class="robot idle" viewBox="0 0 120 120" aria-hidden="true">
 <g class="float">
  <line x1="60" y1="20" x2="60" y2="31" stroke="var(--acc)" stroke-width="3" stroke-linecap="round"/>
  <circle class="orb" cx="60" cy="15" r="5.5" fill="var(--g)"/>
  <rect x="20" y="31" width="80" height="62" rx="24" fill="url(#hd)" stroke="var(--g)" stroke-width="2.5"/>
  <rect x="29" y="41" width="62" height="40" rx="16" fill="url(#vz)" stroke="var(--acc)" stroke-width="1.6"/>
  <g id="eyes">
   <g class="eye"><rect x="40" y="50" width="14" height="17" rx="7" fill="var(--g)"/></g>
   <g class="eye"><rect x="66" y="50" width="14" height="17" rx="7" fill="var(--g)"/></g>
  </g>
  <path class="m m-smile" d="M50 72 Q60 79 70 72" stroke="var(--g)" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path class="m m-flat" d="M52 74 L68 74" stroke="var(--acc)" stroke-width="3" fill="none" stroke-linecap="round"/>
  <ellipse class="m m-open" cx="60" cy="74" rx="8" ry="5.5" fill="var(--g)"/>
  <path class="m m-sad" d="M50 77 Q60 70 70 77" stroke="var(--red)" stroke-width="3" fill="none" stroke-linecap="round"/>
  <rect x="50" y="93" width="20" height="7" rx="3" fill="#14163a" stroke="var(--acc)" stroke-width="1.5"/>
  <path d="M28 114 Q28 101 60 101 Q92 101 92 114" fill="#14163a" stroke="var(--g)" stroke-width="2"/>
 </g>
</svg>
</template>

<div id="app">
  <div class="grid3d"></div>
  <div class="top">
    <button class="mini" id="histBtn" aria-label="المحادثات السابقة">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="14" y2="17"/></svg>
    </button>
    <button class="mini" id="menuBtn" aria-label="القائمة">⋮</button>
    <div class="botwrap" id="botBox"></div>
    <div id="say"></div>
    <div id="mname" title="تبديل الموديل">Gemini</div>
  </div>

  <div id="menu" hidden>
    <button id="mNew">✨ محادثة جديدة</button>
    <button id="mFile">📎 حذف الملف المرفق</button>
    <a href="https://www.instagram.com/haydar_xe/" target="_blank" rel="noopener noreferrer">📸 تواصل · إنستغرام @haydar_xe</a>
    <button id="mOut">🚪 تسجيل خروج</button>
    <div class="themes" id="themes">
      <i data-t="violet" style="background:linear-gradient(135deg,#8b5cf6,#00ff9d);color:#8b5cf6" title="بنفسجي"></i>
      <i data-t="blue" style="background:linear-gradient(135deg,#38bdf8,#00ff9d);color:#38bdf8" title="أزرق"></i>
      <i data-t="orange" style="background:linear-gradient(135deg,#fb923c,#00ff9d);color:#fb923c" title="برتقالي"></i>
      <i data-t="green" style="background:linear-gradient(135deg,#10b981,#00ff9d);color:#10b981" title="أخضر"></i>
    </div>
  </div>

  <main id="msgs"></main>
  <div class="floating off" id="floating"></div>
  <div id="sheet" hidden></div>
  <div id="chip" hidden><span id="chipName"></span><button id="chipX" aria-label="حذف المرفق">✕</button></div>

  <footer>
    <button class="dbtn" id="plus" aria-label="اختيار موديل"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>
    <div class="pill">
      <textarea id="inp" rows="1" placeholder="اكتب رسالة..."></textarea>
      <div class="ups">
        <button class="ib" id="imgBtn" aria-label="رفع صورة"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg></button>
        <button class="ib" id="docBtn" aria-label="رفع ملف"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></button>
      </div>
      <input type="file" id="imgFile" hidden accept="image/*">
      <input type="file" id="docFile" hidden accept=".pdf,.docx,.txt,.md,.csv,.json,.py,.js,.ts,.html,.css,.xml,.log,.yaml,.yml,.lua,.java,.c,.cpp,.cs,.sql">
    </div>
    <button class="dbtn" id="send" aria-label="إرسال"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
    <button class="dbtn" id="cancel" style="display:none" aria-label="إلغاء"><svg viewBox="0 0 24 24" fill="none" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
  </footer>

  <div id="scrim" hidden></div>
  <aside class="drawer" id="drawer">
    <div class="dh"><b>المحادثات</b><button id="dClose">✕</button></div>
    <button class="dnew" id="dNew">＋ محادثة جديدة</button>
    <div id="dList"></div>
    <div class="dfoot">محفوظة على هالجهاز (آخر 25 محادثة)</div>
  </aside>
</div>

<div class="overlay" id="login" hidden>
  <div class="box">
    <div class="botwrap" id="loginBot"></div>
    <h2>__APP_NAME__</h2>
    <p>موقع خاص، دخّل كلمة السر</p>
    <input type="password" id="pw" placeholder="كلمة السر" autocomplete="current-password">
    <button id="go">دخول</button>
    <div id="lerr"></div>
  </div>
  <div class="social">
    <div class="soc-title"><i></i><span>تواصل</span><i></i></div>
    <a class="ig" href="https://www.instagram.com/haydar_xe/" target="_blank" rel="noopener noreferrer">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="5.5"/><circle cx="12" cy="12" r="4.2"/><circle cx="17.3" cy="6.7" r="1.1" fill="currentColor" stroke="none"/></svg><span class="igh">@haydar_xe</span><em>تابعني ←</em>
    </a>
  </div>
</div>
<div id="lb" hidden><img id="lbImg" alt=""></div>
<div id="toast"></div>

<script>
const $=s=>document.querySelector(s);
const msgs=$('#msgs'),inp=$('#inp'),sendBtn=$('#send'),botEl_tpl=$('#botTpl');
let busy=false,models=[],current='gemini',codeAuto=false,hasGroq=false,abortCtrl=null;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(t){const el=$('#toast');el.textContent=t;el.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>el.style.display='none',2600)}
function label(id){const o=models.find(x=>x.id===id);return o?o.label:(id==='gemini'?'Gemini':id==='ryn_mix'?'Ryn Mix ⚡':id.replace('or:','').replace(':free',''))}

function applyTheme(t){document.documentElement.dataset.theme=t;try{localStorage.setItem('ryn_theme',t)}catch(e){}
  document.querySelectorAll('#themes i').forEach(i=>i.classList.toggle('on',i.dataset.t===t))}
applyTheme((()=>{try{return localStorage.getItem('ryn_theme')||'violet'}catch(e){return 'violet'}})());
$('#themes').addEventListener('click',e=>{const i=e.target.closest('i');if(i){e.stopPropagation();applyTheme(i.dataset.t)}});

$('#botBox').appendChild(botEl_tpl.content.cloneNode(true));
$('#loginBot').appendChild(botEl_tpl.content.cloneNode(true));
const bot=$('#botBox .robot');
let botT=null,sayT=null;
function setBot(state,ms){clearTimeout(botT);bot.setAttribute('class','robot '+state);if(ms)botT=setTimeout(()=>setBot('idle'),ms)}
function say(t){clearInterval(sayT);const el=$('#say');el.textContent='';let i=0;
  sayT=setInterval(()=>{el.textContent=t.slice(0,++i);if(i>=t.length)clearInterval(sayT)},26)}
const IDLE=['أهلاً! اسألني أي شي 🤖','جاهز للمساعدة 💚','ارفع صورة وبشوفها 👀','اطلب كود وبكتبه 💻','جرّب Ryn Mix ⚡','عندك ملف؟ ارفعه 📎'];
let idleN=0;
setInterval(()=>{if(!busy&&!inp.value&&bot.classList.contains('idle')&&$('#login').hidden){say(IDLE[idleN++%IDLE.length])}},14000);
document.addEventListener('pointermove',e=>{
  const r=bot.getBoundingClientRect();const cx=r.left+r.width/2,cy=r.top+r.height/2;
  const dx=Math.max(-1,Math.min(1,(e.clientX-cx)/220)),dy=Math.max(-1,Math.min(1,(e.clientY-cy)/220));
  bot.style.setProperty('--ex',(dx*4.5)+'px');bot.style.setProperty('--ey',(dy*3.5)+'px');
});

const LS='ryn_store_v2';
let store={cur:null,chats:[]};
const uid=()=>Date.now().toString(36)+Math.random().toString(36).slice(2,6);
function titleOf(ms){const u=ms.find(m=>m.role==='user');return u?u.text.replace(/\s+/g,' ').trim().slice(0,34):'محادثة جديدة'}
function loadStore(){try{const s=JSON.parse(localStorage.getItem(LS)||'null');if(s&&Array.isArray(s.chats))store=s}catch(e){}
  if(!store.chats.find(c=>c.id===store.cur))store.cur=store.chats.length?store.chats[0].id:null;
  if(!store.cur)newChat(true)}
const curChat=()=>store.chats.find(c=>c.id===store.cur);
const M=()=>curChat().msgs;
function saveStore(){let chats=store.chats.slice(0,25).map(c=>({id:c.id,title:c.title,ts:c.ts,
  msgs:c.msgs.slice(-80).map(m=>({role:m.role,text:String(m.text).slice(0,8000),note:m.note,err:m.err,img:(m.img&&m.img.length<150000)?m.img:undefined}))}));
  for(;;){try{localStorage.setItem(LS,JSON.stringify({cur:store.cur,chats}));return}catch(e){if(chats.length<=2)return;chats.pop()}}}
function touch(){const c=curChat();c.ts=Date.now();c.title=titleOf(c.msgs);store.chats=[c].concat(store.chats.filter(x=>x!==c))}
function newChat(silent){const c=curChat&&store.cur?curChat():null;
  if(c&&!c.msgs.length&&!silent){closeDrawer();return}
  const n={id:uid(),title:'محادثة جديدة',ts:Date.now(),msgs:[]};
  store.chats.unshift(n);store.cur=n.id;
  if(!silent){saveStore();render();drawDrawer();closeDrawer();clearFile();say('محادثة جديدة، تفضّل 🙂')}}
function switchChat(id){store.cur=id;saveStore();render();drawDrawer();closeDrawer();clearFile()}
function delChat(id){store.chats=store.chats.filter(c=>c.id!==id);if(!store.chats.length)newChat(true);
  if(!store.chats.find(c=>c.id===store.cur))store.cur=store.chats[0].id;saveStore();render();drawDrawer()}
function ago(ts){const s=(Date.now()-ts)/1000;if(s<60)return 'الآن';if(s<3600)return 'قبل '+Math.floor(s/60)+' د';if(s<86400)return 'قبل '+Math.floor(s/3600)+' س';return 'قبل '+Math.floor(s/86400)+' يوم'}
function drawDrawer(){$('#dList').innerHTML=store.chats.map(c=>'<div class="ci'+(c.id===store.cur?' on':'')+'" data-id="'+c.id+'"><div class="t"><b>'+esc(c.title)+'</b><small>'+ago(c.ts)+' · '+c.msgs.filter(m=>m.role!=='sys').length+' رسالة</small></div><button data-del="'+c.id+'">🗑️</button></div>').join('')}
function openDrawer(){drawDrawer();$('#scrim').hidden=false;$('#drawer').classList.add('on')}
function closeDrawer(){$('#drawer').classList.remove('on');$('#scrim').hidden=true}
$('#lb').onclick=()=>{$('#lb').hidden=true};
$('#histBtn').onclick=e=>{e.stopPropagation();openDrawer()};
$('#dClose').onclick=closeDrawer;$('#scrim').onclick=closeDrawer;
$('#dNew').onclick=()=>newChat(false);
$('#dList').addEventListener('click',e=>{const d=e.target.closest('[data-del]');if(d){e.stopPropagation();delChat(d.dataset.del);return}
  const c=e.target.closest('.ci');if(c)switchChat(c.dataset.id)});

function md(text){
  const blocks=[];
  let t=String(text).replace(/```([\w+#.-]*)\n?([\s\S]*?)```/g,(m,l,c)=>{blocks.push({l,c});return '\u0000'+(blocks.length-1)+'\u0000'});
  t=esc(t).replace(/`([^`\n]+)`/g,'<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g,'<b>$1</b>');
  let html='',list=[];
  const flush=()=>{if(list.length){html+='<ul>'+list.map(x=>'<li>'+x+'</li>').join('')+'</ul>';list=[]}};
  for(const ln of t.split('\n')){
    const hm=ln.match(/^\s*#{1,6}\s+(.*)/);
    if(hm){flush();html+='<div class="h">'+hm[1]+'</div>';continue}
    const m=ln.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)/);
    if(m){list.push(m[1])}else{flush();html+=ln+'<br>'}
  }
  flush();
  html=html.replace(/(https?:\/\/image\.pollinations\.ai\/[^\s<>"'`]+)/gi,'<img class="pic" src="$1" alt="صورة مولّدة">');
  return html.replace(/<br>$/,'').replace(/\u0000(\d+)\u0000/g,(m,i)=>{
    const b=blocks[+i];
    return '<div class="code"><div class="ch"><span>'+esc(b.l||'code')+'</span><button class="cp">نسخ</button></div><pre><code>'+esc(b.c.replace(/\n$/,''))+'</code></pre></div>';
  });
}
const pm=t=>md((t.match(/```/g)||[]).length%2?t+'\n```':t);
let reveal=null;
function render(){
  const ms=M();
  if(!ms.length){msgs.innerHTML='<div class="hint">أهلاً فيك في Ryn AI 👋<br><b>اسأل أي سؤال</b>، ارفع صورة 📷 أو ملف 📎،<br>أو جرّب Ryn Mix ⚡ للصور والكود.</div>';return}
  msgs.innerHTML=ms.map((m,i)=>{
    if(m.role==='sys')return '<div class="sys">'+esc(m.text)+'</div>';
    const me=m.role==='user';
    const pic=(m.img&&String(m.img).startsWith('data:image/jpeg;base64,'))?'<img class="pic" data-i="'+i+'" src="'+m.img+'" alt="صورة">':'';
    const body=pic+(m.reveal?'':md(m.text));
    return '<div class="bubble '+(me?'me':'bot')+(m.err?' err':'')+'"'+(m.reveal?' data-rev="1"':'')+'>'+body+'</div>'+
      (m.note?'<div class="note">'+esc(m.note)+'</div>':'')+
      (me?'':'<div class="acts"><button data-copy="'+i+'">نسخ الرد</button></div>');
  }).join('')+(busy?'<div class="bubble bot"><span class="typing"><i></i><i></i><i></i></span></div>':'');
  msgs.scrollTop=msgs.scrollHeight;
  const rv=ms.findIndex(m=>m.reveal);if(rv>=0)startReveal(rv);
}
function startReveal(idx){
  const m=M()[idx],el=msgs.querySelector('[data-rev="1"]');
  if(!el){m.reveal=false;return}
  const toks=m.text.split(/(\s+)/),step=Math.max(1,Math.ceil(toks.length/90));
  let n=0,skip=false;el.onclick=()=>{skip=true};
  clearInterval(reveal);setBot('speaking');
  reveal=setInterval(()=>{
    n+=step;if(skip)n=toks.length;
    el.innerHTML=pm(toks.slice(0,n).join(''));
    msgs.scrollTop=msgs.scrollHeight;
    if(n>=toks.length){clearInterval(reveal);reveal=null;m.reveal=false;el.removeAttribute('data-rev');el.innerHTML=md(m.text);
      setBot('happy',1400);say(['تفضّل! 🙂','هاد اللي لقيته 💚','تم ✅'][Math.floor(Math.random()*3)]);
      msgs.scrollTop=msgs.scrollHeight;saveStore()}
  },30);
}
msgs.addEventListener('click',e=>{
  const pic=e.target.closest('.pic');
  if(pic){$('#lbImg').src=pic.src;$('#lb').hidden=false;return}
  const cp=e.target.closest('.cp');
  if(cp){copy(cp.closest('.code').querySelector('code').innerText);return}
  const c=e.target.closest('[data-copy]');
  if(c){copy(M()[+c.dataset.copy].text)}
});
async function copy(t){try{await navigator.clipboard.writeText(t)}catch(e){
  const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove()}
  toast('اننسخ ✅')}

async function api(path,opt={}){
  opt.headers=Object.assign({'X-Requested-With':'astra'},opt.headers||{});
  if(opt.body&&!(opt.body instanceof FormData))opt.headers['Content-Type']='application/json';
  opt.credentials='same-origin';
  const r=await fetch(path,opt);
  if(r.status===401){showLogin();throw new Error('auth')}
  return r;
}

async function send(){
  const text=inp.value.trim();
  if(!text||busy)return;
  inp.value='';grow();
  abortCtrl=new AbortController();
  const sig=abortCtrl.signal;
  const hist=M().filter(m=>m.role!=='sys').slice(-14).map(m=>({role:m.role,text:m.text}));
  const um={role:'user',text};if(pendingThumb){um.img=pendingThumb;pendingThumb=null}
  M().push(um);touch();busy=true;
  sendBtn.style.display='none';
  $('#cancel').style.display='flex';
  setBot('thinking');
  say(/(python|javascript|html|css|sql|كود|برمج|code)/i.test(text)?'عم أكتب الكود 💻':($('#chip').hidden?'عم أفكّر...':'عم أقرا المرفق 📎'));
  render();saveStore();
  let ok=true, aborted=false;
  try{
    const r=await api('/api/chat',{method:'POST',body:JSON.stringify({message:text,history:hist}),signal:sig});
    const d=await r.json();
    if(!r.ok){M().push({role:'model',text:d.error||'صار خطأ',err:true});ok=false}
    else{
      let done=false;
      for(let i=0;i<240&&!done;i++){
        await sleep(i<4?1000:2000);
        if(sig.aborted){aborted=true;break}
        const j=await (await api('/api/job/'+d.job,{signal:sig})).json();
        if(j.status==='done'){
          const full=j.text||'(رد فاضي)';
          M().push({role:'model',text:full,note:j.note,reveal:full.length<6000});done=true}
        else if(j.status!=='running'){M().push({role:'model',text:j.text||'صار خطأ',err:true});ok=false;done=true}
      }
      if(!done && !aborted){M().push({role:'model',text:'⏳ تأخر الرد كتير، جرّب تاني.',err:true});ok=false}
    }
  }catch(e){
    if(e.name==='AbortError' || sig.aborted){aborted=true}
    else if(e.message!=='auth'){M().push({role:'model',text:'ما قدرت أوصل للسيرفر، جرّب تاني.',err:true});ok=false}
  }
  abortCtrl=null;busy=false;
  sendBtn.style.display='flex';
  $('#cancel').style.display='none';
  touch();render();saveStore();
  if(aborted){setBot('idle');toast('تم إلغاء الطلب ✅')}
  else if(!ok){setBot('error',2600);say('صار خطأ، جرّب تاني')}
  else if(!M()[M().length-1].reveal){setBot('happy',1200)}
}
$('#cancel').onclick=()=>{
  if(abortCtrl){abortCtrl.abort();abortCtrl=null}
  if(reveal){clearInterval(reveal);reveal=null}
  busy=false;
  sendBtn.style.display='flex';
  $('#cancel').style.display='none';
  setBot('idle');
  render();
};
sendBtn.onclick=send;
const fine=matchMedia('(pointer:fine)').matches;
inp.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&(fine||e.ctrlKey)){e.preventDefault();send()}});
function grow(){inp.style.height='auto';inp.style.height=Math.min(inp.scrollHeight,120)+'px';inp.style.overflowY=inp.scrollHeight>120?'auto':'hidden'}
let typT=null;
inp.addEventListener('input',()=>{grow();if(!busy){setBot('typing');clearTimeout(typT);typT=setTimeout(()=>{if(!busy)setBot('idle')},1400)}});

const standalone=matchMedia('(display-mode: standalone)').matches;
function dbg(msg){try{api('/api/clientlog',{method:'POST',body:JSON.stringify({msg:String(msg).slice(0,300)})}).catch(()=>{})}catch(e){}}
async function prepImage(f){
  try{
    const bmp=await createImageBitmap(f);
    const draw=max=>{const sc=Math.min(1,max/Math.max(bmp.width,bmp.height));const cv=document.createElement('canvas');
      cv.width=Math.max(1,Math.round(bmp.width*sc));cv.height=Math.max(1,Math.round(bmp.height*sc));
      cv.getContext('2d').drawImage(bmp,0,0,cv.width,cv.height);return cv};
    const blob=await new Promise(r=>draw(1600).toBlob(r,'image/jpeg',0.85));
    const thumb=draw(420).toDataURL('image/jpeg',0.72);
    if(blob&&blob.size)return {file:new File([blob],'photo.jpg',{type:'image/jpeg'}),thumb};
  }catch(e){}
  return {file:f,thumb:null};
}
async function toMemory(f){const buf=await f.arrayBuffer();return new File([buf],f.name||'file',{type:f.type||'application/octet-stream'})}
let pendingThumb=null;
async function upload(f,isImg){
  if(!f)return;
  toast(isImg?'عم أجهّز الصورة...':'عم أرفع الملف...');setBot('thinking');
  let thumb=null;
  try{if(isImg){const p=await prepImage(f);f=p.file;thumb=p.thumb}f=await toMemory(f)}
  catch(e){dbg('prep failed: '+e.name+' '+e.message);toast('ما قدرت أقرا الملف');setBot('error',1800);return}
  if(f.size>15*1024*1024){toast('الملف أكبر من 15 ميجا');setBot('error',1800);return}
  for(let attempt=0;attempt<2;attempt++){
    try{
      const fd=new FormData();fd.append('file',f,f.name||(isImg?'photo.jpg':'file'));
      const r=await api('/api/upload',{method:'POST',body:fd});
      let d={};try{d=await r.json()}catch(e){}
      if(!r.ok){toast(d.error||('فشل الرفع ('+r.status+')'));setBot('error',1800);
        dbg('upload http '+r.status+' '+(d.error||'')+' size='+f.size);return}
      showChip(d.name,!!d.image);pendingThumb=d.image?thumb:null;
      if(!inp.value.trim()){inp.value=d.image?'شو بالصورة؟ اشرحلي إياها.':'لخّصلي هالملف.';grow()}
      inp.focus();setBot('happy',1200);say(d.image?'شفت الصورة 👀':'استلمت الملف 📎');return;
    }catch(e){
      if(e.message==='auth')return;
      dbg('upload err try'+attempt+': '+e.name+' '+e.message+' size='+f.size);
      if(attempt===1){toast('فشل الرفع: '+e.name);setBot('error',1800)}else await sleep(700);
    }
  }
}
$('#imgBtn').onclick=()=>$('#imgFile').click();
$('#docBtn').onclick=()=>$('#docFile').click();
$('#imgFile').onchange=e=>{const f=e.target.files[0];e.target.value='';upload(f,true)};
$('#docFile').onchange=e=>{const f=e.target.files[0];e.target.value='';upload(f,false)};
inp.addEventListener('paste',e=>{const it=[...(e.clipboardData&&e.clipboardData.items||[])].find(x=>x.type.startsWith('image/'));
  if(it){e.preventDefault();upload(it.getAsFile(),true)}});
function showChip(name,img){$('#chipName').textContent=img?'🖼️ صورة مرفقة (10 دقايق)':('📎 '+name+' (30 دقيقة)');$('#chip').hidden=false}
function hideChip(){$('#chip').hidden=true;pendingThumb=null}
async function clearFile(){try{await api('/api/clearfile',{method:'POST'})}catch(e){}hideChip()}
$('#chipX').onclick=clearFile;

$('#menuBtn').onclick=e=>{e.stopPropagation();$('#menu').hidden=!$('#menu').hidden};
document.addEventListener('click',e=>{if(!e.target.closest('#menu'))$('#menu').hidden=true;
  if(!e.target.closest('#sheet,#floating,#plus,#mname'))$('#sheet').hidden=true});
$('#mNew').onclick=()=>{$('#menu').hidden=true;newChat(false)};
$('#mFile').onclick=clearFile;
$('#mOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'})}catch(e){}showLogin()};
$('#plus').onclick=e=>{e.stopPropagation();$('#sheet').hidden=true;$('#floating').classList.toggle('off')};
$('#mname').onclick=e=>{e.stopPropagation();$('#floating').classList.add('off');openSheet()};

function drawFloating(){
  const auto=models.find(o=>o.id==='or:openrouter/free');
  let h='<button class="dia'+(current==='gemini'?' on':'')+'" data-m="gemini"><span>Gemini</span></button>';
  h+='<button class="dia'+(current==='ryn_mix'?' on':'')+'" data-m="ryn_mix"><span>Ryn<br>Mix⚡</span></button>';
  if(auto)h+='<button class="dia'+(current===auto.id?' on':'')+'" data-m="'+esc(auto.id)+'"><span>تلقائي<br>✨</span></button>';
  if(models.length>1)h+='<button class="dia" data-m="__all"><span>كل<br>الموديلات</span></button>';
  if(hasGroq)h+='<button class="dia'+(codeAuto?' on':'')+'" data-m="__code"><span>كود<br>'+(codeAuto?'ON':'OFF')+'</span></button>';
  $('#floating').innerHTML=h;
}
$('#floating').addEventListener('click',async e=>{
  const b=e.target.closest('.dia');if(!b)return;e.stopPropagation();
  const m=b.dataset.m;
  if(m==='__all'){$('#floating').classList.add('off');openSheet();return}
  if(m==='__code'){try{await api('/api/code',{method:'POST',body:JSON.stringify({on:!codeAuto})});codeAuto=!codeAuto;drawFloating();toast('توجيه الكود: '+(codeAuto?'شغّال':'مطفّي'))}catch(x){}return}
  $('#floating').classList.add('off');setModel(m);
});
function openSheet(){
  $('#sheet').innerHTML=models.map(o=>'<button class="'+(o.id===current?'on':'')+'" data-m="'+esc(o.id)+'"><b>'+esc(o.label)+'</b>'+(o.desc?'<small>'+esc(o.desc)+'</small>':'')+'</button>').join('');
  $('#sheet').hidden=false;
}
$('#sheet').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;e.stopPropagation();$('#sheet').hidden=true;setModel(b.dataset.m)});
async function setModel(id){
  try{
    const r=await api('/api/model',{method:'POST',body:JSON.stringify({id})});const d=await r.json();
    if(!r.ok){toast(d.error||'ما انبدّل');loadModels();return}
    current=id;$('#mname').textContent=label(id);drawFloating();
    M().push({role:'sys',text:'تم تفعيل: '+label(id)+' ✅'});render();saveStore();
    setBot('happy',1200);say('تمام! صرت '+label(id));
  }catch(x){}
}
async function loadModels(){
  try{
    const d=await (await api('/api/models')).json();
    models=d.options;current=d.current;hasGroq=!!d.groq;codeAuto=!!d.code_auto;
    $('#mname').textContent=label(current);drawFloating();
    if(d.file)showChip(d.file,!!d.file_image);
  }catch(e){}
}

function showLogin(){$('#login').hidden=false;setTimeout(()=>$('#pw').focus(),50)}
async function login(){
  $('#lerr').textContent='';
  try{
    const r=await fetch('/api/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Requested-With':'astra'},body:JSON.stringify({password:$('#pw').value})});
    const d=await r.json();
    if(r.ok){$('#pw').value='';$('#login').hidden=true;loadModels();render();say('أهلاً فيك في Ryn AI! 👋')}
    else{$('#lerr').textContent=d.error||'غلط'}
  }catch(e){$('#lerr').textContent='ما في اتصال'}
}
$('#go').onclick=login;
$('#pw').addEventListener('keydown',e=>{if(e.key==='Enter')login()});

(async()=>{
  loadStore();render();drawDrawer();
  try{const d=await (await fetch('/api/me',{credentials:'same-origin'})).json();
    if(d.ok){loadModels();say('أهلاً فيك في Ryn AI! 👋 كيف بقدر ساعدك؟')}else{showLogin()}}catch(e){showLogin()}
  if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
})();
</script>
</body>
</html>
'''


def describe(mid):
    m = mid.replace("or:", "").replace(":free", "").lower()
    if mid == "gemini":
        return "الأساسي: دردشة عامة، صور، ملفات، PDF."
    if mid == "ryn_mix":
        return "DeepSeek + Qwen + Kimi للنصوص، وFLUX للصور — كله مجاني."
    if m == "openrouter/free":
        return "بيختار لحاله موديل متاح. مناسب للأسئلة العادية."
    if any(k in m for k in ("safety", "guard", "moderation")):
        return "لفحص أمان المحتوى، مو للمحادثة."
    if "nemotron" in m:
        if "ultra" in m: return "موديل تفكير كبير من NVIDIA لسياق طويل."
        if "reason" in m or "super" in m: return "من NVIDIA، موجّه للتفكير."
        if "lightning" in m: return "نسخة سريعة من Nemotron."
        return "موديل من NVIDIA للمحادثة."
    if "gemma" in m: return "موديل مفتوح من Google، خفيف."
    if "deepseek" in m: return "قوي بالتفكير والبرمجة."
    if "qwen" in m: return "متعدد اللغات، جيد للنصوص والكود."
    if "gpt-oss" in m: return "موديل OpenAI المفتوح."
    if "llama" in m: return "موديل Meta المفتوح."
    if any(k in m for k in ("flash", "mini", "lite", "nano", "small", "xs")):
        return "خفيف وسريع للمهام البسيطة."
    return "ما عندي معلومات مؤكدة. جرّبه."


def register(app, b):
    password = os.environ.get("WEB_PASSWORD", "")
    app.secret_key = os.environ.get("WEB_SECRET") or hashlib.sha256(
        ("ryn-web|" + password + "|" + str(getattr(b, "BOT_TOKEN", ""))).encode()).hexdigest()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("WEB_SECURE_COOKIE", "1") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        MAX_CONTENT_LENGTH=MAX_UPLOAD + 1024 * 1024,
    )
    jobs, jlock = {}, threading.Lock()
    fails = {}

    # ========== مفتاح الجلسة الفريد ==========
    def sid():
        """معرّف فريد لكل جلسة متصفح (cookie). لا يشارك أي حالة مع جلسة تانية."""
        s = session.get("sid")
        if not s:
            s = "web_" + uuid.uuid4().hex[:16]
            session["sid"] = s
            session.permanent = True
        return s

    def client_ip():
        return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "?")

    def authed():
        return bool(session.get("ok"))

    def guard():
        if not authed():
            return jsonify(error="سجّل دخول"), 401
        if request.method == "POST" and request.headers.get("X-Requested-With") != "astra":
            return jsonify(error="طلب مرفوض"), 403
        return None

    @app.after_request
    def headers(resp):
        p = request.path
        if p == "/" or p.startswith("/api") or p in ("/manifest.webmanifest", "/sw.js", "/icon.svg"):
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["Referrer-Policy"] = "no-referrer"
            resp.headers["X-Frame-Options"] = "DENY"
            if p == "/":
                resp.headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: https://image.pollinations.ai; connect-src 'self'; base-uri 'none'; form-action 'self'")
        return resp

    # ========== صفحات ثابتة ==========
    @app.get("/")
    def web_index():
        return Response(INDEX_HTML.replace("__APP_NAME__", APP_NAME), mimetype="text/html")

    @app.get("/manifest.webmanifest")
    def web_manifest():
        return Response(json.dumps(MANIFEST, ensure_ascii=False), mimetype="application/manifest+json")

    @app.get("/sw.js")
    def web_sw():
        r = Response(SW_JS, mimetype="application/javascript")
        r.headers["Service-Worker-Allowed"] = "/"
        return r

    @app.get("/icon.svg")
    def web_icon():
        return Response(ICON_SVG, mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    def sniff_image(data):
        if data[:3] == b"\xff\xd8\xff": return "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
        return None

    @app.errorhandler(413)
    def too_big(_e):
        return jsonify(error="الملف كبير (الحد 15 ميجا)"), 413

    # ========== دخول ==========
    @app.get("/api/me")
    def api_me():
        return jsonify(ok=authed(), configured=bool(password))

    @app.post("/api/login")
    def api_login():
        if not password:
            return jsonify(error="WEB_PASSWORD مو مضاف"), 503
        if request.headers.get("X-Requested-With") != "astra":
            return jsonify(error="طلب مرفوض"), 403
        ip, now = client_ip(), time.time()
        recent = [t for t in fails.get(ip, []) if now - t < 600]
        if len(recent) >= 5:
            return jsonify(error="محاولات كتير، جرّب بعد 10 دقايق"), 429
        given = str((request.get_json(silent=True) or {}).get("password", ""))
        if hmac.compare_digest(given.encode(), password.encode()):
            fails.pop(ip, None)
            session.clear()
            session["ok"] = True
            session.permanent = True
            # ملاحظة: session.clear() بيولّد sid جديد تلقائياً عند أول طلب
            return jsonify(ok=True)
        recent.append(now)
        fails[ip] = recent
        time.sleep(0.8)
        return jsonify(error="كلمة السر غلط"), 401

    @app.post("/api/logout")
    def api_logout():
        session.clear()
        return jsonify(ok=True)

    # ========== الدردشة ==========
    def clean_history(raw):
        out = []
        for m in (raw if isinstance(raw, list) else [])[-20:]:
            if not isinstance(m, dict):
                continue
            role = "model" if m.get("role") == "model" else "user"
            text = str(m.get("text", ""))[:6000]
            if text.strip():
                out.append((role, text))
        while out and out[0][0] != "user":
            out.pop(0)
        return out

    def run_job(job_id, sid_val, text, history):
        """مهمة الخادم: بتشتغل بـ sid_val (مو WEB_ID المشترك)."""
        try:
            T = b.types
            contents = [T.Content(role=r, parts=[T.Part(text=t)]) for r, t in history]
            ctx = b.get_doc(sid_val)                       # ← ملفات الجلسة فقط
            if ctx:
                base = ctx["alt"] if ctx.get("alt") else ctx["parts"]
                parts = list(base) + [T.Part(text=text)]
            else:
                parts = [T.Part(text=text)]
            contents.append(T.Content(role="user", parts=parts))
            deep = b.is_deep_question(text)
            answer, note = b.ask_model(sid_val, contents, deep=deep, code=b.is_code_request(text))
            if deep and answer and len(answer) > 150 and b.load_knowledge() and not note:
                answer = b.review_answer(sid_val, text, answer)
            res = {"status": "done", "text": answer or "", "note": note or ""}
        except Exception as e:
            log.exception("web chat error")
            res = {"status": "error", "text": b.friendly_error(e)}
        with jlock:
            if job_id in jobs:
                jobs[job_id].update(res)

    @app.post("/api/chat")
    def api_chat():
        bad = guard()
        if bad:
            return bad
        data = request.get_json(silent=True) or {}
        text = str(data.get("message", "")).strip()
        if not text:
            return jsonify(error="الرسالة فاضية"), 400
        now = time.time()
        sid_val = sid()   # ← نثبّت sid قبل ما نطلق الثريد
        with jlock:
            for k in [k for k, v in jobs.items() if now - v["ts"] > 900]:
                jobs.pop(k, None)
            if sum(1 for v in jobs.values() if v["status"] == "running" and v.get("sid") == sid_val) >= 1:
                return jsonify(error="عندك طلب شغّال، استنى أو ألغيه"), 429
            if sum(1 for v in jobs.values() if v["status"] == "running") >= 6:
                return jsonify(error="السيرفر مضغوط، جرّب بعد لحظة"), 429
            job_id = uuid.uuid4().hex
            jobs[job_id] = {"status": "running", "ts": now, "sid": sid_val}
        threading.Thread(
            target=run_job,
            args=(job_id, sid_val, text[:12000], clean_history(data.get("history"))),
            daemon=True,
        ).start()
        return jsonify(job=job_id)

    @app.get("/api/job/<job_id>")
    def api_job(job_id):
        bad = guard()
        if bad:
            return bad
        with jlock:
            j = jobs.get(job_id)
            if not j:
                return jsonify(status="error", text="انتهت صلاحية الطلب، جرّب تاني."), 404
            if j.get("sid") != sid():
                return jsonify(status="error", text="طلب غير مصرّح"), 403
            return jsonify({k: v for k, v in j.items() if k not in ("ts", "sid")})

    # ========== الملفات ==========
    @app.post("/api/upload")
    def api_upload():
        bad = guard()
        if bad:
            return bad
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify(error="ما وصل ملف"), 400
        data = f.read()
        if len(data) > MAX_UPLOAD:
            return jsonify(error="الملف أكبر من 15 ميجا"), 413
        name = os.path.basename(f.filename)
        sid_val = sid()   # ← جلسة الزائر
        mime = sniff_image(data)
        if mime:
            parts = [b.types.Part.from_bytes(data=data, mime_type=mime)]
            b.set_doc(sid_val, name, parts, None)
            b.doc_ctx[sid_val]["image"] = True
            b.doc_ctx[sid_val]["exp"] = time.time() + 600
            return jsonify(ok=True, name=name, image=True)
        try:
            parts = b.build_doc_parts(name, data)
        except ValueError as e:
            reason = str(e)
            msg = {"unsupported": "نوع الملف مو مدعوم.",
                   "empty": "الملف فاضي."}.get(reason, "ما قدرت أفتح الملف.")
            return jsonify(error=msg), 400
        alt = b.pdf_alt_parts(name, data) if name.lower().endswith(".pdf") else None
        b.set_doc(sid_val, name, parts, alt)
        return jsonify(ok=True, name=name, image=False)

    @app.post("/api/clientlog")
    def api_clientlog():
        bad = guard()
        if bad:
            return bad
        msg = str((request.get_json(silent=True) or {}).get("msg", ""))[:300]
        log.warning("CLIENT[%s]: %s | UA=%s", sid()[:12], msg, request.headers.get("User-Agent", "")[:120])
        return jsonify(ok=True)

    @app.post("/api/clearfile")
    def api_clearfile():
        bad = guard()
        if bad:
            return bad
        b.doc_ctx.pop(sid(), None)   # ← يمسح ملف الجلسة الحالية فقط
        return jsonify(ok=True)

    # ========== الموديلات ==========
    def free_ids():
        try:
            return b.free_chat_models() if b._or_key() else []
        except Exception:
            return []

    @app.get("/api/models")
    def api_models():
        bad = guard()
        if bad:
            return bad
        opts = [
            {"id": "gemini", "label": "Gemini", "desc": describe("gemini")},
            {"id": "ryn_mix", "label": "Ryn Mix ⚡", "desc": describe("ryn_mix")},
        ]
        for i in free_ids()[:30]:
            opts.append({"id": "or:" + i,
                         "label": "تلقائي ✨" if i == "openrouter/free" else i.replace(":free", ""),
                         "desc": describe("or:" + i)})
        sid_val = sid()
        cur = b.chat_provider.get(sid_val, "gemini")   # ← موديل الجلسة
        if cur not in ("gemini", "ryn_mix") and not any(o["id"] == cur for o in opts):
            opts.append({"id": cur, "label": cur.replace("or:", "").replace(":free", ""), "desc": describe(cur)})
        ctx = b.get_doc(sid_val)                        # ← ملف الجلسة
        return jsonify(current=cur, options=opts, groq=bool(b._groq_ready()),
                       code_auto=sid_val not in b.code_route_off,
                       file=(ctx["name"] if ctx else ""),
                       file_image=bool(ctx and ctx.get("image")))

    @app.post("/api/model")
    def api_model():
        bad = guard()
        if bad:
            return bad
        mid = str((request.get_json(silent=True) or {}).get("id", ""))
        sid_val = sid()
        if mid == "gemini":
            b.chat_provider.pop(sid_val, None)
            return jsonify(ok=True)
        if mid == "ryn_mix":
            if not b._groq_ready():
                return jsonify(error="GROQ_API_KEY مو مضاف"), 400
            b.chat_provider[sid_val] = "ryn_mix"
            return jsonify(ok=True)
        if mid.startswith("or:") and b._or_key():
            if mid[3:] in free_ids():
                b.chat_provider[sid_val] = mid
                return jsonify(ok=True)
            return jsonify(error="هالموديل مو متاح"), 400
        return jsonify(error="موديل غير معروف"), 400

    @app.post("/api/code")
    def api_code():
        bad = guard()
        if bad:
            return bad
        sid_val = sid()
        if (request.get_json(silent=True) or {}).get("on"):
            b.code_route_off.discard(sid_val)
        else:
            b.code_route_off.add(sid_val)
        return jsonify(ok=True)

    log.info("Web UI enabled (password %s, per-session isolation ON)", "set" if password else "NOT set")
