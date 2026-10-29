// Adapted DOM composer and receipt helpers from LockyBee/dsh-douyin-agent comment-browser.mjs.
// MIT; see LICENSE in this directory. No browser creation or account access.
const stop = signal => signal?.throwIfAborted();
const cleanDraft = value => String(value ?? '').replace(/[\u200B-\u200F\uFEFF]/g, '').trim();
const EDITORS = ['.comment-input-container [contenteditable="true"]', '.comment-input-container textarea', '.comment-input-inner-container [contenteditable="true"]', '.comment-input-inner-container textarea', '[data-e2e="comment-input"] [contenteditable="true"]', 'textarea[data-e2e="comment-input"]', '[contenteditable="true"][data-e2e="comment-input"]'].map(selector => `${selector}:not([data-e2e="im-dialog"] *):not([data-e2e="danmaku-container"] *)`).join(',');
const BUTTONS = '[data-e2e="comment-post"],[data-e2e="comment-submit"],[data-e2e="comment-publish"],button,[role="button"],a,div,span';
const LABELLED_BUTTONS = '[aria-label="发布"],[aria-label="发送"],[aria-label="发表评论"],[aria-label="发布评论"],[title="发布"],[title="发表评论"],[title="发布评论"]';
const NATIVE_PUBLISH = '.commentInput-right-ct .oWdMk9B9 span.wchsYBpK';
// 2026-10-24从专用浏览器只读核对；无标签图标仅接受这组结构与几何。
const NATIVE_PUBLISH_ICON = {
  viewBox: '0 0 36 36',
  paths: [
    { d: 'M17.5 30C23.851 30 29 24.851 29 18.5S23.851 7 17.5 7 6 12.149 6 18.5 11.149 30 17.5 30zm-5.16-13.883a1.16 1.16 0 0 0 1.64 1.64l2.395-2.394v8.838a1.16 1.16 0 0 0 2.321 0v-8.839l1.028 1.028 1.368 1.368a1.16 1.16 0 1 0 1.64-1.641l-4.219-4.22a1.382 1.382 0 0 0-1.954 0l-4.22 4.22h.001z', fill: '#fe2c55' },
    { d: 'M12.34 16.117a1.16 1.16 0 0 0 1.64 1.64l2.395-2.394v8.838a1.16 1.16 0 0 0 2.321 0v-8.839l1.028 1.028 1.368 1.368a1.16 1.16 0 1 0 1.64-1.641l-4.219-4.22a1.382 1.382 0 0 0-1.954 0l-4.22 4.22h.001z', fill: '#fff' },
  ],
};
const PUBLISH_LABEL = /^(?:发布|发送|发表评论|发布评论)$/;

async function composerScopeValid(root, expectedVideoId) {
  return root.evaluate((element, expected) => {
    if (element.closest('[data-e2e="im-dialog"],[data-e2e="danmaku-container"],[data-e2e="comment-item"],[data-e2e="comment-list"]')) return false;
    const owner = element.closest('[data-aweme-id],[data-video-id]');
    const videoId = owner?.getAttribute('data-aweme-id') || owner?.getAttribute('data-video-id');
    return !videoId || videoId === expected;
  }, expectedVideoId);
}

