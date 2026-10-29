// Offline regression for integer-rounded scrollTop; no browser or model calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const sandbox={window:{matchMedia:()=>({matches:false})},document:{documentElement:{classList:{contains:()=>true}}}};
vm.runInNewContext(fs.readFileSync('music_queue.js','utf8'),sandbox);
let stored=0;
const list={get scrollTop(){return stored},set scrollTop(v){stored=Math.round(v)}};
const view=Object.assign(Object.create(sandbox.window.MusicQueueView.prototype),{list,overflow:true,lastFrame:0,holdUntil:0,hover:false,cycleHeight:200,offset:0,lastWritten:0,policy:{queue_auto_scroll:true,queue_scroll_pixels_per_second:18}});
view.scroll(1000);for(let n=1;n<=60;n++)view.scroll(1000+n*1000/60);
assert(Math.abs(list.scrollTop-18)<=1,'fractional motion must accumulate');
view.offset=199;view.lastFrame=2000;view.scroll(2100);assert(view.offset<2,'last row wraps into copy without a pause');
const still=list.scrollTop;view.policy.queue_auto_scroll=false;view.scroll(2200);assert.equal(list.scrollTop,still);
sandbox.window.matchMedia=()=>({matches:true});view.policy.queue_auto_scroll=true;view.scroll(2300);assert.equal(list.scrollTop,still);
console.log('4 rolling-board checks passed');
