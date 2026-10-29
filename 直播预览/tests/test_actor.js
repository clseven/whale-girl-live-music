/* Actual renderer class, fake Cubism lifecycle: no browser, model APIs or delays. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('assets/live2d/actor.js','utf8');
const prefix=source.slice(0,source.indexOf(' window.whaleActor=new WhaleActor'));
function fixture(){
 let now=1000000;const calls=[],timers=[],values={keyboard:1,xbox:1,chuipaopao3:1};
 const context={window:{},devicePixelRatio:1,Date:{now:()=>now},performance:{now:()=>now},Math,
  clearTimeout:t=>{if(t)t.cancelled=true;},setTimeout:(fn,delay)=>{const t={fn,delay};timers.push(t);return t;}};
 vm.createContext(context);vm.runInContext(prefix+'window.ActorClass=WhaleActor;})();',context);
 const actor=Object.create(context.window.ActorClass.prototype);
 actor.state={outfit:{},prop:'none',activity:'rest',reaction:{}};
 actor.catalog={'蛋包饭':[{Id:'danbaofan',Value:1},{Id:'point',Value:-1}],love:[{Id:'love',Value:1}],'喵喵手~喵~动画':[{Id:'maoshou',Value:1}],'蝴蝶结贴纸':[{Id:'ParamCheek81',Value:1}]};
 actor.ids=['danbaofan','point','love','maoshou','ParamCheek81'];actor.motionDefaults={keyboard:0,xbox:0,chuipaopao3:0};
 actor.actions={eat:{index:3,duration:5,setting:'omelet_motion_seconds'},selfie:{index:4,duration:3.3,setting:'selfie_motion_seconds'},blow_bubbles:{index:0,duration:5,setting:'bubble_motion_seconds'}};
 const core={getParameterValueById:id=>values[id]||0,setParameterValueById:(id,v)=>values[id]=v,saveParameters:()=>calls.push('save')};
 actor.model={internalModel:{coreModel:core,motionManager:{stopAllMotions:()=>calls.push('stop'),loadMotion:async(group,index)=>({setIsLoop:v=>calls.push(['loop',index,v])})}},motion:async(...args)=>{calls.push(args);return true;}};
 actor.app={ticker:{},renderer:{resolution:1}};
 const state=(activity,prop='none',id=activity)=>({outfit:{head:'bow'},prop,activity,scene:{id,name:activity},renderer:{omelet_motion_seconds:3,min_action_seconds:3,transition_seconds:.45},reaction:{emotion:'neutral',effect:'none',gesture:'none',until:0}});
 return {actor,calls,values,core,state,advance:ms=>{now+=ms;},finish:()=>{actor.activeMotion.finished=true;actor.draw(core);}};
}
const flush=async()=>{await Promise.resolve();await Promise.resolve();};
(async()=>{
 const f=fixture(),{actor,calls,values,core,state}=f;
 actor.update({...state('eat','omelet'),reaction:{emotion:'happy',effect:'hearts',gesture:'none',until:1009,effect_until:1003}});await flush();
 assert.equal(calls.filter(c=>Array.isArray(c)&&c[0]==='Tap').length,1);
 assert.ok(calls.some(c=>Array.isArray(c)&&c[0]==='loop'&&c[2]===false));
 assert.equal(actor.activeMotion.remaining,1,'legacy 3s setting must still allow a complete 5s cycle');
 f.advance(1000);actor.update(state('phone'));assert.equal(actor.state.activity,'eat');assert.equal(actor.target.danbaofan,1);
 actor.update(state('selfie'));actor.update(state('blow_bubbles'));
 assert.equal(calls.filter(c=>Array.isArray(c)&&c[0]==='Tap').length,1,'no overlapping action loads');
 f.advance(2000);actor.effectTimer.fn();assert.equal(actor.state.reaction.effect,'none');assert.equal(actor.requestedState.activity,'blow_bubbles','effect timeout must not erase pending action');
 f.advance(2000);values.keyboard=1;f.finish();assert.equal(actor.state.activity,'eat');assert.equal(values.keyboard,1,'no pose snap at finish');
 f.advance(225);actor.draw(core);assert.ok(values.keyboard>0&&values.keyboard<1,'return interpolates');
 f.advance(226);actor.draw(core);await flush();assert.equal(actor.state.activity,'blow_bubbles');assert.equal(values.keyboard,0);
 assert.equal(actor.target.ParamCheek81,1,'headwear preserved');
 assert.equal(calls.filter(c=>Array.isArray(c)&&c[0]==='Tap'&&c[1]===4).length,0,'superseded selfie never runs');
 assert.equal(calls.filter(c=>Array.isArray(c)&&c[0]==='Tap'&&c[1]===0).length,1);
 const count=calls.length;
 actor.update({...actor.requestedState,scene:{id:'new-poll-id',name:'blow_bubbles'},time_theme:{effective:'night'}});
 actor.update({...actor.requestedState,reaction:{emotion:'happy',effect:'none',gesture:'none',until:2000}});
 assert.equal(calls.length,count,'poll identity, expression and table changes do not restart action');
 assert.equal(actor.target.cc2,1);
 f.advance(5000);f.finish();f.advance(451);actor.draw(core);await flush();
 actor.update({...actor.requestedState,scene:{id:'repeat-same-scene',name:'blow_bubbles'}});
 assert.equal(calls.filter(c=>Array.isArray(c)&&c[0]==='Tap'&&c[1]===0).length,1,'same semantic action does not replay');
 const g=fixture();g.actor.update({...g.state('rest'),reaction:{gesture:'cat',emotion:'happy'}});
 g.advance(100);g.actor.update({...g.state('rest'),reaction:{gesture:'claw',emotion:'happy'}});
 assert.equal(g.actor.state.reaction.gesture,'cat','hold transient poses');
 g.advance(3000);g.actor.bodyTimer.fn();assert.equal(g.actor.state.reaction.gesture,'claw');
 const h=fixture();h.actor.model.internalModel.motionManager.loadMotion=async()=>{throw Error('missing')};
 h.actor.update(h.state('selfie'));await flush();h.actor.draw(h.core);h.advance(451);h.actor.draw(h.core);
 h.advance(3000);h.actor.update(h.state('phone'));assert.equal(h.actor.state.activity,'phone','failed asset releases gate');
 console.log('Renderer lifecycle checks passed: full cycles, latest action wins, smooth return, stable polls, effects, minimum holds and load failure.');
})().catch(error=>{console.error(error);process.exitCode=1;});
