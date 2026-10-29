// Extracted unchanged from LockyBee/dsh-douyin-agent core.mjs
// Commit: 3ff15a2c7e5ebe6423617f70a9859f2b46ae1993
// MIT; see LICENSE in this directory.
export function readPageState() {
  if(location.origin!=='https://www.douyin.com') return {
    url:null,title:null,video:null,searchResults:[],visibleText:'',loginRequired:false,verificationRequired:false,
    unsupportedPage:true,note:'当前专用标签页已离开抖音，没有读取该页面内容。',
  };
  const visible = element => {
    const r = element.getBoundingClientRect(), s = getComputedStyle(element);
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const clean = value => (value ?? '').replace(/\s+/g, ' ').trim();
  const workId = value => {
    try { const u = new URL(value, location.href); return /\/video\/(\d+)/.exec(u.pathname)?.[1] || u.searchParams.get('modal_id') || u.searchParams.get('aweme_id'); } catch { return null; }
  };
  const privateArea='[data-e2e="im-dialog"],[data-e2e="conversation-item"]';
  const privateChatVisible=[...document.querySelectorAll('[data-e2e="im-dialog"]')].some(visible);
  const videoVisible = element => {
    if(!visible(element))return false;
    for(let node=element;node;node=node.parentElement)if(Number(getComputedStyle(node).opacity)===0)return false;
    return true;
  };
  const videoElements = [...document.querySelectorAll('video')].filter(e=>videoVisible(e)&&!e.closest(privateArea)).map(element => {
    const r = element.getBoundingClientRect();
    return { element, area: Math.max(0, Math.min(innerWidth, r.right) - Math.max(0, r.left)) * Math.max(0, Math.min(innerHeight, r.bottom) - Math.max(0, r.top)) };
  }).sort((a,b) => b.area - a.area);
  const element = videoElements[0]?.element;
  const text = [...document.querySelectorAll('body *')].filter(e => e.childElementCount === 0 && visible(e) && !e.closest('[data-e2e="im-dialog"],[data-e2e="conversation-item"]')).map(e => clean(e.innerText || e.textContent)).filter(Boolean).join('\n').slice(-9000);
  const loginRequired = /扫码登录|验证码登录|登录后即可|手机扫码登录/.test(text);
  const verificationRequired = [...document.querySelectorAll('#captcha_container,[id^="captcha_container"],[data-e2e="captcha-container"]')].some(visible) || /请完成下方验证|请完成验证|安全验证|拖动滑块/.test(text);
  let video = null, activeContainer = null, videoIdentityConflict = false;
  if (element) {
    let container = element.closest('[data-e2e="feed-active-video"],[data-e2e="video-detail"]');
    activeContainer = container;
    if(!container) {
      container=element.parentElement;
      for(let i=0;container?.parentElement&&i<8;i++) {
        if(container.querySelector('[class*="video-info-detail"]')) break;
        container=container.parentElement;
      }
    }
    const links = [...(container?.querySelectorAll('a[href]') ?? [])].filter(e=>visible(e)&&!e.closest(privateArea));
    const playerId=/(?:^|\s)video_(\d+)(?:\s|$)/.exec(element.closest('[data-e2e="player-container"]')?.className??'')?.[1],activeId=/(?:^|\s)video_(\d+)(?:\s|$)/.exec(activeContainer?.className??'')?.[1];
    videoIdentityConflict=Boolean(playerId&&activeId&&playerId!==activeId);
    const id = videoIdentityConflict?null:playerId || activeId || links.map(e=>workId(e.href)).find(Boolean) || workId(location.href) || null;
    const authorElement = links.find(e => /\/user\//.test(e.href)) || [...(container?.querySelectorAll('span,div')??[])].find(e=>e.childElementCount===0 && /^@/.test(clean(e.textContent)) && visible(e));
    const titleElement = container?.querySelector('[data-e2e="video-desc"],[data-e2e="video-title"],[data-e2e="detail-video-info"],[class*="video-info-detail"]');
    const detailAuthor=container?.querySelector('[data-e2e="related-video"]')?.innerText?.split('\n').map(clean).find(Boolean);
    const captionAuthor=/@([^·\n]+)·/.exec(titleElement?.textContent??'')?.[1]?.trim();
    const r = element.getBoundingClientRect();
    video = { id, url: id ? `https://www.douyin.com/video/${id}` : null,
      author: clean(authorElement?.textContent).replace(/^@\s*/, '') || detailAuthor || captionAuthor || null,
      title: clean(titleElement?.getAttribute('data-e2e')==='detail-video-info'?titleElement.innerText.split('\n')[0]:titleElement?.textContent) || clean(links.find(e=>workId(e.href))?.textContent) || null,
      time: element.currentTime, duration: Number.isFinite(element.duration) ? element.duration : null,
      paused: element.paused, muted: element.muted, readyState: element.readyState,
      width: element.videoWidth, height: element.videoHeight,
      viewport: { x: Math.max(0,r.left), y: Math.max(0,r.top), width: Math.min(innerWidth,r.right)-Math.max(0,r.left), height: Math.min(innerHeight,r.bottom)-Math.max(0,r.top) } };
  }
  const route = new URL(location.href);
  const recommendFeed = ['/', '/recommend', '/recommend/'].includes(route.pathname) && !workId(route.href);
  const feedCards = [...document.querySelectorAll('[data-e2e="feed-active-video"],[data-e2e="feed-live-card"],[data-e2e="feed-live"]')].filter(e => visible(e) && !e.closest(privateArea));
  if (!activeContainer && feedCards.length === 1) activeContainer = feedCards[0];
  let livePreview = null;
  if (recommendFeed && activeContainer?.matches('[data-e2e="feed-active-video"],[data-e2e="feed-live-card"],[data-e2e="feed-live"]') && !video?.id && !privateChatVisible && !loginRequired && !verificationRequired) {
    const roomIds = [...activeContainer.querySelectorAll('a[href]')].filter(visible).map(link => {
      try { const target = new URL(link.href, location.href); return target.origin === 'https://live.douyin.com' ? /^\/(\d{4,30})\/?$/.exec(target.pathname)?.[1] : null; } catch { return null; }
    }).filter(Boolean);
    const unique = [...new Set(roomIds)];
    if (unique.length === 1) {
      const r = (element || activeContainer).getBoundingClientRect();
      livePreview = { recognized: true, source: 'active-feed-live-link', scope: 'recommend-feed', roomId: unique[0],
        viewport: { x: Math.max(0,r.left), y: Math.max(0,r.top), width: Math.min(innerWidth,r.right)-Math.max(0,r.left), height: Math.min(innerHeight,r.bottom)-Math.max(0,r.top) } };
    }
  }
  const searchResults = [];
  if (location.pathname.startsWith('/search/')) {
    const candidates = [...document.querySelectorAll('.search-result-card,[data-e2e="search-card"],[data-e2e="search-result"],a[href*="/video/"],a[href*="modal_id="]')].filter(visible);
    const used = new Set();
    for (const candidate of candidates) {
      const anchor = candidate.matches('a') ? candidate : candidate.querySelector('a[href*="/video/"],a[href*="modal_id="]');
      const id = workId(anchor?.href) || candidate.getAttribute('data-aweme-id') || /waterfall_item_(\d+)/.exec(candidate.closest('[id^="waterfall_item_"]')?.id??'')?.[1];
      const card = candidate.closest('.search-result-card,[data-e2e="search-card"],[data-e2e="search-result"]') || candidate;
      const leaves = [...card.querySelectorAll('div,span')].filter(e=>!e.childElementCount).map(e=>clean(e.textContent));
      const title = clean(card.querySelector('[data-e2e="video-desc"],[data-e2e="video-title"]')?.textContent) || leaves.sort((a,b)=>b.length-a.length)[0] || clean(card.textContent);
      if (!title || used.has(id || title)) continue;
      used.add(id || title);
      searchResults.push({ index: searchResults.length, id: id || null, url: id ? `https://www.douyin.com/video/${id}` : null,
        title: title.slice(0,1000), author: clean(card.querySelector('a[href*="/user/"]')?.textContent).replace(/^@\s*/, '') || /@([^\n·]+)/.exec(card.innerText)?.[1]?.trim() || null });
    }
  }
  const u = new URL(location.href);
  for (const key of [...u.searchParams.keys()]) if (!['modal_id','aweme_id','recommend'].includes(key)) u.searchParams.delete(key);
  return { url: u.href, title: document.title, video, videoSelection: { candidateCount: videoElements.length, ambiguous: videoElements.length > 1 || videoIdentityConflict, identityConflict: videoIdentityConflict }, pageKind: livePreview ? 'live-preview' : video?.id ? 'video' : location.pathname.startsWith('/search/') ? 'search' : 'unknown', livePreview, searchResults: searchResults.slice(0,20), visibleText: text,
    loginRequired, verificationRequired, privateChatVisible, documentReadyState: document.readyState };
}
