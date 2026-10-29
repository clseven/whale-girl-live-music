/* The live renderer consumes validated appearance state; it never calls an LLM. */
(()=>{
 const rest={ParamEyeLOpen:1,ParamEyeROpen:1,ParamEyeBallX:0,ParamEyeBallY:0,ParamBrowLX:0,ParamBrowLY:0,ParamBrowLAngle:0,ParamBrowLForm:0,ParamBrowRX:0,ParamBrowRY:0,ParamBrowRAngle:0,ParamBrowRForm:0,ParamMouthForm:0,ParamMouthOpenY:0};
 const emotions={neutral:[],happy:['开心兴奋'],starry:['星星眼'],playful:['调皮'],blush:['脸红'],stunned:['呆呆眼']};
 const effects={none:[],hearts:['love'],flowers:['情绪花花'],question:['问号']};
 const gestures={none:[],cat:['喵喵手~喵~动画'],victory:['双手比耶'],claw:['魔爪']};
 const props={none:[],omelet:['蛋包饭'],parfait:['巴菲'],whale:['鲸鱼'],whale_table:['鲸鱼','鲸鱼放桌上'],dark_table:['深色桌布']};
 const outfits={glasses:{none:[],round:['圆眼镜'],square:['方眼镜'],oval:['椭圆眼镜'],sunglasses:['墨镜']},head:{none:[],ponytail:['单边马尾'],maid:['头箍'],bow:['蝴蝶结贴纸']},sticker:{none:[],cat:['猫猫贴纸'],rabbit:['兔兔贴纸'],flowers:['情绪花花']}};
 function buildTarget(names,catalog,activity,gesture,theme){
  const target={jingyu:1,phone:activity==='phone'&&(!gesture||gesture==='none')?1:0};
  for(const name of names)for(const p of catalog[name]||[])target[p.Id]=p.Value;
  // Table color is a time-of-day setting, independent of props or motions.
  target.cc2=theme==='night'?1:0;
  return target;
 }
 window.whaleVisualParameters=buildTarget;
 class WhaleActor{
  constructor(canvas){this.canvas=canvas;this.state={outfit:{},prop:'none',reaction:{}};this.signature='';this.target={};this.from={};this.started=0;this.idleUntil=0;this.idleSmile=false;this.ready=this.boot();}
  async boot(){
   const response=await fetch('/assets/live2d/expressions.json');if(!response.ok)throw Error('表情文件未能加载');this.catalog=await response.json();
   this.ids=[...new Set(Object.values(this.catalog).flatMap(items=>items.map(p=>p.Id)))];
   this.app=new PIXI.Application({view:this.canvas,resizeTo:this.canvas.parentElement,backgroundAlpha:0,antialias:true,autoDensity:true,resolution:Math.min(devicePixelRatio,this.state.renderer?.pixel_ratio??1)});
   this.app.ticker.maxFPS=this.state.renderer?.max_fps??30;
   this.model=await PIXI.live2d.Live2DModel.from('/assets/live2d/model/c_0120.model3.json',{autoInteract:false});this.app.stage.addChild(this.model);this.model.anchor.set(.5,1);
   // Idle does not animate all the parameters changed by sauce/selfie/bubble motions.
   // Remember the actual model defaults, so stopping an action also restores its pose.
   const actionFiles=['番茄酱','自拍','chuipaopao'];
   const definitions=await Promise.all(actionFiles.map(async name=>{const r=await fetch('/assets/live2d/model/motions/'+encodeURIComponent(name)+'.motion3.json');if(!r.ok)throw Error('动作文件未能加载');return r.json();}));
   this.actions={eat:{index:3,duration:definitions[0].Meta.Duration,setting:'omelet_motion_seconds'},selfie:{index:4,duration:definitions[1].Meta.Duration,setting:'selfie_motion_seconds'},blow_bubbles:{index:0,duration:definitions[2].Meta.Duration,setting:'bubble_motion_seconds'}};
   this.model.internalModel.motionManager.on('motionFinish',()=>{if(this.activeMotion?.playing)this.activeMotion.finished=true;});
   this.motionDefaults={};const core=this.model.internalModel.coreModel;
   for(const id of new Set(definitions.flatMap(d=>d.Curves.filter(c=>c.Target==='Parameter').map(c=>c.Id)))){
    const value=core.getParameterDefaultValue?.(core.getParameterIndex(id));this.motionDefaults[id]=Number.isFinite(value)?value:core.getParameterValueById(id);
   }
   const area=this.canvas.parentElement;const layout=()=>{const w=area.clientWidth,h=area.clientHeight;this.app.renderer.resize(w,h);this.model.scale.set(Math.min(w/this.model.internalModel.originalWidth,h/this.model.internalModel.originalHeight));this.model.x=w/2;this.model.y=h;};this.layout=layout;new ResizeObserver(layout).observe(area);layout();
   document.addEventListener('visibilitychange',()=>{if(document.hidden&&!document.documentElement.classList.contains('audience-view'))this.app.ticker.stop();else this.app.ticker.start();});
   const renderer=this,internal=this.model.internalModel,original=internal.update;
   internal.update=function(){const value=original.apply(this,arguments);renderer.draw(this.coreModel);return value;};
   this.update(this.state,true);document.getElementById('actorLoading').hidden=true;
   return this;
  }
  update(state,force=false){
   this.requestedState=state||this.requestedState||this.state;if(!this.model){this.state=this.requestedState;return;}
   let desired=this.requestedState;
   if(desired.reaction?.effect_until*1000<=Date.now())desired={...desired,reaction:{...desired.reaction,effect:'none'}};
   const bodyKey=s=>JSON.stringify([s.prop,s.activity,s.reaction?.gesture||'none']);
   const held=this.activeMotion||this.poseReturn||performance.now()<(this.bodyHoldUntil||0);
   clearTimeout(this.bodyTimer);
   if(bodyKey(desired)!==bodyKey(this.state)&&held){
    // Latest desired body wins; facial expressions, effects and wardrobe remain responsive.
    desired={...desired,prop:this.state.prop,activity:this.state.activity,scene:this.state.scene,reaction:{...desired.reaction,gesture:this.state.reaction?.gesture||'none'}};
    if(!this.activeMotion&&!this.poseReturn)this.bodyTimer=setTimeout(()=>this.update(this.requestedState),Math.max(0,this.bodyHoldUntil-performance.now()));
   }else if(bodyKey(desired)!==bodyKey(this.state)){
    this.bodyHoldUntil=performance.now()+(desired.renderer?.min_action_seconds??3)*1000;
   }
   this.state=desired;
   if(this.bodyHoldUntil===undefined)this.bodyHoldUntil=performance.now()+(desired.renderer?.min_action_seconds??3)*1000;
   this.app.ticker.maxFPS=this.state.renderer?.max_fps??30;
   const ratio=Math.min(devicePixelRatio,this.state.renderer?.pixel_ratio??1);
   if(this.app.renderer.resolution!==ratio){this.app.renderer.resolution=ratio;this.layout();}
   clearTimeout(this.effectTimer);
   if(this.state.reaction?.effect&&this.state.reaction.effect!=='none'&&this.state.reaction.effect_until)this.effectTimer=setTimeout(()=>this.update(this.requestedState,true),Math.max(0,this.state.reaction.effect_until*1000-Date.now()));
   const signature=JSON.stringify([this.state.outfit,this.state.prop,this.state.activity,this.state.scene?.name,this.state.reaction?.emotion,this.state.reaction?.effect,this.state.reaction?.gesture,this.state.time_theme?.effective]);if(!force&&signature===this.signature)return;this.signature=signature;
   const r=this.state.reaction||{};const names=[...(emotions[r.emotion]||[]),...(effects[r.effect]||[]),...(gestures[r.gesture]||[]),...(props[this.state.prop]||[])];
   const activity=this.state.activity||(this.state.prop==='omelet'?'eat':'phone');
   for(const [slot,options] of Object.entries(outfits))names.push(...(options[this.state.outfit?.[slot]]||[]));
   this.from={};const cm=this.model.internalModel.coreModel;for(const id of this.ids)this.from[id]=cm.getParameterValueById(id);
   this.target=buildTarget(names,this.catalog,activity,r.gesture,this.state.time_theme?.effective);this.started=performance.now();
   if(activity==='phone'&&(!r.gesture||r.gesture==='none'))Object.assign(this.target,{phone4:0,phone5:0,phone6:0});
   const label={phone:'玩手机',parfait:'甜品桌面',whale:'小鲸鱼',whale_table:'桌上鲸鱼',dark_table:'桌面待机',selfie:'自拍',blow_bubbles:'吹泡泡',omelet:'蛋包饭',cat:'喵喵手',victory:'双手比耶',claw:'魔爪'}[this.state.scene?.name]||'日常待机';
   this.canvas?.setAttribute('aria-label','实时 Live2D 鲸鱼娘，当前场景：'+label);
   const motionKey=activity;
   if(motionKey!==this.lastMotionKey){
    const action=this.actions?.[activity];
    if(action){
     this.startAction(action);
    }else this.model.motion('Idle',0,3).catch(()=>{});
    this.lastMotionKey=motionKey;
   }
   this.lastProp=this.state.prop;
  }
  startAction(action){
   const token={action,remaining:Math.max(1,Math.ceil((this.state.renderer?.[action.setting]??action.duration)/action.duration)),finished:false};
   this.activeMotion=token;this.playActionCycle(token);
  }
  async playActionCycle(token){
   token.playing=false;
   try{
    const motion=await this.model.internalModel.motionManager.loadMotion('Tap',token.action.index);
    if(this.activeMotion!==token)return;
    if(!motion)throw Error('动作未加载');
    // Original assets loop forever. Let Cubism finish a complete cycle, including fade-out.
    motion.setIsLoop(false);
    const started=await this.model.motion('Tap',token.action.index,3);
    if(this.activeMotion===token){token.playing=started!==false;if(started===false)token.finished=true;}
   }catch{if(this.activeMotion===token)token.finished=true;}
  }
  finishAction(){
   const token=this.activeMotion;
   if(--token.remaining>0){token.finished=false;this.playActionCycle(token);return;}
   this.activeMotion=null;
   const core=this.model.internalModel.coreModel,from={};
   for(const id of Object.keys(this.motionDefaults||{}))from[id]=core.getParameterValueById(id);
   this.model.internalModel.motionManager.stopAllMotions();
   this.poseReturn={from,started:performance.now(),duration:(this.state.renderer?.transition_seconds??.45)*1000};
   this.model.motion('Idle',0,3).catch(()=>{});
  }
  draw(cm){
   if(this.activeMotion?.finished)this.finishAction();
   if(this.poseReturn){
    const pose=this.poseReturn,t=Math.min(1,(performance.now()-pose.started)/pose.duration),blend=t*t*(3-2*t);
    for(const [id,value] of Object.entries(this.motionDefaults||{}))cm.setParameterValueById(id,pose.from[id]*(1-blend)+value*blend);
    if(t>=1){cm.saveParameters?.();this.poseReturn=null;this.update(this.requestedState);}
   }
   const progress=Math.min(1,(performance.now()-this.started)/220);
   for(const id of this.ids){const desired=this.target[id]??rest[id]??0;cm.setParameterValueById(id,(this.from[id]??desired)*(1-progress)+desired*progress);}
   for(const [id,v] of Object.entries(this.target))cm.setParameterValueById(id,(this.from[id]??v)*(1-progress)+v*progress);
   if((this.state.reaction?.effect||'none')==='none'){
    // The idle motion also animates floating ribbons/hearts independently of expressions.
    for(const id of ['paopao','paopao2','paopao3','paopao4','paopao5','aixing','Param9'])cm.setParameterValueById(id,0);
    if(this.state.outfit?.sticker!=='flowers')for(const id of ['ParamCheek28','ParamCheek36'])cm.setParameterValueById(id,0);
   }
   if(!('ParamEyeLOpen' in this.target)&&!('ParamEyeROpen' in this.target)){
    // Expressions such as '开心兴奋' set the eyes to 0 (closed). Returning to neutral must use the same
    // fade as every other parameter, otherwise the eyes snap open on the first frame and read as a flicker.
    const phase=performance.now()%4700;const v=phase<170?1-Math.sin(phase/170*Math.PI):1;
    cm.setParameterValueById('ParamEyeLOpen',(this.from.ParamEyeLOpen??v)*(1-progress)+v*progress);
    cm.setParameterValueById('ParamEyeROpen',(this.from.ParamEyeROpen??v)*(1-progress)+v*progress);
   }
   if((this.state.reaction?.emotion||'neutral')==='neutral'){
    if(Date.now()>this.idleUntil){this.idleSmile=Math.random()<.4;this.idleUntil=Date.now()+7000+Math.random()*8000;}
    if(!('ParamMouthForm' in this.target))cm.setParameterValueById('ParamMouthForm',this.idleSmile ? 0.35 : 0);
   }
  }
 }
 window.whaleActor=new WhaleActor(document.getElementById('live2dCanvas'));
 window.whaleActor.ready.catch(error=>{const el=document.getElementById('actorLoading');el.hidden=false;el.textContent='Live2D 加载失败：'+error.message;});
 document.addEventListener('whale-visual',event=>window.whaleActor.update(event.detail));
})();