export async function activateCommentEditor(page, expectedVideoId, check) {
  let editors = page.locator(EDITORS).filter({ visible: true });
  if (await editors.count()) return editors;
  const lazy = page.locator('.comment-input-inner-container:not([data-e2e="im-dialog"] *):not([data-e2e="danmaku-container"] *)').filter({ visible: true });
  if (await lazy.count() !== 1 || !await composerScopeValid(lazy, expectedVideoId)) return editors;
  const canActivate = await lazy.evaluate(element => {
    if (element.querySelector('[data-e2e="comment-reply-target"],.PPsJmqBy,img,video,audio,a,button,[role="button"]')) return false;
    for (const input of element.querySelectorAll('input,textarea,[contenteditable]')) {
      if ((input.value ?? input.innerText ?? input.textContent ?? '').replace(/[\u200B-\u200F\uFEFF]/g, '').trim()) return false;
    }
    const text = (element.innerText || '').trim();
    const placeholder = element.getAttribute('data-placeholder') || element.getAttribute('aria-placeholder') || '';
    const currentPlaceholder = element.querySelector('span.RXtfhfhu');
    const knownEmpty = currentPlaceholder?.textContent.trim() === '留下你的精彩评论吧' && text === '留下你的精彩评论吧' && Boolean(element.closest('.comment-input-container'));
    return !text || Boolean(placeholder && text === placeholder.trim()) || knownEmpty;
  });
  if (!canActivate) return editors;
  await check();
  const bounds = await lazy.boundingBox();
  if (!bounds || bounds.width < 8 || bounds.height < 8) return editors;
  // 只激活已核对的空评论输入区域，不点击发布或使用回车提交。
  await lazy.click({ position: { x: Math.min(16, bounds.width / 2), y: bounds.height / 2 }, timeout: 1000 });
  await page.waitForFunction(() => [...document.querySelectorAll('.comment-input-inner-container [contenteditable="true"],.comment-input-inner-container textarea')].some(element => {
    const r = element.getBoundingClientRect(); return r.width > 0 && r.height > 0;
  }), undefined, { timeout: 1500 }).catch(() => {});
  await check();
  editors = page.locator(EDITORS).filter({ visible: true });
  return editors;
}

export async function findCommentSubmit(editor, expectedVideoId) {
  let root = editor.locator('xpath=ancestor-or-self::*[contains(concat(" ",normalize-space(@class)," ")," comment-input-container ") or contains(concat(" ",normalize-space(@class)," ")," comment-input-inner-container ") or @data-e2e="comment-input"][1]');
  let lastRoot = root;
  for (let level = 0; level < 5 && await root.count() === 1; level++) {
    if (!await composerScopeValid(root, expectedVideoId)) return { invalid: true };
    const bounded = await root.evaluate(element => !element.matches('body,html') && !element.querySelector('[data-e2e="comment-item"],[data-e2e="comment-list"],[data-e2e="im-dialog"],[data-e2e="danmaku-container"]') && element.querySelectorAll('[contenteditable="true"],textarea').length + Number(element.matches('[contenteditable="true"],textarea')) === 1);
    if (!bounded) break;
    lastRoot = root;
    const controls = root.locator(BUTTONS).filter({ hasText: PUBLISH_LABEL }).or(root.locator(LABELLED_BUTTONS)).or(root.locator(NATIVE_PUBLISH)).filter({ visible: true });
    const indices = await controls.evaluateAll((elements, icon) => {
      const nativeIcon = element => {
        if (!element.matches('span.wchsYBpK') || element.textContent.trim()) return false;
        const toolbar = element.closest('.commentInput-right-ct'), box = element.closest('.oWdMk9B9'), composer = element.closest('.comment-input-inner-container');
        if (!toolbar || !box || !composer || !composer.closest('.comment-input-container') || toolbar.closest('.comment-input-inner-container') !== composer || box.closest('.commentInput-right-ct') !== toolbar || box.lastElementChild !== element || box.querySelectorAll('span.wchsYBpK').length !== 1 || !box.querySelector('input[type="file"]')) return false;
        const svgs = element.querySelectorAll('svg');
        if (svgs.length !== 1 || svgs[0].getAttribute('viewBox')?.trim().replace(/\s+/g, ' ') !== icon.viewBox) return false;
        const paths = [...svgs[0].querySelectorAll('path')], compact = value => (value || '').replace(/[\s,]+/g, '');
        return paths.length === icon.paths.length && paths.every((node, index) => compact(node.getAttribute('d')) === compact(icon.paths[index].d) && node.getAttribute('fill')?.trim().toLowerCase() === icon.paths[index].fill);
      };
      const official = element => nativeIcon(element) || element.matches('[data-e2e="comment-post"],[data-e2e="comment-submit"],[data-e2e="comment-publish"],button,[role="button"],[aria-label="发布"],[aria-label="发送"],[aria-label="发表评论"],[aria-label="发布评论"],[title="发布"],[title="发表评论"],[title="发布评论"]');
      const eligible = elements.filter(element => {
        if (element.closest('[data-e2e="im-dialog"],[data-e2e="danmaku-container"],[data-e2e="comment-item"],[data-e2e="comment-list"],[contenteditable="true"]') || element.querySelector('[contenteditable],textarea,input')) return false;
        return official(element) || /^(?:发布|发送|发表评论|发布评论)$/.test((element.innerText || element.textContent || '').trim()) && (getComputedStyle(element).cursor === 'pointer' || element.tabIndex >= 0 || typeof element.onclick === 'function' || /(?:comment[-_].*(?:post|submit|publish|send)|(?:post|submit|publish|send)[-_].*comment)/i.test(element.className || ''));
      });
      // 嵌套的文字和按钮是同一控件；两个并列发布控件仍视为歧义。
      const independent = eligible.filter(element => official(element) || !eligible.some(other => other !== element && official(other) && other.contains(element)));
      const selected = independent.filter(element => !independent.some(other => other !== element && element.contains(other) && (official(other) || !official(element))));
      return selected.map(element => ({ index: elements.indexOf(element), nativeIcon: nativeIcon(element) }));
    }, NATIVE_PUBLISH_ICON);
    if (indices.length > 1) return { ambiguous: true, root };
    if (indices.length === 1) return { root, send: controls.nth(indices[0].index), nativeIcon: indices[0].nativeIcon };
    root = root.locator('xpath=..');
  }
  return { missing: true, root: lastRoot };
}

