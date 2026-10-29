/* Exercise the real player with synthetic media events, never request audio. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const env={window:{},setTimeout:()=>1,clearTimeout:()=>{}};vm.createContext(env);vm.runInContext(fs.readFileSync('voice_player.js','utf8'),env);
class Audio{
 constructor(){this.listeners={};this.paused=true;this.ended=false;}
 addEventListener(type,fn){(this.listeners[type]??=new Set()).add(fn);}
 removeEventListener(type,fn){this.listeners[type]?.delete(fn);}
 emit(type){for(const fn of this.listeners[type]||[])fn();}
 play(){this.paused=false;return Promise.resolve();}
 pause(){this.paused=true;this.emit('pause');}
}
(async()=>{
 const blockedAudio=new Audio();blockedAudio.play=()=>Promise.reject({name:'NotAllowedError',message:'User gesture required'});
 const blocked=new env.window.VoicePlayer(blockedAudio);
 await assert.rejects(blocked.play('/audio/offline.mp3'),/NotAllowedError: User gesture required/);
 assert.equal(blocked.blocked,true);assert.equal(blockedAudio.paused,true);blocked.unlock();assert.equal(blocked.blocked,false);
 const goodAudio=new Audio(),good=new env.window.VoicePlayer(goodAudio);const playing=good.play('/audio/offline.mp3');
 goodAudio.emit('pause'); // stale pause before playback starts must not fail this clip
 await Promise.resolve();goodAudio.ended=true;goodAudio.emit('ended');await playing;assert.equal(good.blocked,false);
 const badAudio=new Audio(),bad=new env.window.VoicePlayer(badAudio);const decoding=bad.play('/audio/offline.mp3');
 badAudio.error={code:4,message:'Unsupported format'};badAudio.emit('error');await assert.rejects(decoding,/MediaError: code 4 Unsupported format/);
 assert.equal(badAudio.listeners.error.size,0);
 console.log('Voice player: autoplay details, safe stop, gesture recovery, stale pause and decode errors passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
