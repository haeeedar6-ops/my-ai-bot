/* RYN Duplex — كشف مقاطعة المستخدم أثناء كلام رين (Phase B، ويب).
   - ميكروفون مع echoCancellation/noiseSuppression، وVAD بالطاقة (RMS) مع عتبة تكيفية.
   - ما بيحوّل صوت لنص؛ بس بيكتشف كلام مستمر ويطلق onInterrupt. التفريغ بيصير بعد المقاطعة.
   - مو بديل عن اختبار الأجهزة: الصدى على سبيكر الموبايل لازم ينقاس فعلياً. */
(function(root){
  var CFG={frameMs:30,warmupMs:700,minSpeechMs:240,ratio:4.0,minRms:0.05,absorbMs:150,floorAttack:0.02,floorRelease:0.05};
  var PROFILES={normal:{minRms:0.05,ratio:4.0},headphones:{minRms:0.025,ratio:3.0},speaker:{minRms:0.08,ratio:5.0,minSpeechMs:320}};

  /* قلب الـ VAD نقي (بدون DOM) حتى ينختبر. */
  function createVad(opts){
    var c=Object.assign({},CFG,opts||{});
    var s={t:0,floor:0.01,run:0,fired:false};
    return {
      state:s,
      step:function(rms,dt){
        dt=dt||c.frameMs; s.t+=dt;
        if(s.fired)return false;
        var thr=Math.max(c.minRms,s.floor*c.ratio);
        var hot=rms>thr;
        if(hot){s.run+=dt;}else{s.run=0;}
        if(s.run<=c.absorbMs||!hot){ /* الأرضية بتتبع الضجيج ببطء؛ الكلام الحقيقي ما بيرفعها بسرعة حتى ما يلغي نفسه */
          var a=rms>s.floor?c.floorAttack:c.floorRelease;
          s.floor+=(rms-s.floor)*a;
        }
        if(s.t<c.warmupMs)return false;
        if(s.run>=c.minSpeechMs){s.fired=true;return true;}
        return false;
      }
    };
  }

  var ctx=null,stream=null,analyser=null,timer=null,vad=null,onInt=null,profile='normal',buf=null;
  var api={
    createVad:createVad, CFG:CFG, PROFILES:PROFILES,
    supported:function(){return !!(root.navigator&&navigator.mediaDevices&&navigator.mediaDevices.getUserMedia&&(root.AudioContext||root.webkitAudioContext));},
    enabled:function(){try{var v=localStorage.getItem('ryn_duplex');if(v==='off')return false;return true;}catch(e){return true;}},
    setProfile:function(p){if(PROFILES[p]){profile=p;try{localStorage.setItem('ryn_duplex_profile',p);}catch(e){}}},
    getProfile:function(){try{return localStorage.getItem('ryn_duplex_profile')||profile;}catch(e){return profile;}},
    isOpen:function(){return !!stream;},
    /* فتح الميكروفون (مرة وحدة بالجلسة) */
    open:async function(){
      if(stream)return true;
      if(!api.supported()||!api.enabled())return false;
      try{
        stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:false}});
        var AC=root.AudioContext||root.webkitAudioContext; ctx=new AC();
        var src=ctx.createMediaStreamSource(stream); analyser=ctx.createAnalyser(); analyser.fftSize=1024;
        src.connect(analyser); buf=new Float32Array(analyser.fftSize);
        stream.getAudioTracks().forEach(function(t){t.onended=function(){api.close();};});
        return true;
      }catch(e){stream=null;return false;}
    },
    /* تسليح الكشف أثناء كلام رين */
    arm:function(cb){
      if(!stream||timer)return false;
      onInt=cb; var p=Object.assign({},PROFILES[api.getProfile()]||PROFILES.normal);
      vad=createVad(p);
      timer=setInterval(function(){
        if(!analyser)return;
        analyser.getFloatTimeDomainData(buf);
        var sum=0; for(var i=0;i<buf.length;i++)sum+=buf[i]*buf[i];
        var rms=Math.sqrt(sum/buf.length);
        if(vad.step(rms,CFG.frameMs)){api.disarm(); try{onInt&&onInt();}catch(e){}}
      },CFG.frameMs);
      return true;
    },
    disarm:function(){if(timer){clearInterval(timer);timer=null;} vad=null;},
    /* إغلاق كامل: بيطفّي مؤشر الميكروفون */
    close:function(){
      api.disarm();
      if(stream){stream.getTracks().forEach(function(t){try{t.stop();}catch(e){}});stream=null;}
      if(ctx){try{ctx.close();}catch(e){}ctx=null;} analyser=null;
    }
  };
  root.RynDuplex=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
