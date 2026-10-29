const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const sandbox={window:{},setTimeout,clearTimeout};vm.runInNewContext(fs.readFileSync('voice_player.js','utf8'),sandbox);
class FakeAudio extends EventTarget{
 constructor(){super();this.paused=false;this.ended=false;this.src='';this.loads=0;}
 play(){this.paused=false;this.ended=false;return Promise.resolve();}
 pause(){this.paused=true;this.dispatchEvent(new Event('pause'));}
 removeAttribute(key){if(key==='src')this.src='';}
 load(){this.loads++;}
}
(async()=>{
 const audio=new FakeAudio(),player=new sandbox.window.VoicePlayer(audio);
 const old=player.play('/old.mp3');const stopped=assert.rejects(old,e=>e.cancelled===true);
 await Promise.resolve();player.cancel();await stopped;
 assert.equal(audio.src,'');assert.equal(player.blocked,false);assert.equal(player.lastError,'');assert.equal(player.cancelActive,null);
 const fresh=player.play('/new.mp3');await Promise.resolve();audio.ended=true;audio.dispatchEvent(new Event('ended'));await fresh;
 assert.equal(player.cancelActive,null);player.cancel();assert.equal(audio.loads,2);
 console.log('Voice cancellation and subsequent playback passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