export async function inspectCommentComposer(page, expectedVideoId) {
  const editors = page.locator(EDITORS).filter({ visible: true }), editorCount = await editors.count();
  if (editorCount !== 1) return { editorCount, inputReady: false, submitFound: false, needsUserAction: true };
  const selected = await findCommentSubmit(editors, expectedVideoId);
  if (!selected.send) return { editorCount, inputReady: !selected.invalid, submitFound: false, ambiguous: Boolean(selected.ambiguous), needsUserAction: true };
  const enabled = await selected.send.evaluate((element, nativeIcon) => !element.closest(':disabled,[aria-disabled="true"],[data-disabled="true"],.disabled') && (!nativeIcon || element.classList.contains('jfGCpJo0')), selected.nativeIcon);
  return { editorCount, inputReady: true, submitFound: true, nativeIcon: Boolean(selected.nativeIcon), enabled };
}

// 只读公开评论的编号、正文和自身发出状态；不采集其他评论者的账号、昵称或头像。
export function readCommentDom(expectedVideoId) {
  const visible = element => { const r = element.getBoundingClientRect(), s = getComputedStyle(element); return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth && s.display !== 'none' && s.visibility !== 'hidden'; };
  const profileId = anchor => {
    try { const url = new URL(anchor.getAttribute('href'), 'https://www.douyin.com/'); return url.origin === 'https://www.douyin.com' && !url.username && !url.password ? /^\/user\/([A-Za-z0-9_-]{5,300})\/?$/.exec(url.pathname)?.[1] || null : null; } catch { return null; }
  };
  const privateArea = '[data-e2e="im-dialog"],[data-e2e="conversation-item"]';
  const ownNavigation = [...document.querySelectorAll('.tab-user_self a[href]')].filter(anchor => visible(anchor) && !anchor.closest(`${privateArea},[data-e2e="comment-item"]`) && anchor.textContent.trim() === '我的');
  const ownProfile = ownNavigation.length === 1 ? profileId(ownNavigation[0]) : null;
  const pageVideoIds = new Set();
  try { const url = new URL(location.href), id = /^\/video\/(\d+)\/?$/.exec(url.pathname)?.[1] || url.searchParams.get('modal_id') || url.searchParams.get('aweme_id'); if (/^\d{1,64}$/.test(id || '')) pageVideoIds.add(id); } catch {}
  for (const player of document.querySelectorAll('[data-e2e="player-container"]')) {
    if (!visible(player) || player.closest(privateArea) || ![...player.querySelectorAll('video')].some(visible)) continue;
    const id = /(?:^|\s)video_(\d+)(?:\s|$)/.exec(player.className || '')?.[1]; if (id) pageVideoIds.add(id);
  }
  if (pageVideoIds.size && (pageVideoIds.size !== 1 || !pageVideoIds.has(expectedVideoId))) return { rows: [] };
  const result = [];
  for (const row of document.querySelectorAll('[data-e2e="comment-item"]')) {
    if (!visible(row) || row.closest(privateArea) || row.parentElement?.closest('[data-e2e="comment-item"]')) continue;
    const owned = selector => [...row.querySelectorAll(selector)].filter(element => element.closest('[data-e2e="comment-item"]') === row);
    const ancestorVideoId = row.closest('[data-aweme-id],[data-video-id]')?.getAttribute('data-aweme-id') || row.closest('[data-video-id]')?.getAttribute('data-video-id');
    if (ancestorVideoId && ancestorVideoId !== expectedVideoId) continue;
    const videoBound = ancestorVideoId === expectedVideoId || pageVideoIds.size === 1 && pageVideoIds.has(expectedVideoId);
    const ids = [row.getAttribute('data-comment-id'), /^comment_(\d+)$/.exec(row.id)?.[1], ...owned('[id^="tooltip_"]').map(element => /^tooltip_(\d+)$/.exec(element.id)?.[1])].filter(Boolean);
    const rawId = new Set(ids).size === 1 ? ids[0] : null;
    const content = owned('[data-e2e="comment-content"],.comment-item-content,.FduGc_lz > .Sh1Da424');
    const textNode = content.length === 1 ? content[0] : null;
    const text = (textNode?.innerText || textNode?.textContent || '').trim();
    const authors = owned('.comment-item-avatar a[href]').filter(anchor => visible(anchor) && !anchor.closest('[data-e2e="comment-content"],.comment-item-content,.FduGc_lz > .Sh1Da424'));
    const authorProfile = authors.length === 1 ? profileId(authors[0]) : null;
    const selfMarker = row.getAttribute('data-is-self') === 'true' || row.getAttribute('data-author-is-self') === 'true' || owned('[data-e2e="comment-self"]').length === 1;
    const mine = videoBound && (selfMarker || Boolean(ownProfile && authorProfile && ownProfile === authorProfile));
    const status = row.getAttribute('data-send-status') || row.getAttribute('data-comment-status') || '';
    const labels = owned('[data-e2e="comment-status"],.comment-item-tag-text').map(element => element.textContent.trim());
    const pending = /^(?:sending|pending|reviewing)$/.test(status) || owned('[data-e2e="comment-pending"],[aria-busy="true"],[class*="sending" i]').length > 0 || labels.some(value => /^(?:待审核|审核中|评论审核中|发送中|发布中)$/.test(value));
    const failed = /^(?:failed|rejected|error)$/.test(status) || owned('[data-e2e="comment-failed"],[class*="sendFail"]').length > 0 || labels.some(value => /^(?:审核未通过|发布失败|评论未发布|发送失败)$/.test(value));
    const sent = /^(?:sent|published|success)$/.test(status) || mine && /^\d{1,64}$/.test(rawId || '') && !pending && !failed;
    result.push({ commentId: /^\d{1,64}$/.test(rawId || '') ? rawId : null, videoId: ancestorVideoId || expectedVideoId, text, mine, sent, pending, failed });
  }
  return { rows: result };
}
