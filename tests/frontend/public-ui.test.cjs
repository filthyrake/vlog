const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = 'web/public/';
const source = p => readFileSync(root + p, 'utf8');
const wait = ms => new Promise(r => setTimeout(r, ms));

async function mount(page, prepare, setup = () => {}) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', e => errors.push(e.message));
  virtualConsole.on('warn', (...args) => errors.push(args.join(' ')));
  const dom = new JSDOM(source(page + '.html'), {
    url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole,
  });
  const w = dom.window;
  w.eval(source('static/js/utils.js'));
  setup(w);
  w.eval(source('static/js/pages/' + (page === 'index' ? 'home' : page) + '.js'));
  w.eval(source('static/vendor/alpine.csp.min.js'));
  const register = w.Alpine.data.bind(w.Alpine);
  w.Alpine.data = (name, factory) => register(name, () => {
    const data = factory();
    // Keep actual UI methods and markup, replacing network/player initialization only.
    data.init = function () { return prepare(this); };
    return data;
  });
  await wait(30);
  return { dom, w, errors };
}

test('home search updates its model and sends the entered query to the API', async () => {
  const calls = [];
  const { dom, w, errors } = await mount('index', data => { data.loading = false; }, w => {
    w.VLogUtils.fetchWithTimeout = async url => {
      calls.push(url);
      return { ok: true, json: async () => ({ videos: [] }) };
    };
  });
  try {
    const input = w.document.querySelector('#search-input');
    input.value = 'Porsche & 911';
    input.dispatchEvent(new w.Event('input', { bubbles: true }));
    await wait(400);
    assert(calls.some(url => url.includes('search=Porsche%20%26%20911')));
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('watch comments and rating controls render with the CSP build', async () => {
  const ratings = [];
  const { dom, w, errors } = await mount('watch', data => {
    data.loading = false;
    data.isLoggedIn = true;
    data.currentUser = { id: 'viewer' };
    data.socialStatus = { comments_enabled: true, ratings_enabled: true, ratings_type: 'stars' };
    data.ratingAggregates = { average: 4.5, count: 2 };
    data.comments = [{ id: 1, depth: 2, content: '<script>plain text</script>',
      user: { id: 'viewer', display_name: 'Viewer' }, created_at: new Date().toISOString() }];
    data.submitRating = value => ratings.push(value);
  });
  try {
    const button = w.document.querySelector('[aria-label="Rate 4 stars"]');
    assert(button);
    assert.equal(button.disabled, false);
    button.click();
    await wait(10);
    assert.deepEqual(ratings, [4]);
    assert.equal(w.document.querySelector('.comment').style.marginLeft, '24px');
    assert(w.document.querySelector('.comment').textContent.includes('<script>plain text</script>'));
    assert.equal(w.document.querySelector('.comment script'), null);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('DASH fallback waits for detach and starts HLS only once', async () => {
  const dom = new JSDOM('<video></video>', { url: 'http://localhost/', runScripts: 'outside-only' });
  const w = dom.window;
  w.eval(source('static/js/pages/watch.js'));
  const data = w.VLogWatchPage.watchPage();
  let finishAttach, finishDestroy;
  const attached = new Promise(resolve => { finishAttach = resolve; });
  const destroyed = new Promise(resolve => { finishDestroy = resolve; });
  let loadCalls = 0, destroyCalls = 0, hlsCalls = 0;
  const listeners = {};
  w.shaka = { polyfill: { installAll() {} }, Player: class {
    static isBrowserSupported() { return true; }
    attach() { return attached; }
    configure() {}
    addEventListener(name, fn) { listeners[name] = fn; }
    load() {
      loadCalls++;
      listeners.error({ detail: new Error('DASH unavailable') });
      return Promise.reject(new Error('DASH unavailable'));
    }
    destroy() { destroyCalls++; return destroyed; }
  } };
  data.initHlsPlayer = () => { hlsCalls++; };
  try {
    data.initShakaPlayer(w.document.querySelector('video'), '/manifest.mpd', '/master.m3u8');
    assert.equal(loadCalls, 0);
    finishAttach();
    await wait(0);
    assert.equal(loadCalls, 1);
    assert.equal(destroyCalls, 1);
    assert.equal(hlsCalls, 0);
    finishDestroy();
    await wait(0);
    assert.equal(hlsCalls, 1);
    assert.equal(data.shakaPlayer, null);
  } finally { dom.window.close(); }
});
