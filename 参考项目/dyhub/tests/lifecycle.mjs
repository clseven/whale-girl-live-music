import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {BrowserManager} from '../dist/collector/browser.js';
import {Collector} from '../dist/collector/collector.js';
import {LiveSession} from '../dist/collector/liveSession.js';
import {EventEmitter} from 'node:events';
const original=chromium.launch;
let launches=0,current;
chromium.launch=async()=>{
 launches++;
 const context={addInitScript:async()=>{},newPage:async()=>{throw Error('test page creation failed');}};
 current={connected:true,isConnected(){return this.connected;},newContext:async()=>context,close:async()=>{current.connected=false;}};
 return current;
};
try{
 const manager=new BrowserManager({executablePath:'offline-test'});
 await manager.init();await manager.init();assert.equal(launches,1);
 current.connected=false;await manager.init();assert.equal(launches,2,'disconnected browser must relaunch after successful init');
 await assert.rejects(manager.openRoom('https://example.invalid'),/test page creation failed/);
 await manager.init();assert.equal(launches,3,'failed page creation must discard broken browser');
 await manager.close();
 let attempts=0;
 const collector=new Collector({onMessage(){},createSession:()=>{
  if(++attempts===1)throw Error('offline init failure');
  return {roomId:'12345',status:'connecting',startedAt:Date.now(),stats:()=>({wsCount:0,frameCount:0,msgCount:0}),stop:async()=>{},waitForLive:()=>new Promise(()=>{})};
 }});
 await assert.rejects(collector.connect('12345'),/offline init failure/);
 assert.equal(collector.getRooms()[0].status,'error');
 assert.equal(collector.getRooms()[0].progress.detail,'offline init failure');
 await collector.connect('12345');assert.equal(attempts,2,'failed pending promise must release');
 await collector.disconnectAll();
 const page=new EventEmitter();page.content=()=>new Promise(()=>{});page.close=async()=>{};
 const live=new LiveSession('12345',page,new EventEmitter(),{onMessage(){}});
 const began=Date.now();assert.equal(await live.getRoomStatus(),null);assert.ok(Date.now()-began<5000,'blocked page content must have a bounded timeout');
 await live.stop();
 console.log('Offline DyHub lifecycle checks passed: reconnect, failed browser cleanup, visible diagnostics, retry.');
}finally{chromium.launch=original;}
