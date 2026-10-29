/* Preserve browser error details; input caching/model calls do not belong here. */
(()=>{
 class VoicePlayer{
  constructor(audio){this.audio=audio;this.blocked=false;this.lastError='';this.cancelActive=null;}
  unlock(){this.blocked=false;this.lastError='';}
  cancel(){
   this.cancelActive?.();this.audio.pause();this.audio.removeAttribute('src');this.audio.load();this.unlock();
  }
  play(url){
   return new Promise((resolve,reject)=>{
    const a=this.audio;let timer,started=false,settled=false;
    const cleanup=()=>{clearTimeout(timer);if(this.cancelActive===cancel)this.cancelActive=null;for(const [type,fn] of [['ended',done],['error',mediaError],['pause',paused]])a.removeEventListener(type,fn);};
    const cancel=()=>{if(settled)return;settled=true;cleanup();const error=Error('本场语音已清空');error.name='AbortError';error.cancelled=true;reject(error);};
    const done=()=>{if(settled)return;settled=true;cleanup();resolve();};
    const fail=error=>{if(settled)return;settled=true;cleanup();try{a.pause();}catch(_){}this.blocked=true;const name=error?.name||'PlaybackError';this.lastError=name+': '+(error?.message||'语音播放失败');reject(Error(this.lastError));};
    const mediaError=()=>fail({name:'MediaError',message:a.error?('code '+a.error.code+' '+(a.error.message||'')):'音频加载或解码失败'});
    const paused=()=>{if(started&&a.paused&&!a.ended)fail({name:'PlaybackPaused',message:'语音被暂停'});};
    this.cancelActive=cancel;a.src=url;a.addEventListener('ended',done);a.addEventListener('error',mediaError);a.addEventListener('pause',paused);
    timer=setTimeout(()=>fail({name:'PlaybackTimeout',message:'语音播放超时'}),120000);
    try{Promise.resolve(a.play()).then(()=>{started=true;},fail);}catch(error){fail(error);}
   });
  }
 }
 window.VoicePlayer=VoicePlayer;
})();
