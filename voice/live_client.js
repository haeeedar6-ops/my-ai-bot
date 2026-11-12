/* RYN Live — مكالمة صوتية حية بقناة وحدة (WebSocket) مع السيرفر ← Gemini Live.
   - ميكروفون واحد فقط (getUserMedia مع إلغاء الصدى) → PCM16 16kHz → WebSocket.
   - الرد PCM16 24kHz بيتشغّل متواصل عبر WebAudio، وبينوقف فوراً عند المقاطعة.
   - ما في SpeechRecognition ولا TTS منفصل: مافي حاجة تطفي/تشغّل المايك.
   - حدود المتصفح: Chrome بيوقف الميكروفون لما الصفحة تروح بالخلفية (الشاشة لازم تضل شغالة). */
(function(root){
  /* ---------- دوال نقية (قابلة للاختبار) ---------- */
  function makeDown(inRate,outRate,chunk){
    outRate=outRate||16000;chunk=chunk||640;
    var ratio=inRate/outRate,pos=0,sum=0,cnt=0,buf=new Int16Array(chunk),n=0;
    return {
      push:function(f32){
        var res=[];
        for(var i=0;i<f32.length;i++){
          sum+=f32[i];cnt++;pos+=1;
          if(pos>=ratio){
            var v=sum/cnt;sum=0;cnt=0;pos-=ratio;
            v=Math.max(-1,Math.min(1,v));
            buf[n++]=v<0?v*32768:v*32767;
            if(n>=chunk){res.push(buf.slice(0,n));n=0;}
          }
        }
        return res;
      }
    };
  }
  function pcm16ToFloat32(ab){
    var i16=new Int16Array(ab.byteLength>>1?ab.slice(0,ab.byteLength&~1):new ArrayBuffer(0));
    var f=new Float32Array(i16.length);
    for(var i=0;i<i16.length;i++)f[i]=i16[i]/32768;
    return f;
  }
  function rmsOf(f32){var s=0;for(var i=0;i<f32.length;i++)s+=f32[i]*f32[i];return f32.length?Math.sqrt(s/f32.length):0;}

  function b64FromBuffer(ab){
    var u=new Uint8Array(ab),s='';
    for(var i=0;i<u.length;i+=0x8000)s+=String.fromCharCode.apply(null,u.subarray(i,i+0x8000));
    return btoa(s);
  }
  function b64ToBuffer(b64){
    var bin=atob(b64),u=new Uint8Array(bin.length);
    for(var i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);
    return u.buffer;
  }
  /* رسالة setup بأكتر من صياغة (الأسماء بتتغير بين نسخ الـ API): نجرّب بالترتيب لحد ما setupComplete توصل */
  function buildSetupVariants(model,voice,si){
    var m=model.indexOf('models/')===0?model:'models/'+model;
    var sys={parts:[{text:si}]};
    var speech={voiceConfig:{prebuiltVoiceConfig:{voiceName:voice}}};
    return [
      {setup:{model:m,generationConfig:{responseModalities:['AUDIO'],speechConfig:speech},systemInstruction:sys,inputAudioTranscription:{},outputAudioTranscription:{}}},
      {setup:{model:m,generationConfig:{responseModalities:['AUDIO']},systemInstruction:sys}},
      {setup:{model:m,responseModalities:['AUDIO'],speechConfig:speech,systemInstruction:sys,inputAudioTranscription:{},outputAudioTranscription:{}}}
    ];
  }
  /* رسالة Google الخام → أحداث بسيطة */
  function parseServerMessage(o){
    var ev=[];if(!o||typeof o!=='object')return ev;
    if(o.setupComplete!==undefined)ev.push({k:'ready'});
    var sc=o.serverContent;
    if(sc){
      var parts=(sc.modelTurn&&sc.modelTurn.parts)||[];
      for(var i=0;i<parts.length;i++){var d=parts[i].inlineData&&parts[i].inlineData.data;if(d)ev.push({k:'audio',b64:d});}
      if(sc.inputTranscription&&sc.inputTranscription.text)ev.push({k:'user_text',text:sc.inputTranscription.text});
      if(sc.outputTranscription&&sc.outputTranscription.text)ev.push({k:'bot_text',text:sc.outputTranscription.text});
      if(sc.interrupted)ev.push({k:'interrupted'});
      if(sc.turnComplete)ev.push({k:'turn_complete'});
    }
    if(o.goAway!==undefined)ev.push({k:'go_away'});
    return ev;
  }

  var WORKLET_SRC=function(){
    return "var makeDown="+makeDown.toString()+";"+
    "class P extends AudioWorkletProcessor{constructor(){super();this.d=makeDown(sampleRate,16000,640);}"+
    "process(inputs){var ch=inputs[0]&&inputs[0][0];if(!ch)return true;var o=this.d.push(ch);"+
    "for(var i=0;i<o.length;i++)this.port.postMessage(o[i].buffer,[o[i].buffer]);return true;}}"+
    "registerProcessor('ryn-down',P);";
  };

  /* ---------- الجلسة ---------- */
  var S=null;
  function supported(){
    return !!(root.WebSocket&&root.navigator&&navigator.mediaDevices&&navigator.mediaDevices.getUserMedia&&(root.AudioContext||root.webkitAudioContext));
  }
  function wsUrl(){return (location.protocol==='https:'?'wss://':'ws://')+location.host+'/api/v2/live/ws';}

  async function start(o){
    if(S)stop();
    o=o||{};
    var cb=function(n,a,b){try{o[n]&&o[n](a,b);}catch(e){}};
    var AC=root.AudioContext||root.webkitAudioContext;
    var st={o:o,cb:cb,mode:(o.mode==='direct'?'direct':'proxy'),ready:false,q:Promise.resolve(),userBuf:'',botBuf:'',dReconnects:0,stopped:false,muted:false,ws:null,ctx:null,stream:null,node:null,src:null,analyser:null,
      nextT:0,active:[],dropAudio:false,speaking:false,retry:0,vad:null,vadTimer:null,pingTimer:null,vis:null,micBuf:null};
    S=st;
    try{
      st.stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1}});
    }catch(e){S=null;cb('onError','mic_denied','لازم تسمح للميكروفون');return false;}
    st.ctx=new AC();
    try{await st.ctx.resume();}catch(e){}
    st.src=st.ctx.createMediaStreamSource(st.stream);
    st.analyser=st.ctx.createAnalyser();st.analyser.fftSize=1024;st.micBuf=new Float32Array(1024);
    st.src.connect(st.analyser);
    var sendPcm=function(ab){
      if(!st.ws||st.ws.readyState!==1||st.muted)return;
      if(st.mode==='direct'){if(st.ready)st.ws.send(JSON.stringify({realtimeInput:{audio:{data:b64FromBuffer(ab),mimeType:'audio/pcm;rate=16000'}}}));}
      else st.ws.send(ab);
    };
    try{
      if(!st.ctx.audioWorklet)throw new Error('noworklet');
      var url=URL.createObjectURL(new Blob([WORKLET_SRC()],{type:'application/javascript'}));
      await st.ctx.audioWorklet.addModule(url);URL.revokeObjectURL(url);
      st.node=new AudioWorkletNode(st.ctx,'ryn-down');
      st.node.port.onmessage=function(e){sendPcm(e.data);};
      st.src.connect(st.node);
      var mute=st.ctx.createGain();mute.gain.value=0;st.node.connect(mute);mute.connect(st.ctx.destination);
    }catch(e){
      /* احتياطي: ScriptProcessor (قديم لكن مدعوم) */
      var d=makeDown(st.ctx.sampleRate,16000,640);
      st.node=st.ctx.createScriptProcessor(2048,1,1);
      st.node.onaudioprocess=function(ev){var out=d.push(ev.inputBuffer.getChannelData(0));for(var i=0;i<out.length;i++)sendPcm(out[i].buffer);};
      st.src.connect(st.node);st.node.connect(st.ctx.destination);
    }
    /* مقاطعة محلية سريعة: نفس منطق RynDuplex لكن على نفس الميكروفون (بدون فتح ميكروفون تاني) */
    if(o.localBargeIn!==false&&root.RynDuplex&&RynDuplex.createVad){
      var prof=(RynDuplex.PROFILES&&RynDuplex.PROFILES[o.profile||'normal'])||{};
      st.vad=null;
      st.vadTimer=setInterval(function(){
        if(!st.speaking||st.muted){st.vad=null;return;}
        if(!st.vad)st.vad=RynDuplex.createVad(prof);
        st.analyser.getFloatTimeDomainData(st.micBuf);
        if(st.vad.step(rmsOf(st.micBuf),30)){st.vad=null;localInterrupt();}
      },30);
    }
    st.pingTimer=setInterval(function(){if(st.mode!=='direct'&&st.ws&&st.ws.readyState===1&&st.muted)st.ws.send(JSON.stringify({type:'ping'}));},20000);
    st.vis=function(){
      if(!document.hidden&&st.ctx&&st.ctx.state==='suspended')st.ctx.resume().catch(function(){});
      if(!document.hidden&&st.stream&&st.stream.getAudioTracks().some(function(t){return t.readyState==='ended';}))cb('onError','mic_lost','الميكروفون انقطع (الصفحة راحت بالخلفية). اضغط إنهاء وابدأ من جديد.');
    };
    document.addEventListener('visibilitychange',st.vis);
    if(st.mode==='direct')connectDirect(st,false);else connectWs(st);
    return true;
  }

  function connectWs(st){
    if(st.stopped)return;
    var ws=new WebSocket(wsUrl());ws.binaryType='arraybuffer';st.ws=ws;
    st.cb('onState','connecting');
    ws.onopen=function(){
      var h=[];try{h=(st.o.getHistory&&st.o.getHistory())||[];}catch(e){}
      ws.send(JSON.stringify({type:'start',voice:st.o.voice||'',dialect:st.o.dialect||'syrian',memory:st.o.memory||'',history:h}));
    };
    ws.onmessage=function(ev){
      if(typeof ev.data!=='string'){playChunk(st,ev.data);return;}
      var m;try{m=JSON.parse(ev.data);}catch(e){return;}
      switch(m.type){
        case 'ready':st.retry=0;st.cb('onReady',m);st.cb('onState','listening');break;
        case 'user_text':st.cb('onUserText',m.text);break;
        case 'bot_text':st.cb('onBotText',m.text);break;
        case 'interrupted':stopPlayback(st);st.dropAudio=false;st.cb('onState','listening');break;
        case 'turn_complete':st.dropAudio=false;st.cb('onTurnComplete');break;
        case 'turn':st.cb('onTurn',m.user,m.bot);break;
        case 'reconnecting':st.cb('onState','reconnecting');break;
        case 'warning':break;
        case 'error':st.cb('onError',m.code,m.message);break;
        case 'closed':st.cb('onClosed',m.reason);break;
      }
    };
    ws.onclose=function(){
      if(st.stopped)return;
      if(st.retry<3){st.retry++;st.cb('onState','reconnecting');setTimeout(function(){connectWs(st);},800*st.retry);}
      else{st.cb('onError','ws_lost','انقطع الاتصال بالسيرفر');}
    };
    ws.onerror=function(){};
  }

  /* ---------- اتصال مباشر متصفح ← Google (خطة Gemini) ---------- */
  async function decodeMsg(data){
    if(typeof data==='string')return data;
    if(typeof Blob!=='undefined'&&data instanceof Blob)return await data.text();
    if(data&&data.byteLength!==undefined)return new TextDecoder().decode(data);
    return '';
  }
  async function connectDirect(st,isReconnect){
    if(st.stopped)return;
    st.cb('onState',isReconnect?'reconnecting':'connecting');
    var cfg;
    try{cfg=await st.o.getDirectConfig();}catch(e){st.cb('onError','token_failed',String(e&&e.message||e));return;}
    if(st.stopped)return;
    if(!cfg||!cfg.endpoint||!cfg.token){st.cb('onError','token_failed','ما وصل توكن الاتصال');return;}
    var url=cfg.endpoint+'?'+(cfg.auth_param||'key')+'='+encodeURIComponent(cfg.token);
    var attempts=[];
    (cfg.models&&cfg.models.length?cfg.models:['gemini-3.1-flash-live-preview']).slice(0,3).forEach(function(m){
      buildSetupVariants(m,cfg.voice||'Charon',cfg.system_instruction||'').forEach(function(v,i){attempts.push({m:m,v:v,i:i});});
    });
    attempts=attempts.slice(0,5);
    var idx=0,lastErr='';
    function tryNext(){
      if(st.stopped)return;
      if(idx>=attempts.length){st.cb('onError','connect_failed',lastErr||'تعذّر الاتصال بـ Gemini Live');return;}
      var at=attempts[idx++],ws=new WebSocket(url),done=false,timer=null;
      st.ws=ws;st.ready=false;
      var fail=function(why){if(done)return;done=true;clearTimeout(timer);lastErr=why||lastErr;try{ws.onclose=null;ws.close();}catch(e){}tryNext();};
      ws.onopen=function(){ws.send(JSON.stringify(at.v));timer=setTimeout(function(){fail('timeout setup');},8000);};
      ws.onerror=function(){};
      ws.onclose=function(ev){
        if(!done){fail('close '+(ev&&ev.code)+' '+String(ev&&ev.reason||'').slice(0,120));return;}
      };
      ws.onmessage=function(ev){
        st.q=st.q.then(function(){return decodeMsg(ev.data);}).then(function(txt){
          if(!txt)return;var o;try{o=JSON.parse(txt);}catch(e){return;}
          var evs=parseServerMessage(o);
          for(var i=0;i<evs.length;i++){
            var e=evs[i];
            if(e.k==='ready'&&!done){
              done=true;clearTimeout(timer);st.ready=true;st.retry=0;
              ws.onclose=function(){onDirectClosed(st);};
              st.cb('onReady',{model:at.m,variant:at.i});st.cb('onState','listening');
            }else if(e.k==='audio'){playChunk(st,b64ToBuffer(e.b64));}
            else if(e.k==='user_text'){st.userBuf+=e.text;st.cb('onUserText',e.text);}
            else if(e.k==='bot_text'){st.botBuf+=e.text;st.cb('onBotText',e.text);}
            else if(e.k==='interrupted'){stopPlayback(st);st.dropAudio=false;st.cb('onState','listening');}
            else if(e.k==='turn_complete'){
              st.dropAudio=false;
              var u=st.userBuf.trim(),b=st.botBuf.trim();st.userBuf='';st.botBuf='';
              if(u||b)st.cb('onTurn',u,b);
              st.cb('onTurnComplete');
            }else if(e.k==='go_away'){st.goAway=true;}
          }
        }).catch(function(){});
      };
    }
    tryNext();
  }
  function onDirectClosed(st){
    if(st.stopped)return;
    st.ready=false;
    if(st.dReconnects<4){st.dReconnects++;setTimeout(function(){connectDirect(st,true);},600*st.dReconnects);}
    else st.cb('onError','ws_lost','انقطع الاتصال بـ Gemini');
  }

  function playChunk(st,ab){
    if(st.dropAudio||st.stopped||!st.ctx)return;
    var f=pcm16ToFloat32(ab);if(!f.length)return;
    var buf=st.ctx.createBuffer(1,f.length,24000);buf.copyToChannel(f,0);
    var src=st.ctx.createBufferSource();src.buffer=buf;src.connect(st.ctx.destination);
    var now=st.ctx.currentTime;
    if(st.nextT<now+0.02)st.nextT=now+0.02;
    src.start(st.nextT);st.nextT+=buf.duration;
    st.active.push(src);
    if(!st.speaking){st.speaking=true;st.cb('onState','speaking');}
    src.onended=function(){
      var i=st.active.indexOf(src);if(i>=0)st.active.splice(i,1);
      if(!st.active.length&&st.speaking&&st.ctx&&st.nextT<=st.ctx.currentTime+0.05){st.speaking=false;st.cb('onState','listening');}
    };
  }
  function stopPlayback(st){
    st.active.slice().forEach(function(s){try{s.onended=null;s.stop();}catch(e){}});
    st.active=[];st.nextT=0;
    if(st.speaking){st.speaking=false;}
  }
  function localInterrupt(){
    if(!S)return;
    stopPlayback(S);S.dropAudio=true;   /* نتجاهل بقية الرد لحد ما Gemini يأكد المقاطعة أو ينتهي الدور */
    S.cb('onState','listening');
  }
  function setMuted(v){
    if(!S)return;S.muted=!!v;
    S.stream.getAudioTracks().forEach(function(t){t.enabled=!v;});
    if(v){
      localInterrupt();
      if(S.mode==='direct'&&S.ws&&S.ws.readyState===1)S.ws.send(JSON.stringify({realtimeInput:{audioStreamEnd:true}}));
    }
  }
  function sendText(t){
    if(!S||!S.ws||S.ws.readyState!==1)return;
    if(S.mode==='direct')S.ws.send(JSON.stringify({realtimeInput:{text:String(t||'')}}));
    else S.ws.send(JSON.stringify({type:'text',text:String(t||'')}));
  }
  function stop(){
    var st=S;if(!st)return;S=null;st.stopped=true;
    try{if(st.mode!=='direct'&&st.ws&&st.ws.readyState===1)st.ws.send(JSON.stringify({type:'end'}));}catch(e){}
    try{st.ws&&st.ws.close();}catch(e){}
    stopPlayback(st);
    if(st.vadTimer)clearInterval(st.vadTimer);if(st.pingTimer)clearInterval(st.pingTimer);
    if(st.vis)document.removeEventListener('visibilitychange',st.vis);
    try{st.node&&st.node.disconnect();}catch(e){}
    try{st.src&&st.src.disconnect();}catch(e){}
    if(st.stream)st.stream.getTracks().forEach(function(t){try{t.stop();}catch(e){}});   /* بيطفّي مؤشر الميكروفون */
    try{st.ctx&&st.ctx.close();}catch(e){}
  }

  var api={supported:supported,start:start,stop:stop,setMuted:setMuted,interrupt:localInterrupt,sendText:sendText,
    isActive:function(){return !!S;},_makeDown:makeDown,_pcm16ToFloat32:pcm16ToFloat32,_b64FromBuffer:b64FromBuffer,_b64ToBuffer:b64ToBuffer,_buildSetupVariants:buildSetupVariants,_parseServerMessage:parseServerMessage};
  root.RynLive=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
